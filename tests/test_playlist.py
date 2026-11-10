import urllib.request

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
