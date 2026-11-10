#!/usr/bin/env python3
"""vsniff — a general streaming-video sniffer + downloader.

Drives a headless browser to *discover* the live video stream a page plays
(the stream URLs are usually hotlink-protected and tokened, so they must be
captured from a real browser session), then hands the captured playlist +
referer to ffmpeg to download and mux into a Sonarr/Jellyfin-named .mp4.

The core sniff-and-capture is site-agnostic. Thin per-site *adapters* add
niceties: pulling the episode number out of the URL, and (for chinaq and
dramasq) trying each playback source until one plays. Sites without an adapter still work —
you just provide --episode yourself.

    vsniff "https://www.hkanime.com/play/<name>/120x0" \
        --series "Fullmetal Alchemist Brotherhood" --season 1
    vsniff "https://chinaq.net/video/68261-7.html#sid=9" \
        --series "Blossoms of Power" --source ZYun
    vsniff "https://dramasq.io/vodplay/202614591/ep1.html" \
        --series "Fall in Love with the Moon"
"""
import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class VsniffError(Exception):
    """User-facing failure."""


# --------------------------------------------------------------------------- #
# Generic browser sniffing (site-agnostic)
# --------------------------------------------------------------------------- #
def sniff_once(page, nav_url, timeout_ms=12000):
    """Load nav_url, trigger playback, capture the first working .m3u8.

    Returns (m3u8_url, referer) or None.
    """
    hits = []

    def on_resp(resp):
        if ".m3u8" in resp.url and resp.status < 400:
            hits.append((resp.url, resp.request.headers.get("referer", "")))

    page.context.on("response", on_resp)
    try:
        page.goto("about:blank")
        page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
        for sel in ("video", ".dplayer", "#player", ".vjs-big-play-button", ".play"):
            try:
                page.click(sel, timeout=1000)
            except Exception:
                pass
        page.wait_for_timeout(timeout_ms)
    finally:
        page.context.remove_listener("response", on_resp)

    if not hits:
        return None
    # prefer a master playlist over a bitrate-specific variant
    masters = [h for h in hits
               if "master.m3u8" in h[0] or h[0].rstrip("/").endswith("/index.m3u8")]
    return (masters or hits)[0]


def origin_of(url):
    pr = urlparse(url)
    return f"{pr.scheme}://{pr.netloc}"


def encode_url(url):
    """Percent-encode a URL path so ffmpeg/urllib accept non-ASCII paths."""
    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/,.-_~%"),
                       s.query, s.fragment))


def fetch_text(ctx, url, referer):
    """Fetch a URL's text through the browser session (shares TLS + referer)."""
    try:
        return ctx.request.get(
            url, headers={"Referer": referer, "User-Agent": UA}).text()
    except Exception:
        return ""


# ---- resolution + duration from the playlist ------------------------------ #
def _res_tag(height):
    for std in (2160, 1440, 1080, 720, 480, 360):
        if height >= std:
            return f"{std}p"
    return f"{height}p"


RESOLUTION_RX = re.compile(r"RESOLUTION=(\d+)x(\d+)")
STD_HEIGHT_RX = re.compile(r"(?<!\d)(2160|1440|1080|720|480|360)(?!\d)")
EXTINF_RX = re.compile(r"#EXTINF:([\d.]+)")


def parse_resolution(playlist_text, url=""):
    heights = [int(h) for _w, h in RESOLUTION_RX.findall(playlist_text)]
    if heights:
        return _res_tag(max(heights))
    marks = [int(x) for x in STD_HEIGHT_RX.findall(url)]
    if marks:
        return _res_tag(max(marks))
    return "unknown"


SUB_URI_RX = re.compile(r'URI="([^"]+)"')


def parse_subtitle_playlist(playlist_text, m3u8_url):
    """URL of a master playlist's subtitle rendition, or None if it has none.

    Subtitles ride along as a separate WebVTT rendition, named by an
    `#EXT-X-MEDIA:TYPE=SUBTITLES` line, which `ffmpeg -c copy` drops on the
    floor. Reading that rendition on its own pulls only the .vtt segments —
    and without the duplicate cues you get when reading it through the master.
    """
    for line in playlist_text.splitlines():
        if line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES"):
            m = SUB_URI_RX.search(line)
            if m:
                return urljoin(m3u8_url, m.group(1))
    return None


def analyze_playlist(ctx, m3u8_url, referer, keep_ads=False):
    """Return (resolution, duration, subtitle_url, playlist_for_ffmpeg).

    playlist_for_ffmpeg is m3u8_url itself, or a localhost URL serving a copy
    with spliced-in ad blocks removed (see find_ad_blocks).
    """
    text = fetch_text(ctx, m3u8_url, referer)
    resolution = parse_resolution(text, m3u8_url)
    subtitles = parse_subtitle_playlist(text, m3u8_url)

    media_text, media_url = text, m3u8_url
    variants = 0
    if "EXT-X-STREAM-INF" in text:  # master -> resolve first variant for durations
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                variants += 1
                if variants == 1:
                    media_url = urljoin(m3u8_url, line)
                    media_text = fetch_text(ctx, media_url, referer)

    secs = [float(s) for s in EXTINF_RX.findall(media_text)]
    duration = sum(secs) if secs else None

    playlist = m3u8_url
    # Only rewrite a playlist we fully understand: a media playlist, or a master
    # with a single variant and no separate audio track. A master with several
    # variants or audio renditions is left for ffmpeg to pick from as before.
    rewritable = variants <= 1 and "TYPE=AUDIO" not in text
    if not keep_ads and rewritable and secs:
        cleaned = strip_ads(media_text, media_url)
        if cleaned:
            clean_text, removed_s, removed_n = cleaned
            playlist = serve_playlist(clean_text)
            duration -= removed_s
            print(f"  ads: removed {removed_n} spliced-in block(s), "
                  f"{removed_s:.0f}s total")
    return resolution, duration, subtitles, playlist


# ---- spliced-in ad removal ------------------------------------------------ #
# Some sources splice short ad clips into the episode's HLS playlist, each
# bracketed by #EXT-X-DISCONTINUITY. Two tells, both seen on chinaq sources:
#   * the clip's segments live in a different directory from the episode's
#     (e.g. /20261102/8EzXUOn8/10134kb/hls/ inside a /20261019/7cbiw8z4/... show)
#   * the clip shares the episode's directory but was encoded at another frame
#     rate (30fps ads in a 25fps episode), which shows in the EXTINF durations:
#     a segment always holds a whole number of frames.
# Discontinuities alone mean nothing: some sources split every scene cut into
# its own block, so a block is only dropped on one of the tells above, and only
# when it is short.
AD_MAX_BLOCK_S = 60      # longest run of segments we would ever call an ad
AD_MAX_SHARE = 0.2       # never strip more than this fraction of the episode
FRAME_RATES = (24000 / 1001, 24, 25, 30000 / 1001, 30, 50, 60000 / 1001, 60)
HEADER_TAGS = ("#EXTM3U", "#EXT-X-VERSION", "#EXT-X-TARGETDURATION",
               "#EXT-X-MEDIA-SEQUENCE", "#EXT-X-PLAYLIST-TYPE",
               "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-ALLOW-CACHE")
STATEFUL_TAGS = ("#EXT-X-KEY", "#EXT-X-MAP")  # apply until replaced
TAG_URI_RX = re.compile(r'URI="([^"]+)"')


def parse_media_playlist(text, url):
    """Split a media playlist into (header_lines, blocks).

    Each block is the run of segments between discontinuities; each segment is
    a dict with its absolute uri, duration, own tag lines, and the KEY/MAP tags
    in force for it (so dropping a block never strands the next one's key).
    """
    header, blocks, pending = [], [[]], []
    state = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "#EXT-X-ENDLIST":
            continue
        if line.startswith(HEADER_TAGS):
            header.append(line)
        elif line == "#EXT-X-DISCONTINUITY":
            if blocks[-1]:
                blocks.append([])
        elif line.startswith(STATEFUL_TAGS):
            tag = line.split(":", 1)[0]
            state[tag] = TAG_URI_RX.sub(
                lambda m: f'URI="{urljoin(url, m.group(1))}"', line)
        elif line.startswith("#"):
            pending.append(line)
        else:
            m = EXTINF_RX.match(next(
                (t for t in pending if t.startswith("#EXTINF")), ""))
            blocks[-1].append({
                "uri": urljoin(url, line),
                "duration": float(m.group(1)) if m else 0.0,
                "tags": pending,
                "state": dict(state),
            })
            pending = []
    return header, [b for b in blocks if b]


def _fits_frame_rate(duration, fps):
    frames = duration * fps
    return abs(frames - round(frames)) <= 0.003 * fps  # within 3ms of a frame


def _seg_dir(uri):
    return urlsplit(uri).path.rsplit("/", 1)[0]


def find_ad_blocks(blocks):
    """Indices of the blocks that look like spliced-in ads."""
    if len(blocks) < 2:
        return set()
    segs = [s for b in blocks for s in b]
    total = sum(s["duration"] for s in segs)
    if total <= 0:
        return set()

    by_dir = {}
    for s in segs:
        by_dir[_seg_dir(s["uri"])] = by_dir.get(_seg_dir(s["uri"]), 0) + s["duration"]
    main_dir = max(by_dir, key=by_dir.get)

    # The episode's frame rate: the lowest one that explains >=90% of the
    # airtime. Grids nest (every 25fps duration also fits 50fps, and a few 30fps
    # ad durations happen to as well), so the highest-scoring rate can be a
    # coarser grid that hides the ad; the lowest well-fitting rate is the real one.
    share = {fps: sum(s["duration"] for s in segs
                      if _fits_frame_rate(s["duration"], fps)) / total
             for fps in FRAME_RATES}
    fitting = [f for f in FRAME_RATES if share[f] >= 0.9]
    main_fps = min(fitting) if fitting else None

    ads = set()
    for i, block in enumerate(blocks):
        length = sum(s["duration"] for s in block)
        if length > AD_MAX_BLOCK_S:
            continue
        foreign = all(_seg_dir(s["uri"]) != main_dir for s in block)
        off_grid = main_fps is not None and sum(
            not _fits_frame_rate(s["duration"], main_fps) for s in block
        ) * 2 > len(block)
        if foreign or off_grid:
            ads.add(i)

    removed = sum(s["duration"] for i in ads for s in blocks[i])
    if len(ads) == len(blocks) or removed > total * AD_MAX_SHARE:
        return set()  # implausible: rather keep an ad than lose the episode
    return ads


def strip_ads(text, url):
    """(clean_playlist_text, removed_seconds, removed_blocks), or None if the
    playlist has no ad blocks. Segment and key URIs in the result are absolute,
    so it can be served from anywhere."""
    header, blocks = parse_media_playlist(text, url)
    ads = find_ad_blocks(blocks)
    if not ads:
        return None
    out = list(header) or ["#EXTM3U"]
    emitted = {}
    first = True
    for i, block in enumerate(blocks):
        if i in ads:
            continue
        if not first:
            out.append("#EXT-X-DISCONTINUITY")
        first = False
        for s in block:
            for tag, line in s["state"].items():
                if emitted.get(tag) != line:
                    out.append(line)
                    emitted[tag] = line
            out.extend(s["tags"])
            out.append(s["uri"])
    out.append("#EXT-X-ENDLIST")
    removed = sum(s["duration"] for i in ads for s in blocks[i])
    return "\n".join(out) + "\n", removed, len(ads)


# ffmpeg only forwards -headers (our Referer) to segment requests when the
# playlist itself arrives over HTTP — a local file input rejects the option — so
# cleaned playlists are served from a throwaway localhost server.
_served = {}
_server = None
_serial = itertools.count(1)


class _PlaylistHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _served.get(self.path)
        if body is None:
            self.send_error(404)
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve_playlist(text):
    """Serve a playlist on localhost for the life of the process; return its URL."""
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), _PlaylistHandler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    path = f"/clean-{next(_serial)}.m3u8"
    _served[path] = text
    return f"http://127.0.0.1:{_server.server_address[1]}{path}"


# --------------------------------------------------------------------------- #
# Site adapters
# --------------------------------------------------------------------------- #
class GenericSite:
    """Fallback: sniff whatever the page plays; user supplies episode."""
    name = "generic"
    supports_batch = False

    def matches(self, host):
        return True

    def episode(self, url):
        return None  # unknown -> require --episode

    def is_movie(self, url):
        return False  # unknown -> only --movie turns movie naming on

    def movie_url(self, url):
        """The page to fetch a movie from; the URL itself unless the site bundles."""
        return url

    def discover(self, page, ctx, url, user_source):
        """Return (source_label, m3u8_url, referer)."""
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the page (nothing played)")
        return "default", found[0], found[1]


class ChinaqSite(GenericSite):
    name = "chinaq"
    supports_batch = True
    SOURCE_RX = re.compile(
        r'#sid=(\d+)"[^>]*onclick="changeSid\(\'\d+\'\)"><strong>[^<]*</strong><small>([^<]*)</small>'
    )

    def matches(self, host):
        return host.endswith("chinaq.net")

    def episode(self, url):
        m = re.search(r"/video/\d+-(\d+)\.html", url)
        return int(m.group(1)) if m else None

    def series_id(self, url):
        m = re.search(r"/video/(\d+)-\d+\.html", url) \
            or re.search(r"/voddetail/(\d+)\.html", url)
        return int(m.group(1)) if m else None

    def parse_episodes(self, html, series_id):
        """Parse voddetail HTML -> {sid: {episode numbers}} for this series."""
        rx = re.compile(rf"/video/{series_id}-(\d+)\.html#sid=(\d+)")
        by_sid = {}
        for ep, sid in rx.findall(html):
            by_sid.setdefault(int(sid), set()).add(int(ep))
        return by_sid

    def _sources(self, html):
        tabs = [(int(s), n.strip()) for s, n in self.SOURCE_RX.findall(html)]
        tabs.sort()
        if not tabs:
            raise VsniffError("no playback sources found on the chinaq page")
        return tabs

    def _order(self, sources, user_source, hash_sid):
        sids = [s for s, _ in sources]
        if user_source is not None:
            return [match_source(sources, user_source)]
        if hash_sid in sids:
            return [hash_sid] + [s for s in sids if s != hash_sid]
        return sids

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series."""
        series_id = self.series_id(url)
        if series_id is None:
            raise VsniffError("could not parse the chinaq series id from the URL")
        origin = origin_of(url)
        hm = re.search(r"#sid=(\d+)", url)
        preferred_sid = int(hm.group(1)) if hm else None

        page.goto(f"{origin}/voddetail/{series_id}.html",
                  wait_until="domcontentloaded", timeout=30000)
        by_sid = self.parse_episodes(page.content(), series_id)
        items = []
        for ep in available_episodes(by_sid):
            ep_url = f"{origin}/video/{series_id}-{ep}.html"
            if preferred_sid is not None and ep in by_sid.get(preferred_sid, set()):
                ep_url += f"#sid={preferred_sid}"
            items.append((ep, ep_url))
        return items

    def discover(self, page, ctx, url, user_source):
        base = url.split("#")[0]
        hm = re.search(r"#sid=(\d+)", url)
        hash_sid = int(hm.group(1)) if hm else None

        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        sources = self._sources(page.content())
        names = dict(sources)
        for sid in self._order(sources, user_source, hash_sid):
            label = names.get(sid, "?")
            print(f"  trying source sid={sid} ({label}) ...", flush=True)
            found = sniff_once(page, f"{base}#sid={sid}")
            if found:
                print(f"  -> live stream on {label}")
                return label, found[0], found[1]
        raise VsniffError("no working source found (all chinaq sources failed)")


HK_EP_RX = re.compile(r"^\s*EP\s*#?\s*0*(\d+)", re.I)


def hk_episode_numbers(labels):
    """Episode number for each hkanime playlist label, in site order.

    Labels are usually "EP01 <title>", but the site also carries movies with no
    number at all, merged double episodes ("EP01-02 ..."), and series that open
    part-way through a long run (One Piece starts at EP517). Trust the labels
    only when every one parses into a strictly ascending run — otherwise they
    can repeat, and two episodes would fight over one filename — and fall back
    to the 1-based position in the list.
    """
    nums = []
    for label in labels:
        m = HK_EP_RX.match(label)
        nums.append(int(m.group(1)) if m else None)
    if None not in nums and all(a < b for a, b in zip(nums, nums[1:])):
        return nums
    return list(range(1, len(labels) + 1))


@lru_cache(maxsize=8)
def hk_api(origin, series_id):
    """The parsed play-api JSON for an hkanime entry.

    Cached: a batch run asks for the same entry once per episode, and the movie
    check and the episode list read the same response.
    """
    req = urllib.request.Request(
        f"{origin}/play-api/{series_id}",
        headers={"User-Agent": UA, "Referer": f"{origin}/"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def hk_playurl(origin, series_id):
    """[(label, m3u8_url)] for an hkanime entry, in site order."""
    block = (hk_api(origin, series_id).get("playurl") or [{}])[0]
    return [(label, encode_url(m3u8))
            for label, m3u8 in block.items() if label.strip() and m3u8]


class HKAnimeSite(GenericSite):
    """hkanime is a single-page app: /play/<slug>/<seriesId>x<epIndex>.

    Its /play-api/<seriesId> JSON is both the episode catalog and the source of
    a playable master.m3u8 for every episode, so one request covers the whole
    series and no per-episode sniffing is needed. Sniffing stays as a fallback
    in case that endpoint changes.
    """
    name = "hkanime"
    supports_batch = True
    URL_RX = re.compile(r"^(?P<prefix>.*/play/[^/]+)/(?P<sid>\d+)(?:x(?P<idx>\d+))?$")

    def matches(self, host):
        return host.endswith("hkanime.com")

    def _parts(self, url):
        """(url_prefix, series_id, ep_index); ids are None when unparseable."""
        m = self.URL_RX.match(url.split("#")[0].split("?")[0].rstrip("/"))
        if not m:
            return None, None, None
        idx = m.group("idx")
        return (m.group("prefix"), int(m.group("sid")),
                int(idx) if idx is not None else None)

    def series_id(self, url):
        return self._parts(url)[1]

    def _numbers(self, url, series_id):
        return hk_episode_numbers(
            [label for label, _ in hk_playurl(origin_of(url), series_id)])

    def episode(self, url):
        _prefix, series_id, idx = self._parts(url)
        if series_id is None or idx is None:
            return None
        try:
            nums = self._numbers(url, series_id)
        except Exception:
            return idx + 1  # API unreachable: assume a plain 1..N series
        return nums[idx] if idx < len(nums) else None

    def is_movie(self, url):
        """True for entries the site files as a movie (remarks "劇場版")."""
        _prefix, series_id, _idx = self._parts(url)
        if series_id is None:
            return False
        try:
            info = (hk_api(origin_of(url), series_id).get("info") or [{}])[0]
        except Exception:
            return False  # can't tell -> keep series naming; --movie still works
        return "劇場版" in (info.get("remarks") or "")

    def movie_url(self, url):
        """Pick the one video a movie entry holds.

        Nearly every movie entry holds exactly one video, so a bare entry URL
        (/play/<slug>/357) resolves to it. A few bundle several separate films
        (all 13 Naruto movies in one entry), and each needs its own title and
        year, so those must be picked by index rather than guessed at.
        """
        prefix, series_id, idx = self._parts(url)
        if series_id is None or idx is not None:
            return url
        episodes = hk_playurl(origin_of(url), series_id)
        if len(episodes) == 1:
            return f"{prefix}/{series_id}x0"
        listing = "\n".join(f"  {prefix}/{series_id}x{i}  {label.strip()}"
                            for i, (label, _) in enumerate(episodes))
        raise VsniffError(
            f"this entry bundles {len(episodes)} separate movies; download each "
            f"by its own URL with its own --title and --year:\n{listing}")

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series."""
        prefix, series_id, _idx = self._parts(url)
        if series_id is None:
            raise VsniffError("could not parse the hkanime series id from the URL")
        nums = self._numbers(url, series_id)
        if not nums:
            raise VsniffError("hkanime lists no episodes for this series")
        return [(ep, f"{prefix}/{series_id}x{i}") for i, ep in enumerate(nums)]

    def discover(self, page, ctx, url, user_source):
        if user_source is not None:
            print("  note: --source is ignored for hkanime (source is in the URL)")
        _prefix, series_id, idx = self._parts(url)
        if series_id is not None and idx is not None:
            try:
                episodes = hk_playurl(origin_of(url), series_id)
            except Exception as e:
                print(f"  note: play-api unavailable ({e}); sniffing the page")
                episodes = []
            if idx < len(episodes):
                return "hkanime", episodes[idx][1], url
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the hkanime page")
        return "hkanime", found[0], found[1]


DQ_SOURCE_RX = re.compile(
    r'v_data="([^"]*)"[^>]*>\s*<strong>[^<]*</strong>\s*<small>([^<]*)</small>')


def dq_api(origin, series_id, slug):
    """The parsed /drq/<seriesId>/<slug> JSON: every source for one episode.

    The player page fetches this itself, and it carries both the stream URLs
    (`video_plays`) and the rendered source tabs (`html_content`) with names.
    """
    req = urllib.request.Request(
        f"{origin}/drq/{series_id}/{slug}",
        headers={"User-Agent": UA,
                 "Referer": f"{origin}/vodplay/{series_id}/{slug}.html"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def dq_sources(data):
    """[(source_number, name, m3u8_url)] from a /drq response, in site order.

    Numbered by position like the page's 片源1, 片源2 … tabs. Names repeat (two
    different CDNs are both "JYun"), so the number is the unambiguous handle.
    """
    tabs = DQ_SOURCE_RX.findall(data.get("html_content") or "")
    if tabs:
        pairs = [(url, name.strip()) for url, name in tabs]
    else:  # tab markup changed: fall back to the bare list, unnamed
        pairs = [(p.get("play_data") or p.get("v_data") or "", p.get("src_site", "?"))
                 for p in data.get("video_plays") or []]
    return [(i, name, url) for i, (url, name) in enumerate(pairs, 1) if url]


def stream_reachable(url, referer):
    """True if url serves an HLS playlist. Several dramasq CDNs answer 403/404
    (region-locked or expired), so a source is checked before it is picked."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(64).lstrip(b"\xef\xbb\xbf").startswith(b"#EXTM3U")
    except Exception:
        return False


class DramasqSite(GenericSite):
    """dramasq: /vodplay/<seriesId>/ep<N>.html episodes, /detail/<seriesId>.html
    series pages.

    Like chinaq it offers several sources per episode, but its /drq API hands
    over every source's m3u8 directly — and the CDNs don't check tokens or
    referers — so sources are tried with plain HTTP instead of a browser.
    Sniffing the page stays as a fallback in case that API changes.
    """
    name = "dramasq"
    supports_batch = True
    URL_RX = re.compile(r"/(?:vodplay|detail)/(?P<sid>\d+)(?:/(?P<slug>[^/.#?]+))?")
    EP_SLUG_RX = re.compile(r"^ep(\d+)$")
    MAX_UNLISTED = 10  # newest episodes the API serves before the listing shows them

    def matches(self, host):
        return re.search(r"(^|\.)dramasq\.[a-z]+$", host) is not None

    def _parts(self, url):
        """(series_id, slug); slug is e.g. "ep7", "sp", "20261110", or None."""
        m = self.URL_RX.search(urlsplit(url).path)
        if not m:
            return None, None
        return int(m.group("sid")), m.group("slug")

    def series_id(self, url):
        return self._parts(url)[0]

    def episode(self, url):
        """N for an epN page; None for specials (sp), a 正片 (zheng_pian) feature
        or a daily show's date-keyed page — those need --episode or --movie."""
        _sid, slug = self._parts(url)
        m = self.EP_SLUG_RX.match(slug or "")
        return int(m.group(1)) if m else None

    def parse_episodes(self, html, series_id):
        """Episode numbers a detail page links to (epN pages only)."""
        rx = re.compile(rf"/vodplay/{series_id}/ep(\d+)\.html")
        return sorted({int(n) for n in rx.findall(html)})

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series.

        The detail page lags the API: an episode can be playable for a while
        before it is linked, so episodes past the last listed one are probed
        until the API has nothing.
        """
        series_id = self.series_id(url)
        if series_id is None:
            raise VsniffError("could not parse the dramasq series id from the URL")
        origin = origin_of(url)
        req = urllib.request.Request(f"{origin}/detail/{series_id}.html",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            eps = self.parse_episodes(resp.read().decode("utf-8", "replace"), series_id)
        if not eps:
            raise VsniffError(
                "dramasq lists no numbered episodes for this series (daily shows "
                "and specials have none); download pages one at a time with --episode")
        for ep in range(eps[-1] + 1, eps[-1] + 1 + self.MAX_UNLISTED):
            try:
                if not dq_sources(dq_api(origin, series_id, f"ep{ep}")):
                    break
            except Exception:
                break
            eps.append(ep)
        return [(ep, f"{origin}/vodplay/{series_id}/ep{ep}.html") for ep in eps]

    def discover(self, page, ctx, url, user_source):
        series_id, slug = self._parts(url)
        sources = []
        if series_id is not None and slug:
            try:
                sources = dq_sources(dq_api(origin_of(url), series_id, slug))
            except Exception as e:
                print(f"  note: dramasq API unavailable ({e}); sniffing the page")
        if sources:
            by_num = {n: (name, m3u8) for n, name, m3u8 in sources}
            order = [n for n, _, _ in sources]
            if user_source is not None:
                order = [match_source([(n, name) for n, name, _ in sources], user_source)]
            referer = url.split("#")[0]
            for n in order:
                label, m3u8 = by_num[n]
                print(f"  trying source {n} ({label}) ...", flush=True)
                if stream_reachable(m3u8, referer):
                    print(f"  -> live stream on {label}")
                    return label, m3u8, referer
            raise VsniffError("no working source found (all dramasq sources failed)")
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the dramasq page")
        return "dramasq", found[0], found[1]


ADAPTERS = [ChinaqSite(), HKAnimeSite(), DramasqSite(), GenericSite()]


def pick_adapter(url):
    host = urlparse(url).netloc.lower()
    for a in ADAPTERS:
        if a.matches(host):
            return a
    return ADAPTERS[-1]


def match_source(sources, token):
    """Resolve a --source token (number or case-insensitive name) to a sid."""
    token = str(token).strip()
    if token.isdigit():
        sid = int(token)
        if sid in {s for s, _ in sources}:
            return sid
        raise VsniffError(f"source number {sid} not available "
                          f"(have: {', '.join(str(s) for s, _ in sources)})")
    for sid, name in sources:
        if name.lower() == token.lower():
            return sid
    names = ", ".join(name for _, name in sources)
    raise VsniffError(f"source name {token!r} not found (have: {names})")


def filter_from(episodes, start):
    """Keep only episodes numbered >= start (inclusive). start=None keeps all."""
    if start is None:
        return list(episodes)
    return [e for e in episodes if e >= start]


def available_episodes(by_sid):
    """Sorted union of every episode number across all sources."""
    eps = set()
    for episodes in by_sid.values():
        eps |= episodes
    return sorted(eps)


# --------------------------------------------------------------------------- #
# Discovery orchestration (one browser session)
# --------------------------------------------------------------------------- #
def discover_with_session(adapter, page, ctx, url, user_source, keep_ads=False):
    """Discover one stream using an already-open browser session.

    Returns (source_label, playlist, referer, resolution, duration, subtitles);
    playlist is the stream URL, or a local ad-stripped copy of it.
    """
    label, m3u8, referer = adapter.discover(page, ctx, url, user_source)
    resolution, duration, subtitles, playlist = analyze_playlist(
        ctx, m3u8, referer, keep_ads)
    return label, playlist, referer, resolution, duration, subtitles


def discover_stream(url, user_source, show=False, keep_ads=False):
    """Return (adapter,) + everything discover_with_session found."""
    adapter = pick_adapter(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            found = discover_with_session(
                adapter, page, ctx, url, user_source, keep_ads)
            return (adapter,) + found
        finally:
            browser.close()


def download_all(url, args):
    """Download every episode the site lists for a series into args.out.

    Returns 0 on full success, 1 if any episode failed.
    """
    adapter = pick_adapter(url)
    print(f"site: {adapter.name}")
    if not adapter.supports_batch:
        raise VsniffError("--all is only supported for chinaq.net, hkanime.com and dramasq.io")

    out_dir = expand_out_dir(args.out)
    os.makedirs(out_dir, exist_ok=True)

    downloaded, failed, present, subs_added = [], [], [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            by_ep = dict(adapter.catalog(page, ctx, url))
            available = sorted(by_ep)
            if not available:
                raise VsniffError("no episodes found for this series")

            wanted = filter_from(available, args.start)
            have = existing_episodes(out_dir, args.series, args.season)
            present = [e for e in wanted if e in have]
            missing = [e for e in wanted if e not in have]
            backfill = [e for e in present if not os.path.exists(
                subtitle_sidecar(os.path.join(out_dir, have[e]), args.sub_lang))]
            note = f", {len(backfill)} without subtitles" if backfill else ""
            print(f"catalog: {len(available)} episodes; "
                  f"{len(missing)} to download, {len(present)} already present"
                  f"{note}")

            for ep in missing:
                try:
                    ep_url = by_ep[ep]
                    print(f"episode {ep}: discovering...")
                    (label, m3u8, referer, resolution, duration,
                     subtitles) = discover_with_session(
                        adapter, page, ctx, ep_url, args.source, args.keep_ads)
                    fname = build_filename(
                        args.series, args.season, ep, args.quality, resolution)
                    out_path = os.path.join(out_dir, fname)
                    print(f"  -> {out_path}  (source={label}, {resolution})")
                    download_stream(m3u8, referer, out_path, duration)
                    if subtitles:
                        save_subtitles(subtitles, referer, out_path, args.sub_lang)
                    downloaded.append(ep)
                except Exception as e:
                    # Continue-on-failure: a single episode failing (no working
                    # source, ffmpeg error, or a browser/network error such as a
                    # Playwright timeout) must not abort the rest of the batch.
                    print(f"  episode {ep} failed: {e}", file=sys.stderr)
                    failed.append(ep)

            for ep in backfill:
                print(f"episode {ep}: already downloaded, fetching subtitles...")
                try:
                    (_lbl, _m3u8, referer, _res, _dur,
                     subtitles) = discover_with_session(
                        adapter, page, ctx, by_ep[ep], args.source,
                        keep_ads=True)  # video is already on disk
                    if not subtitles:
                        # Whether a series is packaged with a subtitle track is a
                        # property of the series, not of one episode (checked
                        # across 543 episodes of 5 series), so one miss means
                        # there is nothing to find in any of them. Bail out rather
                        # than re-discover hundreds of episodes to learn the same.
                        print("  no subtitle track on this series; "
                              "skipping the remaining episodes")
                        break
                    if save_subtitles(subtitles, referer,
                                      os.path.join(out_dir, have[ep]),
                                      args.sub_lang):
                        subs_added += 1
                except Exception as e:
                    # A missing sidecar is cosmetic next to an intact video, so a
                    # failure here is reported but does not fail the run.
                    print(f"  episode {ep} subtitles failed: {e}", file=sys.stderr)
        finally:
            browser.close()

    parts = [f"{len(downloaded)} downloaded", f"{len(present)} already present"]
    if subs_added:
        parts.append(f"{subs_added} subtitles backfilled")
    parts.append(f"{len(failed)} failed")
    summary = "done: " + ", ".join(parts)
    if failed:
        summary += f" (episodes: {', '.join(str(e) for e in failed)})"
    print(summary)
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# Download with progress bar (ffmpeg)
# --------------------------------------------------------------------------- #
def _headers(referer):
    return f"Referer: {referer}\r\nUser-Agent: {UA}\r\n"


def _fmt_time(secs):
    secs = int(secs)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _render_bar(done_s, total_s, width=32):
    if total_s and total_s > 0:
        frac = min(done_s / total_s, 1.0)
        filled = int(frac * width)
        bar = "#" * filled + "-" * (width - filled)
        line = f"\r  [{bar}] {frac*100:5.1f}%  {_fmt_time(done_s)}/{_fmt_time(total_s)}"
    else:
        line = f"\r  downloaded {_fmt_time(done_s)} (length unknown)"
    sys.stdout.write(line)
    sys.stdout.flush()


def subtitle_sidecar(video_path, lang):
    """Sidecar path for a video: `<video basename>.<lang>.srt`.

    This is the naming Jellyfin scans for, so the track shows up beside the
    video without touching the mp4 itself.
    """
    return f"{os.path.splitext(video_path)[0]}.{lang}.srt"


def save_subtitles(sub_url, referer, video_path, lang):
    """Write an HLS subtitle rendition beside the video as a .srt sidecar.

    Best effort: a broken or empty subtitle track is not worth failing a
    finished video download over, so this reports and returns None instead.
    """
    out_path = subtitle_sidecar(video_path, lang)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-headers", _headers(referer), "-i", sub_url,
         "-c:s", "srt", "-loglevel", "error", out_path],
        capture_output=True, text=True)
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if proc.returncode != 0 or size == 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        reason = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "empty"
        print(f"  subtitles skipped: {reason}")
        return None
    print(f"  subtitles -> {os.path.basename(out_path)}  ({size/1024:.0f} KB)")
    return out_path


def download_stream(m3u8, referer, out_path, duration=None):
    """Download the full stream to out_path with ffmpeg, showing a progress bar."""
    cmd = ["ffmpeg", "-y", "-headers", _headers(referer), "-i", m3u8,
           "-c", "copy", "-movflags", "+faststart",
           "-loglevel", "error", "-progress", "pipe:1", "-nostats", out_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    done_s = 0.0
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            val = line.split("=", 1)[1]
            if val.isdigit():
                done_s = int(val) / 1_000_000
                _render_bar(done_s, duration)
        elif line == "progress=end":
            _render_bar(duration or done_s, duration)
    proc.wait()
    sys.stdout.write("\n")
    stderr = proc.stderr.read()
    if proc.returncode != 0:
        raise VsniffError("ffmpeg failed:\n" + stderr[-1500:])
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size < 50_000:
        raise VsniffError(f"download produced a suspiciously small file ({size} bytes)")
    return size


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def existing_episodes(out_dir, series, season):
    """{episode: filename} already in out_dir as {series} - Sxx Exx - ... .mp4.

    The filename is kept, not just the number, because a sidecar has to be
    named after the file that is actually on disk — an episode grabbed earlier
    may carry a different resolution tag than the one we would build today.
    """
    if not os.path.isdir(out_dir):
        return {}
    prefix = f"{series} - S{season:02d}E"
    rx = re.compile(re.escape(prefix) + r"(\d+) - .*\.mp4$")
    found = {}
    for name in sorted(os.listdir(out_dir)):
        m = rx.match(name)
        if m:
            found[int(m.group(1))] = name
    return found


def build_filename(series, season, episode, quality, resolution):
    return f"{series} - S{season:02d}E{episode:02d} - {quality} - {resolution}.mp4"


def movie_name(title, year):
    """`Title (Year)` — Jellyfin's movie folder and file stem; year optional."""
    return f"{title} ({year})" if year else title


def build_movie_filename(title, year, quality, resolution):
    return f"{movie_name(title, year)} - {quality} - {resolution}.mp4"


def existing_movie(folder, title, year):
    """Filename of a copy of this movie already in its folder, or None.

    Matched by prefix, not rebuilt, because an earlier copy may carry a
    different resolution tag than the stream serves today.
    """
    if not os.path.isdir(folder):
        return None
    prefix = f"{movie_name(title, year)} - "
    for name in sorted(os.listdir(folder)):
        if name.startswith(prefix) and name.endswith(".mp4"):
            return name
    return None


def download_movie(adapter, args):
    """Download one movie into `<out>/Title (Year)/Title (Year) - Q - RES.mp4`.

    That per-movie folder is what a Jellyfin Movies library expects, so a movie
    lands beside the series rather than being numbered into one. With --all it
    syncs like a series: an existing copy is kept and only a missing subtitle
    sidecar is fetched.
    """
    print(f"site: {adapter.name} (movie)")
    if args.all and not args.out:
        raise VsniffError("--all requires --out (the library directory to sync into)")
    for flag, value in (("--episode", args.episode), ("--from", args.start)):
        if value is not None:
            print(f"note: {flag} is ignored for a movie")

    url = adapter.movie_url(args.url)
    folder = os.path.join(expand_out_dir(args.out or "."),
                          movie_name(args.series, args.year))
    have = existing_movie(folder, args.series, args.year) if args.all else None
    if have and os.path.exists(subtitle_sidecar(os.path.join(folder, have), args.sub_lang)):
        print(f"already present: {have}")
        return 0

    print("discovering stream...")
    (_adapter, label, m3u8, referer, resolution, duration,
     subtitles) = discover_stream(
        url, args.source, show=args.show,
        keep_ads=args.keep_ads or bool(have))  # `have`: video already on disk
    if have:
        print(f"already present: {have}")
        if subtitles:
            save_subtitles(subtitles, referer, os.path.join(folder, have), args.sub_lang)
        return 0

    dur_txt = _fmt_time(duration) if duration else "unknown"
    print(f"stream: source={label}  resolution={resolution}  length={dur_txt}  "
          f"subtitles={'yes' if subtitles else 'none'}")
    os.makedirs(folder, exist_ok=True)
    out_path = os.path.join(folder, build_movie_filename(
        args.series, args.year, args.quality, resolution))
    print(f"downloading -> {out_path}")
    size = download_stream(m3u8, referer, out_path, duration)
    if subtitles:
        save_subtitles(subtitles, referer, out_path, args.sub_lang)
    print(f"done: {out_path}  ({size/1024/1024:.1f} MB)")
    return 0


def expand_out_dir(path):
    """Resolve an output dir for macOS/Linux: expand ~ / ~user and $VARS."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vsniff",
        description="Sniff and download a streaming video as a Sonarr/Jellyfin-named MP4.")
    ap.add_argument("url", help="episode/play page URL")
    ap.add_argument("--series", "--title", dest="series", required=True,
                    help="English series (or movie) title")
    ap.add_argument("--season", type=int, default=1, help="season number (default 1)")
    ap.add_argument("--episode", type=int,
                    help="episode number (auto-detected for supported sites)")
    ap.add_argument("--quality", default="WEBDL", help="quality tag (default WEBDL)")
    ap.add_argument("--source", help="chinaq/dramasq: force a source by number (9) or name (ZYun)")
    ap.add_argument("--from", dest="start", type=int,
                    help="batch (--all) only: fetch episodes numbered N and above")
    ap.add_argument("--out", default=None,
                    help="output directory (default . for single mode; "
                         "REQUIRED with --all); supports ~ and $VARS, e.g. --out ~/Movies")
    ap.add_argument("--all", action="store_true",
                    help="download every available episode "
                         "(chinaq/hkanime/dramasq only; requires --out)")
    ap.add_argument("--sub-lang", dest="sub_lang", default="zh",
                    help="language code for the .srt sidecar (default zh)")
    ap.add_argument("--movie", action="store_true",
                    help="name it as a movie, 'Title (Year)/Title (Year) - ...mp4' "
                         "(automatic for hkanime 劇場版 entries)")
    ap.add_argument("--year", type=int,
                    help="movie release year, for the folder/filename")
    ap.add_argument("--keep-ads", dest="keep_ads", action="store_true",
                    help="don't strip ad clips spliced into the stream")
    ap.add_argument("--show", action="store_true", help="run the browser headful (debug)")
    args = ap.parse_args(argv)

    try:
        adapter = pick_adapter(args.url)
        if args.movie or adapter.is_movie(args.url):
            return download_movie(adapter, args)
        if args.year is not None:
            print("note: --year only applies to movies")

        if args.all:
            if not args.out:
                raise VsniffError(
                    "--all requires --out (the library directory to sync into)")
            if args.episode is not None:
                print("note: --episode is ignored with --all "
                      "(episodes come from the catalog)")
            return download_all(args.url, args)

        if args.start is not None:
            print("note: --from is ignored without --all")
        print(f"site: {adapter.name}")

        episode = args.episode if args.episode is not None else adapter.episode(args.url)
        if episode is None:
            raise VsniffError(
                f"could not auto-detect the episode number for a '{adapter.name}' URL; "
                f"pass it explicitly with --episode N")

        print("discovering stream...")
        (adapter, label, m3u8, referer, resolution, duration,
         subtitles) = discover_stream(args.url, args.source, show=args.show,
                                      keep_ads=args.keep_ads)
        dur_txt = _fmt_time(duration) if duration else "unknown"
        subs_txt = "yes" if subtitles else "none"
        print(f"stream: source={label}  resolution={resolution}  "
              f"length={dur_txt}  subtitles={subs_txt}")

        out_dir = expand_out_dir(args.out or ".")
        os.makedirs(out_dir, exist_ok=True)
        fname = build_filename(args.series, args.season, episode, args.quality, resolution)
        out_path = os.path.join(out_dir, fname)

        print(f"downloading -> {out_path}")
        size = download_stream(m3u8, referer, out_path, duration)
        if subtitles:
            save_subtitles(subtitles, referer, out_path, args.sub_lang)
        print(f"done: {out_path}  ({size/1024/1024:.1f} MB)")
        return 0
    except VsniffError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
