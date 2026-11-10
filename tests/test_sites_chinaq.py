from vsniff import sites
from vsniff.sites import chinaq


def test_series_id_from_video_url():
    s = sites.ChinaqSite()
    assert s.series_id("https://chinaq.net/video/68261-20.html#sid=1") == 68261


def test_series_id_from_voddetail_url():
    s = sites.ChinaqSite()
    assert s.series_id("https://chinaq.net/voddetail/68261.html") == 68261


def test_series_id_unknown_url_is_none():
    s = sites.ChinaqSite()
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
    s = sites.ChinaqSite()
    by_sid = s.parse_episodes(SAMPLE_VODDETAIL, 68261)
    assert by_sid == {6: {1, 2, 3}, 3: {1, 2}}


def test_parse_episodes_ignores_other_series_ids():
    s = sites.ChinaqSite()
    html = SAMPLE_VODDETAIL + '<a href="/video/99999-7.html#sid=6">x</a>'
    by_sid = s.parse_episodes(html, 68261)
    assert 7 not in by_sid.get(6, set())


def test_available_episodes_is_sorted_union():
    assert chinaq.available_episodes({6: {1, 2, 3}, 3: {1, 2}}) == [1, 2, 3]
