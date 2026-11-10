import urllib.request

import pytest

from vsniff import common
from vsniff import sites
from vsniff.sites import dramasq


DQ_URL = "https://dramasq.io/vodplay/202614591/ep3.html"


DQ_TABS = (
    '<div class="blocker"></div>\n'
    '<a href="javascript:;" onclick="select_vd(this)" v_data="https://a/1.m3u8" '
    'class="active"><strong>片源1</strong><small>FYun</small></a>\n'
    '<a href="javascript:;" onclick="select_vd(this)" v_data="https://b/2.m3u8" '
    'class=""><strong>片源2</strong><small>JYun</small></a>\n'
    '<a href="javascript:;" onclick="select_vd(this)" v_data="https://c/3.m3u8" '
    'class=""><strong>片源3</strong><small>JYun</small></a>\n')


DQ_DATA = {"video_plays": [{"play_data": u, "src_site": s} for u, s in
                           (("https://a/1.m3u8", "fy"), ("https://b/2.m3u8", "jy"),
                            ("https://c/3.m3u8", "js"))],
           "html_content": DQ_TABS}


def test_pick_adapter_routes_dramasq_hosts():
    assert sites.pick_adapter(DQ_URL).name == "dramasq"
    assert sites.pick_adapter("https://www.dramasq.cc/detail/1.html").name == "dramasq"
    assert sites.pick_adapter("https://notdramasq.io/x").name == "generic"


def test_dq_series_id_from_play_and_detail_urls():
    s = sites.DramasqSite()
    assert s.series_id(DQ_URL) == 202614591
    assert s.series_id("https://dramasq.io/detail/202614591.html#all-eps") == 202614591
    assert s.series_id("https://dramasq.io/all/") is None


def test_dq_episode_from_ep_slug_only():
    s = sites.DramasqSite()
    assert s.episode(DQ_URL) == 3
    assert s.episode("https://dramasq.io/vodplay/202552516/ep369.html") == 369
    for slug in ("sp", "zheng_pian", "20261110"):
        assert s.episode(f"https://dramasq.io/vodplay/201932632/{slug}.html") is None


def test_dq_sources_numbers_tabs_and_keeps_duplicate_names():
    assert dramasq.dq_sources(DQ_DATA) == [
        (1, "FYun", "https://a/1.m3u8"),
        (2, "JYun", "https://b/2.m3u8"),
        (3, "JYun", "https://c/3.m3u8")]


def test_dq_sources_falls_back_to_the_bare_list():
    data = {"video_plays": DQ_DATA["video_plays"], "html_content": ""}
    assert dramasq.dq_sources(data)[1] == (2, "jy", "https://b/2.m3u8")


def test_dq_parse_episodes_ignores_other_series_and_non_numbered():
    html = ('<a href="/vodplay/9/ep2.html"> <a href="/vodplay/9/ep1.html">'
            '<a href="/vodplay/9/sp.html"> <a href="/vodplay/8/ep7.html">')
    assert sites.DramasqSite().parse_episodes(html, 9) == [1, 2]


def fake_dq(monkeypatch, live, data=DQ_DATA):
    monkeypatch.setattr(dramasq, "dq_api", lambda origin, sid, slug: data)
    monkeypatch.setattr(dramasq, "stream_reachable", lambda url, ref: url in live)


def test_dq_discover_skips_dead_sources(monkeypatch):
    fake_dq(monkeypatch, live={"https://b/2.m3u8", "https://c/3.m3u8"})
    assert sites.DramasqSite().discover(None, None, DQ_URL, None) == (
        "JYun", "https://b/2.m3u8", DQ_URL)


def test_dq_discover_honours_source_number(monkeypatch):
    fake_dq(monkeypatch, live={"https://b/2.m3u8", "https://c/3.m3u8"})
    got = sites.DramasqSite().discover(None, None, DQ_URL, "3")
    assert got[1] == "https://c/3.m3u8"


def test_dq_discover_fails_when_every_source_is_dead(monkeypatch):
    fake_dq(monkeypatch, live=set())
    with pytest.raises(common.VsniffError, match="all dramasq sources failed"):
        sites.DramasqSite().discover(None, None, DQ_URL, None)


def test_dq_discover_sniffs_when_the_api_fails(monkeypatch):
    def boom(*a):
        raise OSError("down")
    monkeypatch.setattr(dramasq, "dq_api", boom)
    monkeypatch.setattr(dramasq, "sniff_once", lambda page, url: ("m", "r"))
    assert sites.DramasqSite().discover(None, None, DQ_URL, None) == (
        "dramasq", "m", "r")


class _Resp:
    def __init__(self, body):
        self.body = body.encode()

    def read(self, *a):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_dq_catalog_adds_episodes_the_listing_has_not_caught_up_with(monkeypatch):
    html = "".join(f'<a href="/vodplay/9/ep{n}.html">' for n in (2, 1))
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(html))

    def api(origin, sid, slug):
        if slug in ("ep3", "ep4"):
            return DQ_DATA
        raise ValueError("not json")
    monkeypatch.setattr(dramasq, "dq_api", api)
    items = sites.DramasqSite().catalog(None, None, "https://dramasq.io/detail/9.html")
    assert [ep for ep, _ in items] == [1, 2, 3, 4]
    assert items[-1][1] == "https://dramasq.io/vodplay/9/ep4.html"


def test_dq_catalog_rejects_a_series_without_numbered_episodes(monkeypatch):
    html = '<a href="/vodplay/9/20261110.html">'
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(html))
    with pytest.raises(common.VsniffError, match="no numbered episodes"):
        sites.DramasqSite().catalog(None, None, "https://dramasq.io/detail/9.html")
