"""Reading a stream's playlist: resolution, length, subtitles, and serving an
ad-stripped copy to ffmpeg."""
import itertools
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

from .ads import EXTINF_RX, strip_ads
from .common import UA, encode_url, fetch_text


# ---- resolution + duration from the playlist ------------------------------ #
def _res_tag(height):
    for std in (2160, 1440, 1080, 720, 480, 360):
        if height >= std:
            return f"{std}p"
    return f"{height}p"


def frame_tag(width, height):
    """Resolution tag for a frame, judged like a 16:9 one of the same width.

    Widescreen video is wider than 16:9, so its height alone undersells it:
    a 1920x800 drama is 1080p (as Sonarr calls it), not 720p. A frame counts
    as tall as a 16:9 frame of its width would be, or its real height if
    that's more (4:3 1440x1080 stays 1080p).
    """
    return _res_tag(max(height, round(width * 9 / 16)))


RESOLUTION_RX = re.compile(r"RESOLUTION=(\d+)x(\d+)")
STD_HEIGHT_RX = re.compile(r"(?<!\d)(2160|1440|1080|720|480|360)(?!\d)")


def parse_resolution(playlist_text, url=""):
    frames = [(int(w), int(h)) for w, h in RESOLUTION_RX.findall(playlist_text)]
    if frames:
        return frame_tag(*max(frames, key=lambda f: f[0] * f[1]))
    marks = [int(x) for x in STD_HEIGHT_RX.findall(url)]
    if marks:
        return _res_tag(max(marks))
    return "unknown"


def probe_resolution(m3u8_url, referer):
    """Resolution tag read from the stream itself with ffprobe, or "unknown".

    For a single-quality playlist that never states its RESOLUTION (common on
    dramasq). ffprobe only opens the first segment, so this takes a second or
    two, not a download.
    """
    cmd = ["ffprobe", "-v", "error", "-headers",
           f"Referer: {encode_url(referer)}\r\nUser-Agent: {UA}\r\n",
           "-select_streams", "v:0", "-show_entries", "stream=width,height",
           "-of", "csv=p=0", m3u8_url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    frames = [(int(w), int(h)) for w, h in re.findall(r"(\d+),(\d+)", out)]
    return frame_tag(*max(frames, key=lambda f: f[0] * f[1])) if frames else "unknown"


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
    if resolution == "unknown" and text:
        resolution = probe_resolution(m3u8_url, referer)
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
