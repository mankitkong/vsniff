import os
import subprocess
import urllib.request

import pytest

import vsniff


def test_module_imports():
    assert hasattr(vsniff, "ChinaqSite")
    assert hasattr(vsniff, "build_filename")


def test_filter_from_none_returns_all():
    assert vsniff.filter_from([1, 2, 3], None) == [1, 2, 3]


def test_filter_from_is_inclusive():
    assert vsniff.filter_from([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_filter_from_above_max_is_empty():
    assert vsniff.filter_from([1, 2, 3], 99) == []


def test_series_id_from_video_url():
    s = vsniff.ChinaqSite()
    assert s.series_id("https://chinaq.net/video/68261-20.html#sid=1") == 68261


def test_series_id_from_voddetail_url():
    s = vsniff.ChinaqSite()
    assert s.series_id("https://chinaq.net/voddetail/68261.html") == 68261


def test_series_id_unknown_url_is_none():
    s = vsniff.ChinaqSite()
    assert s.series_id("https://chinaq.net/label/new.html") is None


SAMPLE_VODDETAIL = """
<b>片源6 : DYun</b>
<a href="/video/68261-1.html#sid=6">第01集</a>
<a href="/video/68261-2.html#sid=6">第02集</a>
<a href="/video/68261-3.html#sid=6">第03集</a>
<b>片源3 : WYun</b>
<a href="/video/68261-1.html#sid=3">第01集</a>
<a href="/video/68261-2.html#sid=3">第02集</a>
"""


def test_parse_episodes_maps_sid_to_episode_set():
    s = vsniff.ChinaqSite()
    by_sid = s.parse_episodes(SAMPLE_VODDETAIL, 68261)
    assert by_sid == {6: {1, 2, 3}, 3: {1, 2}}


def test_parse_episodes_ignores_other_series_ids():
    s = vsniff.ChinaqSite()
    html = SAMPLE_VODDETAIL + '<a href="/video/99999-7.html#sid=6">x</a>'
    by_sid = s.parse_episodes(html, 68261)
    assert 7 not in by_sid.get(6, set())


def test_available_episodes_is_sorted_union():
    assert vsniff.available_episodes({6: {1, 2, 3}, 3: {1, 2}}) == [1, 2, 3]


def test_supports_batch_flags():
    assert vsniff.ChinaqSite().supports_batch is True
    assert vsniff.HKAnimeSite().supports_batch is True
    assert vsniff.GenericSite().supports_batch is False


# ---- hkanime ------------------------------------------------------------- #
HK_URL = "https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A/145x1"


def test_hk_parts_from_player_url():
    s = vsniff.HKAnimeSite()
    assert s._parts(HK_URL) == ("https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A",
                                145, 1)


def test_hk_parts_from_detail_url_has_no_index():
    s = vsniff.HKAnimeSite()
    prefix, sid, idx = s._parts("https://www.hkanime.com/play/x/145")
    assert (sid, idx) == (145, None)
    assert prefix.endswith("/play/x")


def test_hk_series_id_unknown_url_is_none():
    assert vsniff.HKAnimeSite().series_id("https://www.hkanime.com/play") is None


def test_hk_episode_numbers_uses_labels():
    labels = ["EP01 a", "EP02 b", "EP03 c"]
    assert vsniff.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_keeps_series_that_starts_late():
    # One Piece [ViuTV] opens at EP517, so position is not the episode number.
    labels = ["EP517 a", "EP518 b", "EP519 c"]
    assert vsniff.hk_episode_numbers(labels) == [517, 518, 519]


def test_hk_episode_numbers_keeps_merged_double_episodes():
    labels = ["EP01-02 a", "EP03-04 b", "EP05-06 c"]
    assert vsniff.hk_episode_numbers(labels) == [1, 3, 5]


def test_hk_episode_numbers_falls_back_when_labels_repeat():
    labels = ["EP01 a", "EP01 b", "EP02 c"]
    assert vsniff.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_falls_back_when_a_label_has_no_number():
    labels = ["EP01 a", "Movie b", "EP03 c"]
    assert vsniff.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_falls_back_when_labels_go_backwards():
    labels = ["EP03 a", "EP01 b"]
    assert vsniff.hk_episode_numbers(labels) == [1, 2]


def fake_playurl(monkeypatch, episodes):
    monkeypatch.setattr(vsniff, "hk_playurl", lambda origin, sid: episodes)


def test_hk_episode_reads_the_label_at_that_index(monkeypatch):
    fake_playurl(monkeypatch, [("EP517 a", "u1"), ("EP518 b", "u2")])
    assert vsniff.HKAnimeSite().episode(HK_URL) == 518


def test_hk_episode_falls_back_to_index_when_api_fails(monkeypatch):
    def boom(origin, sid):
        raise OSError("no network")
    monkeypatch.setattr(vsniff, "hk_playurl", boom)
    assert vsniff.HKAnimeSite().episode(HK_URL) == 2


def test_hk_episode_needs_an_index():
    assert vsniff.HKAnimeSite().episode("https://www.hkanime.com/play/x/145") is None


def test_hk_catalog_pairs_episodes_with_indexed_urls(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1"), ("EP02 b", "u2")])
    items = vsniff.HKAnimeSite().catalog(None, None, HK_URL)
    prefix = "https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A"
    assert items == [(1, f"{prefix}/145x0"), (2, f"{prefix}/145x1")]


def test_hk_catalog_rejects_a_series_with_no_episodes(monkeypatch):
    fake_playurl(monkeypatch, [])
    with pytest.raises(vsniff.VsniffError):
        vsniff.HKAnimeSite().catalog(None, None, HK_URL)


def test_hk_discover_uses_the_api_stream(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1"), ("EP02 b", "u2")])
    assert vsniff.HKAnimeSite().discover(None, None, HK_URL, None) == (
        "hkanime", "u2", HK_URL)


def test_hk_discover_sniffs_when_the_api_has_no_such_index(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1")])
    monkeypatch.setattr(vsniff, "sniff_once", lambda page, url: ("m", "r"))
    assert vsniff.HKAnimeSite().discover(None, None, HK_URL, None) == (
        "hkanime", "m", "r")


# ---- subtitles ----------------------------------------------------------- #
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
    got = vsniff.parse_subtitle_playlist(
        MASTER_WITH_SUBS, "https://cdn.example.com/show/01/master.m3u8")
    assert got == "https://cdn.example.com/show/01/index-f1.m3u8"


def test_parse_subtitle_playlist_returns_none_without_a_subtitle_track():
    assert vsniff.parse_subtitle_playlist(
        MASTER_NO_SUBS, "https://cdn.example.com/show/01/master.m3u8") is None


def test_parse_subtitle_playlist_ignores_the_audio_rendition_uri():
    # the AUDIO line also carries a URI="..."; it must not be mistaken for subs
    audio_only = MASTER_WITH_SUBS.replace("TYPE=SUBTITLES", "TYPE=CLOSED-CAPTIONS")
    assert vsniff.parse_subtitle_playlist(audio_only, "https://x/master.m3u8") is None


def fake_ffmpeg(monkeypatch, writes=None, returncode=0, stderr=""):
    """Stand in for the ffmpeg subprocess, optionally writing output bytes."""
    seen = {}

    def run(cmd, capture_output=False, text=False):
        seen["cmd"] = cmd
        if writes is not None:
            with open(cmd[-1], "w", encoding="utf-8") as fh:
                fh.write(writes)
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)

    monkeypatch.setattr(vsniff.subprocess, "run", run)
    return seen


def test_save_subtitles_names_the_sidecar_for_jellyfin(monkeypatch, tmp_path):
    seen = fake_ffmpeg(monkeypatch, writes="1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    out = vsniff.save_subtitles("https://x/index-f1.m3u8", "https://ref/", video, "zh")
    assert out == str(tmp_path / "Show - S01E01 - WEBDL - 1080p.zh.srt")
    assert os.path.exists(out)
    assert "https://x/index-f1.m3u8" in seen["cmd"]


def test_save_subtitles_honours_the_language_code(monkeypatch, tmp_path):
    fake_ffmpeg(monkeypatch, writes="1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    out = vsniff.save_subtitles("https://x/s.m3u8", "https://ref/", video, "yue")
    assert out.endswith(".yue.srt")


def test_save_subtitles_cleans_up_an_empty_result(monkeypatch, tmp_path, capsys):
    fake_ffmpeg(monkeypatch, writes="", returncode=0)
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    assert vsniff.save_subtitles("https://x/s.m3u8", "https://ref/", video, "zh") is None
    assert list(tmp_path.iterdir()) == []
    assert "subtitles skipped" in capsys.readouterr().out


def test_save_subtitles_survives_an_ffmpeg_failure(monkeypatch, tmp_path, capsys):
    fake_ffmpeg(monkeypatch, writes=None, returncode=1, stderr="Server returned 404")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    assert vsniff.save_subtitles("https://x/s.m3u8", "https://ref/", video, "zh") is None
    assert "404" in capsys.readouterr().out


def test_encode_url_percent_encodes_the_path():
    got = vsniff.encode_url("https://cdn.example.com/動畫/01.mp4/master.m3u8")
    assert got == ("https://cdn.example.com/%E5%8B%95%E7%95%AB/01.mp4/master.m3u8")


def test_existing_episodes_matches_by_prefix(tmp_path):
    (tmp_path / "Blossoms of Power - S01E03 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S01E07 - WEBDL - 720p.mp4").write_text("x")
    (tmp_path / "Other Show - S01E01 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S02E05 - WEBDL - 1080p.mp4").write_text("x")
    found = vsniff.existing_episodes(str(tmp_path), "Blossoms of Power", 1)
    assert found == {
        3: "Blossoms of Power - S01E03 - WEBDL - 1080p.mp4",
        7: "Blossoms of Power - S01E07 - WEBDL - 720p.mp4",
    }


def test_existing_episodes_keeps_the_real_resolution_tag(tmp_path):
    # a sidecar must be named after the file on disk, not a rebuilt name
    (tmp_path / "Show - S01E01 - WEBDL - 480p.mp4").write_text("x")
    found = vsniff.existing_episodes(str(tmp_path), "Show", 1)
    assert found[1].endswith("480p.mp4")


def test_existing_episodes_missing_dir_is_empty():
    assert vsniff.existing_episodes("/no/such/dir", "X", 1) == {}


def test_subtitle_sidecar_replaces_the_video_extension():
    assert vsniff.subtitle_sidecar("/lib/Show - S01E01 - WEBDL - 1080p.mp4", "zh") == \
        "/lib/Show - S01E01 - WEBDL - 1080p.zh.srt"


def test_subtitle_sidecar_keeps_dots_inside_the_name():
    assert vsniff.subtitle_sidecar("/lib/Dr. Show - S01E01 - WEBDL - 720p.mp4", "en") \
        == "/lib/Dr. Show - S01E01 - WEBDL - 720p.en.srt"


def test_discover_with_session_delegates(monkeypatch):
    calls = {}

    class FakeAdapter:
        def discover(self, page, ctx, url, user_source):
            calls["discover"] = (url, user_source)
            return ("SRC", "http://x/index.m3u8", "http://ref/")

    def fake_analyze(ctx, m3u8, referer, keep_ads):
        calls["analyze"] = (m3u8, referer, keep_ads)
        return ("1080p", 1416.0, "http://x/subs.m3u8", "http://127.0.0.1:9/clean-1.m3u8")

    monkeypatch.setattr(vsniff, "analyze_playlist", fake_analyze)
    out = vsniff.discover_with_session(
        FakeAdapter(), page=None, ctx=None,
        url="http://x/video/1-2.html#sid=6", user_source=None)
    # the (possibly ad-stripped) playlist replaces the raw stream URL
    assert out == ("SRC", "http://127.0.0.1:9/clean-1.m3u8", "http://ref/", "1080p", 1416.0,
                   "http://x/subs.m3u8")
    assert calls["discover"] == ("http://x/video/1-2.html#sid=6", None)
    assert calls["analyze"] == ("http://x/index.m3u8", "http://ref/", False)


def test_all_requires_out(capsys):
    rc = vsniff.main([
        "https://chinaq.net/video/68261-20.html", "--series", "X", "--all"])
    assert rc == 1
    assert "requires --out" in capsys.readouterr().err


def test_all_rejects_a_site_without_a_catalog(capsys):
    rc = vsniff.main([
        "https://example.com/watch/123",
        "--series", "X", "--all", "--out", "."])
    assert rc == 1
    assert "only supported for chinaq.net and hkanime.com" in capsys.readouterr().err


# ---- movies -------------------------------------------------------------- #
def test_movie_name_with_and_without_year():
    assert vsniff.movie_name("Cardcaptor Sakura The Movie", 1999) == \
        "Cardcaptor Sakura The Movie (1999)"
    assert vsniff.movie_name("Cardcaptor Sakura The Movie", None) == \
        "Cardcaptor Sakura The Movie"


def test_build_movie_filename_has_no_episode_tag():
    assert vsniff.build_movie_filename("CCS Movie", 1999, "WEBDL", "1080p") == \
        "CCS Movie (1999) - WEBDL - 1080p.mp4"


def test_existing_movie_matches_any_resolution(tmp_path):
    (tmp_path / "CCS Movie (1999) - WEBDL - 480p.mp4").write_text("x")
    (tmp_path / "CCS Movie (1999) - WEBDL - 480p.zh.srt").write_text("x")
    assert vsniff.existing_movie(str(tmp_path), "CCS Movie", 1999) == \
        "CCS Movie (1999) - WEBDL - 480p.mp4"


def test_existing_movie_absent_or_missing_folder_is_none(tmp_path):
    assert vsniff.existing_movie(str(tmp_path), "CCS Movie", 1999) is None
    assert vsniff.existing_movie("/no/such/dir", "CCS Movie", 1999) is None


def fake_api(monkeypatch, remarks, labels):
    data = {"info": [{"name": "x", "remarks": remarks}],
            "playurl": [{label: f"https://cdn/{i}.m3u8" for i, label in enumerate(labels)}]}
    monkeypatch.setattr(vsniff, "hk_api", lambda origin, sid: data)


def test_hk_is_movie_reads_the_site_tag(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["movie"])
    assert vsniff.HKAnimeSite().is_movie(HK_URL) is True
    fake_api(monkeypatch, "全集完 共22集", ["EP01 a"])
    assert vsniff.HKAnimeSite().is_movie(HK_URL) is False


def test_hk_is_movie_is_false_when_the_api_fails(monkeypatch):
    def boom(origin, sid):
        raise OSError("no network")
    monkeypatch.setattr(vsniff, "hk_api", boom)
    assert vsniff.HKAnimeSite().is_movie(HK_URL) is False


def test_hk_movie_url_resolves_a_bare_single_video_entry(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["movie"])
    got = vsniff.HKAnimeSite().movie_url("https://www.hkanime.com/play/x/357")
    assert got == "https://www.hkanime.com/play/x/357x0"


def test_hk_movie_url_keeps_an_explicit_index(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["a", "b", "c"])
    url = "https://www.hkanime.com/play/x/338x2"
    assert vsniff.HKAnimeSite().movie_url(url) == url


def test_hk_movie_url_refuses_to_guess_inside_a_bundle(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["Film One", "Film Two", "Film Three"])
    with pytest.raises(vsniff.VsniffError) as err:
        vsniff.HKAnimeSite().movie_url("https://www.hkanime.com/play/x/338")
    assert "3 separate movies" in str(err.value)
    assert "338x2  Film Three" in str(err.value)


def fake_movie_run(monkeypatch, subtitles="https://cdn/subs.m3u8"):
    """Stub the network edges of a movie download; record what was asked for."""
    calls = {"discover": 0, "download": [], "subs": []}

    def discover(url, source, show=False, keep_ads=False):
        calls["discover"] += 1
        return (None, "hkanime", "https://cdn/m.m3u8", "https://ref/",
                "1080p", 60.0, subtitles)

    def download(m3u8, referer, out_path, duration=None):
        calls["download"].append(out_path)
        with open(out_path, "w") as fh:
            fh.write("x" * 60_000)
        return 60_000

    def subs(sub_url, referer, video_path, lang):
        calls["subs"].append(vsniff.subtitle_sidecar(video_path, lang))

    monkeypatch.setattr(vsniff, "discover_stream", discover)
    monkeypatch.setattr(vsniff, "download_stream", download)
    monkeypatch.setattr(vsniff, "save_subtitles", subs)
    return calls


MOVIE_URL = "https://example.com/watch/ccs-movie"


def test_movie_lands_in_its_own_folder(monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    rc = vsniff.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--out", str(tmp_path)])
    assert rc == 0
    folder = tmp_path / "CCS Movie (1999)"
    assert calls["download"] == [str(folder / "CCS Movie (1999) - WEBDL - 1080p.mp4")]
    assert calls["subs"] == [str(folder / "CCS Movie (1999) - WEBDL - 1080p.zh.srt")]


def test_movie_all_skips_a_complete_copy_without_touching_the_network(
        monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    folder = tmp_path / "CCS Movie (1999)"
    folder.mkdir()
    (folder / "CCS Movie (1999) - WEBDL - 720p.mp4").write_text("x")
    (folder / "CCS Movie (1999) - WEBDL - 720p.zh.srt").write_text("x")
    rc = vsniff.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--all", "--out", str(tmp_path)])
    assert rc == 0
    assert calls["discover"] == 0 and calls["download"] == []


def test_movie_all_backfills_only_the_sidecar(monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    folder = tmp_path / "CCS Movie (1999)"
    folder.mkdir()
    (folder / "CCS Movie (1999) - WEBDL - 720p.mp4").write_text("x")
    rc = vsniff.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--all", "--out", str(tmp_path)])
    assert rc == 0
    assert calls["download"] == []
    assert calls["subs"] == [str(folder / "CCS Movie (1999) - WEBDL - 720p.zh.srt")]


def test_movie_all_requires_out(monkeypatch, capsys):
    fake_movie_run(monkeypatch)
    rc = vsniff.main([MOVIE_URL, "--title", "X", "--movie", "--all"])
    assert rc == 1
    assert "requires --out" in capsys.readouterr().err


# ---- spliced-in ad removal ------------------------------------------------ #
# Shapes below are trimmed from real chinaq playlists (Nov 2026).
def _playlist(*blocks, lead_disc=False):
    """Build a media playlist; each block is [(duration, uri), ...]."""
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:8",
           "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0"]
    for i, block in enumerate(blocks):
        if i or lead_disc:
            out.append("#EXT-X-DISCONTINUITY")
        for d, uri in block:
            out += [f"#EXTINF:{d},", uri]
    return "\n".join(out + ["#EXT-X-ENDLIST"]) + "\n"


def _main(n, prefix, durs=(3.16, 3.12, 3.36, 4.0, 2.52, 1.92)):
    return [(durs[i % len(durs)], f"{prefix}m{i}.ts") for i in range(n)]


DY_MAIN = "https://bf.modujx17.com/20261019/7cbiw8z4/3506kb/hls/"
DY_AD = [(3, "/20261102/8EzXUOn8/10134kb/hls/a1.ts"),
         (3, "/20261102/8EzXUOn8/10134kb/hls/a2.ts"),
         (5, "/20261102/8EzXUOn8/10134kb/hls/a3.ts"),
         (0.64, "/20261102/8EzXUOn8/10134kb/hls/a4.ts")]
DY_URL = "https://play.modujx17.com/20261019/7cbiw8z4/3506kb/hls/index.m3u8"


def test_strip_ads_drops_blocks_from_a_foreign_directory():
    text = _playlist(_main(60, DY_MAIN), DY_AD, _main(60, DY_MAIN), DY_AD,
                     _main(60, DY_MAIN))
    clean, removed, n = vsniff.strip_ads(text, DY_URL)
    assert n == 2 and removed == pytest.approx(2 * 11.64)
    assert "8EzXUOn8" not in clean
    assert clean.count(DY_MAIN) == 180
    assert clean.count("#EXT-X-DISCONTINUITY") == 2  # between kept blocks only
    assert clean.rstrip().endswith("#EXT-X-ENDLIST")


UY_URL = "https://cdn.yzzy31-play.com/20261102/28527_b6e8c545/3000k/hls/mixed.m3u8"
UY_AD = [(6.633333, "ad1.ts"), (3.333333, "ad2.ts"), (4.8, "ad3.ts"),
         (1.7, "ad4.ts")]  # 30fps clip inside a 25fps episode, same directory
UY_MAIN = (6.72, 2.4, 4.0, 3.08, 5.88, 5.04)


def test_strip_ads_drops_blocks_at_a_different_frame_rate():
    text = _playlist(_main(60, "", UY_MAIN), UY_AD, _main(60, "", UY_MAIN),
                     [(5.933333, "t1.ts"), (3.333333, "t2.ts"), (0.3, "t3.ts")],
                     lead_disc=True)
    clean, removed, n = vsniff.strip_ads(text, UY_URL)
    assert n == 2
    assert "ad1.ts" not in clean and "t1.ts" not in clean
    # relative segment names become absolute so a local copy still resolves
    assert ("https://cdn.yzzy31-play.com/20261102/28527_b6e8c545/3000k/hls/m0.ts"
            in clean)


def test_strip_ads_ignores_scene_cut_discontinuities():
    # Some sources split every scene into its own block: same directory, same
    # 2s cadence, many tiny blocks. None of them are ads.
    d = "https://yyv13.qwe132456.cc/yyv13/202610/19/x/video/2000k_1080/hls/"
    blocks = [[(2.0, f"{d}s{b}_{i}.ts") for i in range(1 + b % 7)]
              for b in range(80)]
    assert vsniff.strip_ads(_playlist(*blocks), d + "index.m3u8") is None


def test_strip_ads_leaves_a_clean_playlist_alone():
    assert vsniff.strip_ads(_playlist(_main(100, DY_MAIN)), DY_URL) is None


def test_strip_ads_refuses_to_strip_too_much():
    # a "foreign" block that would be a large share of the video is not an ad
    text = _playlist(_main(10, DY_MAIN), DY_AD * 3)
    assert vsniff.strip_ads(text, DY_URL) is None


def test_strip_ads_keeps_the_encryption_key_of_a_dropped_preroll():
    key = '#EXT-X-KEY:METHOD=AES-128,URI="key.key"'
    text = _playlist(DY_AD, _main(60, DY_MAIN)).replace(
        "#EXTINF:3,\n/20261102/8EzXUOn8/10134kb/hls/a1.ts",
        key + "\n#EXTINF:3,\n/20261102/8EzXUOn8/10134kb/hls/a1.ts")
    clean, _removed, n = vsniff.strip_ads(text, DY_URL)
    assert n == 1
    assert ('#EXT-X-KEY:METHOD=AES-128,URI="https://play.modujx17.com/'
            '20261019/7cbiw8z4/3506kb/hls/key.key"') in clean


def test_serve_playlist_serves_the_text_over_http():
    url = vsniff.serve_playlist("#EXTM3U\n#EXT-X-ENDLIST\n")
    assert url.startswith("http://127.0.0.1:")
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.read().decode() == "#EXTM3U\n#EXT-X-ENDLIST\n"
