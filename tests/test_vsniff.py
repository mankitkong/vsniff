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


def test_encode_url_percent_encodes_the_path():
    got = vsniff.encode_url("https://cdn.example.com/動畫/01.mp4/master.m3u8")
    assert got == ("https://cdn.example.com/%E5%8B%95%E7%95%AB/01.mp4/master.m3u8")


def test_existing_episodes_matches_by_prefix(tmp_path):
    (tmp_path / "Blossoms of Power - S01E03 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S01E07 - WEBDL - 720p.mp4").write_text("x")
    (tmp_path / "Other Show - S01E01 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S02E05 - WEBDL - 1080p.mp4").write_text("x")
    found = vsniff.existing_episodes(str(tmp_path), "Blossoms of Power", 1)
    assert found == {3, 7}


def test_existing_episodes_missing_dir_is_empty():
    assert vsniff.existing_episodes("/no/such/dir", "X", 1) == set()


def test_discover_with_session_delegates(monkeypatch):
    calls = {}

    class FakeAdapter:
        def discover(self, page, ctx, url, user_source):
            calls["discover"] = (url, user_source)
            return ("SRC", "http://x/index.m3u8", "http://ref/")

    def fake_analyze(ctx, m3u8, referer):
        calls["analyze"] = (m3u8, referer)
        return ("1080p", 1416.0)

    monkeypatch.setattr(vsniff, "analyze_playlist", fake_analyze)
    out = vsniff.discover_with_session(
        FakeAdapter(), page=None, ctx=None,
        url="http://x/video/1-2.html#sid=6", user_source=None)
    assert out == ("SRC", "http://x/index.m3u8", "http://ref/", "1080p", 1416.0)
    assert calls["discover"] == ("http://x/video/1-2.html#sid=6", None)
    assert calls["analyze"] == ("http://x/index.m3u8", "http://ref/")


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
