#!/usr/bin/env python3
"""vsniff — a general streaming-video sniffer + downloader.

Drives a headless browser to *discover* the live video stream a page plays
(the stream URLs are usually hotlink-protected and tokened, so they must be
captured from a real browser session), then hands the captured playlist +
referer to ffmpeg to download and mux into a Sonarr/Jellyfin-named .mp4.

The core sniff-and-capture is site-agnostic. Thin per-site *adapters* add
niceties: pulling the episode number out of the URL, and (for chinaq) trying
each playback source until one plays. Sites without an adapter still work —
you just provide --episode yourself.

    vsniff "https://www.hkanime.com/play/<name>/120x0" \
        --series "Fullmetal Alchemist Brotherhood" --season 1
    vsniff "https://chinaq.net/video/68261-7.html#sid=9" \
        --series "Blossoms of Power" --source ZYun
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from functools import lru_cache
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


def analyze_playlist(ctx, m3u8_url, referer):
    """Return (resolution_tag, duration_seconds_or_None, subtitle_url_or_None)."""
    text = fetch_text(ctx, m3u8_url, referer)
    resolution = parse_resolution(text, m3u8_url)
    subtitles = parse_subtitle_playlist(text, m3u8_url)

    media_text, media_url = text, m3u8_url
    if "EXT-X-STREAM-INF" in text:  # master -> resolve first variant for durations
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                media_url = urljoin(m3u8_url, line)
                media_text = fetch_text(ctx, media_url, referer)
                break

    secs = [float(s) for s in EXTINF_RX.findall(media_text)]
    duration = sum(secs) if secs else None
    return resolution, duration, subtitles


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
def hk_playurl(origin, series_id):
    """[(label, m3u8_url)] for an hkanime series, from its play-api JSON.

    Cached: a batch run asks for the same series once per episode.
    """
    req = urllib.request.Request(
        f"{origin}/play-api/{series_id}",
        headers={"User-Agent": UA, "Referer": f"{origin}/"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    block = (data.get("playurl") or [{}])[0]
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


ADAPTERS = [ChinaqSite(), HKAnimeSite(), GenericSite()]


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
def discover_with_session(adapter, page, ctx, url, user_source):
    """Discover one stream using an already-open browser session.

    Returns (source_label, m3u8_url, referer, resolution, duration, subtitles).
    """
    label, m3u8, referer = adapter.discover(page, ctx, url, user_source)
    resolution, duration, subtitles = analyze_playlist(ctx, m3u8, referer)
    return label, m3u8, referer, resolution, duration, subtitles


def discover_stream(url, user_source, show=False):
    """Return (adapter,) + everything discover_with_session found."""
    adapter = pick_adapter(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            found = discover_with_session(adapter, page, ctx, url, user_source)
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
        raise VsniffError("--all is only supported for chinaq.net and hkanime.com")

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
                        adapter, page, ctx, ep_url, args.source)
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
                        adapter, page, ctx, by_ep[ep], args.source)
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


def expand_out_dir(path):
    """Resolve an output dir for macOS/Linux: expand ~ / ~user and $VARS."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vsniff",
        description="Sniff and download a streaming video as a Sonarr/Jellyfin-named MP4.")
    ap.add_argument("url", help="episode/play page URL")
    ap.add_argument("--series", required=True, help="English series title")
    ap.add_argument("--season", type=int, default=1, help="season number (default 1)")
    ap.add_argument("--episode", type=int,
                    help="episode number (auto-detected for supported sites)")
    ap.add_argument("--quality", default="WEBDL", help="quality tag (default WEBDL)")
    ap.add_argument("--source", help="chinaq: force a source by number (9) or name (ZYun)")
    ap.add_argument("--from", dest="start", type=int,
                    help="batch (--all) only: fetch episodes numbered N and above")
    ap.add_argument("--out", default=None,
                    help="output directory (default . for single mode; "
                         "REQUIRED with --all); supports ~ and $VARS, e.g. --out ~/Movies")
    ap.add_argument("--all", action="store_true",
                    help="download every available episode "
                         "(chinaq/hkanime only; requires --out)")
    ap.add_argument("--sub-lang", dest="sub_lang", default="zh",
                    help="language code for the .srt sidecar (default zh)")
    ap.add_argument("--show", action="store_true", help="run the browser headful (debug)")
    args = ap.parse_args(argv)

    try:
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
        adapter = pick_adapter(args.url)
        print(f"site: {adapter.name}")

        episode = args.episode if args.episode is not None else adapter.episode(args.url)
        if episode is None:
            raise VsniffError(
                f"could not auto-detect the episode number for a '{adapter.name}' URL; "
                f"pass it explicitly with --episode N")

        print("discovering stream...")
        (adapter, label, m3u8, referer, resolution, duration,
         subtitles) = discover_stream(args.url, args.source, show=args.show)
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
