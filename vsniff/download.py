"""Download with a progress bar, and subtitle sidecars (ffmpeg)."""
import os
import subprocess
import sys

from .common import UA, VsniffError


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
