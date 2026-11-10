import subprocess
import urllib.request

import pytest

from vsniff import playlist


MASTER_WITH_SUBS = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio0",NAME="yue",LANGUAGE="yue",URI="index-f2-a1.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs0",NAME="粤語",LANGUAGE="yue",AUTOSELECT=YES,DEFAULT=YES,URI="index-f1.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1016128,RESOLUTION=1280x720,AUDIO="audio0",SUBTITLES="subs0"
index-f2-v1.m3u8
"""


MASTER_NO_SUBS = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1759100,RESOLUTION=1280x720,AUDIO="audio0"
index-f2-v1.m3u8
"""


def test_parse_subtitle_playlist_resolves_against_the_master_url():
    got = playlist.parse_subtitle_playlist(
        MASTER_WITH_SUBS, "https://cdn.example.com/show/01/master.m3u8")
    assert got == "https://cdn.example.com/show/01/index-f1.m3u8"


def test_parse_subtitle_playlist_returns_none_without_a_subtitle_track():
    assert playlist.parse_subtitle_playlist(
        MASTER_NO_SUBS, "https://cdn.example.com/show/01/master.m3u8") is None


def test_parse_subtitle_playlist_ignores_the_audio_rendition_uri():
    # the AUDIO line also carries a URI="..."; it must not be mistaken for subs
    audio_only = MASTER_WITH_SUBS.replace("TYPE=SUBTITLES", "TYPE=CLOSED-CAPTIONS")
    assert playlist.parse_subtitle_playlist(audio_only, "https://x/master.m3u8") is None


def test_serve_playlist_serves_the_text_over_http():
    url = playlist.serve_playlist("#EXTM3U\n#EXT-X-ENDLIST\n")
    assert url.startswith("http://127.0.0.1:")
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.read().decode() == "#EXTM3U\n#EXT-X-ENDLIST\n"


MEDIA_NO_RESOLUTION = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:6.0,
seg0.ts
#EXTINF:4.0,
seg1.ts
#EXT-X-ENDLIST
"""


def fake_ffprobe(monkeypatch, stdout="", exc=None):
    seen = {}

    def run(cmd, capture_output=False, text=False, timeout=None):
        seen["cmd"] = cmd
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(playlist.subprocess, "run", run)
    return seen


def test_probe_resolution_reads_the_height_ffprobe_reports(monkeypatch):
    seen = fake_ffprobe(monkeypatch, stdout="1920,1080\n")
    assert playlist.probe_resolution("https://cdn/i.m3u8", "https://dramasq.io/x") == "1080p"
    assert "https://cdn/i.m3u8" in seen["cmd"]


def test_probe_resolution_encodes_a_non_ascii_referer(monkeypatch):
    seen = fake_ffprobe(monkeypatch, stdout="1280,720\n")
    playlist.probe_resolution("https://cdn/i.m3u8", "https://www.hkanime.com/play/信蜂/304x0")
    headers = seen["cmd"][seen["cmd"].index("-headers") + 1]
    assert "%E4%BF%A1%E8%9C%82" in headers


def test_probe_resolution_is_unknown_when_ffprobe_finds_nothing(monkeypatch):
    fake_ffprobe(monkeypatch, stdout="")
    assert playlist.probe_resolution("https://cdn/i.m3u8", "https://r/") == "unknown"


def test_probe_resolution_is_unknown_without_ffprobe(monkeypatch):
    fake_ffprobe(monkeypatch, exc=FileNotFoundError("ffprobe"))
    assert playlist.probe_resolution("https://cdn/i.m3u8", "https://r/") == "unknown"


def test_analyze_playlist_falls_back_to_ffprobe_without_a_resolution_line(monkeypatch):
    monkeypatch.setattr(playlist, "fetch_text", lambda ctx, url, ref: MEDIA_NO_RESOLUTION)
    monkeypatch.setattr(playlist, "probe_resolution", lambda url, ref: "1080p")
    resolution, duration, subs, pl = playlist.analyze_playlist(
        None, "https://cdn/index.m3u8", "https://r/", keep_ads=True)
    assert (resolution, duration, subs, pl) == ("1080p", 10.0, None, "https://cdn/index.m3u8")


def test_analyze_playlist_skips_ffprobe_when_the_playlist_states_it(monkeypatch):
    monkeypatch.setattr(playlist, "fetch_text", lambda ctx, url, ref: MASTER_NO_SUBS)
    monkeypatch.setattr(playlist, "probe_resolution",
                        lambda url, ref: (_ for _ in ()).throw(AssertionError("probed")))
    assert playlist.analyze_playlist(None, "https://cdn/master.m3u8", "https://r/",
                                     keep_ads=True)[0] == "720p"


@pytest.mark.parametrize("width, height, tag", [
    (1920, 1080, "1080p"),
    (1920, 800, "1080p"),   # widescreen drama (When I Meet the Moon): not 720p
    (1920, 816, "1080p"),
    (1280, 536, "720p"),
    (1440, 1080, "1080p"),  # 4:3 keeps its height
    (3840, 1600, "2160p"),
    (854, 480, "480p"),
])
def test_frame_tag_judges_widescreen_by_width(width, height, tag):
    assert playlist.frame_tag(width, height) == tag


def test_probe_resolution_reports_a_widescreen_stream_as_1080p(monkeypatch):
    fake_ffprobe(monkeypatch, stdout="1920,800\n")
    assert playlist.probe_resolution("https://cdn/i.m3u8", "https://r/") == "1080p"


def test_parse_resolution_judges_playlist_frames_by_width():
    text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1920x800\nv.m3u8\n"
    assert playlist.parse_resolution(text) == "1080p"
