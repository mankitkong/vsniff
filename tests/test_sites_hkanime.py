import pytest

from vsniff import common
from vsniff import sites
from vsniff.sites import hkanime


HK_URL = "https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A/145x1"


def test_hk_parts_from_player_url():
    s = sites.HKAnimeSite()
    assert s._parts(HK_URL) == ("https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A",
                                145, 1)


def test_hk_parts_from_detail_url_has_no_index():
    s = sites.HKAnimeSite()
    prefix, sid, idx = s._parts("https://www.hkanime.com/play/x/145")
    assert (sid, idx) == (145, None)
    assert prefix.endswith("/play/x")


def test_hk_series_id_unknown_url_is_none():
    assert sites.HKAnimeSite().series_id("https://www.hkanime.com/play") is None


def test_hk_episode_numbers_uses_labels():
    labels = ["EP01 a", "EP02 b", "EP03 c"]
    assert hkanime.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_keeps_series_that_starts_late():
    # One Piece [ViuTV] opens at EP517, so position is not the episode number.
    labels = ["EP517 a", "EP518 b", "EP519 c"]
    assert hkanime.hk_episode_numbers(labels) == [517, 518, 519]


def test_hk_episode_numbers_keeps_merged_double_episodes():
    labels = ["EP01-02 a", "EP03-04 b", "EP05-06 c"]
    assert hkanime.hk_episode_numbers(labels) == [1, 3, 5]


def test_hk_episode_numbers_falls_back_when_labels_repeat():
    labels = ["EP01 a", "EP01 b", "EP02 c"]
    assert hkanime.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_falls_back_when_a_label_has_no_number():
    labels = ["EP01 a", "Movie b", "EP03 c"]
    assert hkanime.hk_episode_numbers(labels) == [1, 2, 3]


def test_hk_episode_numbers_falls_back_when_labels_go_backwards():
    labels = ["EP03 a", "EP01 b"]
    assert hkanime.hk_episode_numbers(labels) == [1, 2]


def fake_playurl(monkeypatch, episodes):
    monkeypatch.setattr(hkanime, "hk_playurl", lambda origin, sid: episodes)


def test_hk_episode_reads_the_label_at_that_index(monkeypatch):
    fake_playurl(monkeypatch, [("EP517 a", "u1"), ("EP518 b", "u2")])
    assert sites.HKAnimeSite().episode(HK_URL) == 518


def test_hk_episode_falls_back_to_index_when_api_fails(monkeypatch):
    def boom(origin, sid):
        raise OSError("no network")
    monkeypatch.setattr(hkanime, "hk_playurl", boom)
    assert sites.HKAnimeSite().episode(HK_URL) == 2


def test_hk_episode_needs_an_index():
    assert sites.HKAnimeSite().episode("https://www.hkanime.com/play/x/145") is None


def test_hk_catalog_pairs_episodes_with_indexed_urls(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1"), ("EP02 b", "u2")])
    items = sites.HKAnimeSite().catalog(None, None, HK_URL)
    prefix = "https://www.hkanime.com/play/%E7%99%BE%E8%AE%8A"
    assert items == [(1, f"{prefix}/145x0"), (2, f"{prefix}/145x1")]


def test_hk_catalog_rejects_a_series_with_no_episodes(monkeypatch):
    fake_playurl(monkeypatch, [])
    with pytest.raises(common.VsniffError):
        sites.HKAnimeSite().catalog(None, None, HK_URL)


def test_hk_discover_uses_the_api_stream(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1"), ("EP02 b", "u2")])
    assert sites.HKAnimeSite().discover(None, None, HK_URL, None) == (
        "hkanime", "u2", HK_URL)


def test_hk_discover_sniffs_when_the_api_has_no_such_index(monkeypatch):
    fake_playurl(monkeypatch, [("EP01 a", "u1")])
    monkeypatch.setattr(hkanime, "sniff_once", lambda page, url: ("m", "r"))
    assert sites.HKAnimeSite().discover(None, None, HK_URL, None) == (
        "hkanime", "m", "r")


def fake_api(monkeypatch, remarks, labels):
    data = {"info": [{"name": "x", "remarks": remarks}],
            "playurl": [{label: f"https://cdn/{i}.m3u8" for i, label in enumerate(labels)}]}
    monkeypatch.setattr(hkanime, "hk_api", lambda origin, sid: data)


def test_hk_is_movie_reads_the_site_tag(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["movie"])
    assert sites.HKAnimeSite().is_movie(HK_URL) is True
    fake_api(monkeypatch, "全集完 共22集", ["EP01 a"])
    assert sites.HKAnimeSite().is_movie(HK_URL) is False


def test_hk_is_movie_is_false_when_the_api_fails(monkeypatch):
    def boom(origin, sid):
        raise OSError("no network")
    monkeypatch.setattr(hkanime, "hk_api", boom)
    assert sites.HKAnimeSite().is_movie(HK_URL) is False


def test_hk_movie_url_resolves_a_bare_single_video_entry(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["movie"])
    got = sites.HKAnimeSite().movie_url("https://www.hkanime.com/play/x/357")
    assert got == "https://www.hkanime.com/play/x/357x0"


def test_hk_movie_url_keeps_an_explicit_index(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["a", "b", "c"])
    url = "https://www.hkanime.com/play/x/338x2"
    assert sites.HKAnimeSite().movie_url(url) == url


def test_hk_movie_url_refuses_to_guess_inside_a_bundle(monkeypatch):
    fake_api(monkeypatch, "劇場版", ["Film One", "Film Two", "Film Three"])
    with pytest.raises(common.VsniffError) as err:
        sites.HKAnimeSite().movie_url("https://www.hkanime.com/play/x/338")
    assert "3 separate movies" in str(err.value)
    assert "338x2  Film Three" in str(err.value)
