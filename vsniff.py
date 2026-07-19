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
import os
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse

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


def analyze_playlist(ctx, m3u8_url, referer):
    """Return (resolution_tag, duration_seconds_or_None) for a playlist URL."""
    text = fetch_text(ctx, m3u8_url, referer)
    resolution = parse_resolution(text, m3u8_url)

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
    return resolution, duration


# --------------------------------------------------------------------------- #
# Site adapters
# --------------------------------------------------------------------------- #
class GenericSite:
    """Fallback: sniff whatever the page plays; user supplies episode."""
    name = "generic"

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
    SOURCE_RX = re.compile(
        r'#sid=(\d+)"[^>]*onclick="changeSid\(\'\d+\'\)"><strong>[^<]*</strong><small>([^<]*)</small>'
    )

    def matches(self, host):
        return host.endswith("chinaq.net")

    def episode(self, url):
        m = re.search(r"/video/\d+-(\d+)\.html", url)
        return int(m.group(1)) if m else None

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


class HKAnimeSite(GenericSite):
    name = "hkanime"

    def matches(self, host):
        return host.endswith("hkanime.com")

    def episode(self, url):
        # /play/<slug>/<sourceId>x<index>  ; index is 0-based -> episode = index + 1
        m = re.search(r"/play/[^/]+/\d+x(\d+)", url)
        return int(m.group(1)) + 1 if m else None

    # source is encoded in the URL (the N in NxM); generic sniff is enough
    def discover(self, page, ctx, url, user_source):
        if user_source is not None:
            print("  note: --source is ignored for hkanime (source is in the URL)")
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


# --------------------------------------------------------------------------- #
# Discovery orchestration (one browser session)
# --------------------------------------------------------------------------- #
def discover_stream(url, user_source, show=False):
    """Return (adapter, source_label, m3u8, referer, resolution, duration)."""
    adapter = pick_adapter(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            label, m3u8, referer = adapter.discover(page, ctx, url, user_source)
            resolution, duration = analyze_playlist(ctx, m3u8, referer)
            return adapter, label, m3u8, referer, resolution, duration
        finally:
            browser.close()


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
    ap.add_argument("--out", default=".",
                    help="output directory (default .); supports ~ and $VARS, "
                         "e.g. --out ~/Movies")
    ap.add_argument("--show", action="store_true", help="run the browser headful (debug)")
    args = ap.parse_args(argv)

    try:
        adapter = pick_adapter(args.url)
        print(f"site: {adapter.name}")

        episode = args.episode if args.episode is not None else adapter.episode(args.url)
        if episode is None:
            raise VsniffError(
                f"could not auto-detect the episode number for a '{adapter.name}' URL; "
                f"pass it explicitly with --episode N")

        print("discovering stream...")
        adapter, label, m3u8, referer, resolution, duration = discover_stream(
            args.url, args.source, show=args.show)
        dur_txt = _fmt_time(duration) if duration else "unknown"
        print(f"stream: source={label}  resolution={resolution}  length={dur_txt}")

        out_dir = expand_out_dir(args.out)
        os.makedirs(out_dir, exist_ok=True)
        fname = build_filename(args.series, args.season, episode, args.quality, resolution)
        out_path = os.path.join(out_dir, fname)

        print(f"downloading -> {out_path}")
        size = download_stream(m3u8, referer, out_path, duration)
        print(f"done: {out_path}  ({size/1024/1024:.1f} MB)")
        return 0
    except VsniffError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
