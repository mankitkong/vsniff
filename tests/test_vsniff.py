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
    assert vsniff.GenericSite().supports_batch is False


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


def test_all_rejects_non_chinaq(capsys):
    rc = vsniff.main([
        "https://www.hkanime.com/play/x/120x0",
        "--series", "X", "--all", "--out", "."])
    assert rc == 1
    assert "only supported for chinaq" in capsys.readouterr().err
