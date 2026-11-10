"""Site search: parsing each site's results, and the shared helpers."""
import pytest

from vsniff import common, sites
from vsniff.sites import base, chinaq, dramasq, hkanime


# ---- shared helpers ------------------------------------------------------- #
def test_clean_text_drops_zero_width_characters():
    assert common.clean_text("\u200b尚公主\u200b") == "尚公主"


def test_clean_text_collapses_whitespace():
    assert common.clean_text("  長相思\n\t第一季  ") == "長相思 第一季"


@pytest.mark.parametrize("note, count", [
    ("全集完 共50集", 50),
    ("陸劇 更新至34集", 34),
    ("更新更新至12集", 12),
    ("全27集", 27),
    ("第23集", 23),
    ("第23集完結", 23),
    ("更新到 第126集", 126),
    ("全集完 共517-819集", None),   # a range, not a count
    ("綜藝 更新至20261110期", None),  # a date-numbered variety show
    ("劇場版", None),
    ("", None),
])
def test_episode_count(note, count):
    assert base.episode_count(note) == count


def test_generic_site_does_not_search():
    assert sites.GenericSite().search("anything") == []
    assert not sites.GenericSite.supports_search


def test_every_named_site_can_search():
    assert {a.name for a in sites.ADAPTERS if a.supports_search} == {
        "chinaq", "hkanime", "dramasq"}


# ---- chinaq ---------------------------------------------------------------- #
CQ_CARD = ('<div class="drama sizing"> <a href="/voddetail/{id}.html"> '
           '<div class="imgflat imgcover sizing"></div> '
           '<span class="icon-region">陸劇</span> <i></i> '
           '<div class="carousel-caption"><p>{cap}</p></div> '
           '<div class="title sizing">{title}</div> </a> '
           '<div class="title sizing"> 演員A,演員B </div> </div>')

CQ_PAGE = (
    '<h1>与 “尚公主” 相关的搜索结果</h1></div> <div class="thumb sizing clearfix">'
    + CQ_CARD.format(id=1104, cap="更新至34集", title="尚公主")
    + CQ_CARD.format(id=1105, cap="更新至34集", title="\u200b尚公主\u200b")
    + '</div> </div> <div class="sidebar sizing"> <div class="list-title">最近更新</div>'
    '<li class="drama sizing"><a href="/voddetail/1409.html">魔鬼的計謀 更新至05期</a></li>')


def test_chinaq_parse_search_reads_the_results_grid():
    got = chinaq.parse_search(CQ_PAGE)
    assert [(r.title, r.url, r.note, r.episodes) for r in got] == [
        ("尚公主", "https://chinaq.net/voddetail/1104.html", "陸劇 更新至34集", 34),
        ("尚公主", "https://chinaq.net/voddetail/1105.html", "陸劇 更新至34集", 34),
    ]
    assert {r.site for r in got} == {"chinaq"}


def test_chinaq_parse_search_ignores_the_recently_updated_sidebar():
    page = ('<div class="thumb sizing clearfix"> </div>'
            '<div class="sidebar sizing"><a href="/voddetail/1409.html">'
            '<div class="title sizing">魔鬼的計謀</div></a></div>')
    assert chinaq.parse_search(page) == []


def test_chinaq_search_urls_lead_to_its_catalog():
    url = chinaq.parse_search(CQ_PAGE)[0].url
    assert sites.ChinaqSite().series_id(url) == 1104


def test_chinaq_search_queries_the_vodsearch_page(monkeypatch):
    seen = []
    monkeypatch.setattr(chinaq, "http_get", lambda url: seen.append(url) or CQ_PAGE)
    assert len(sites.ChinaqSite().search(" 尚公主 ")) == 2
    assert seen == ["https://chinaq.net/vodsearch/-------------.html?wd=%E5%B0%9A%E5%85%AC%E4%B8%BB"]


def test_blank_query_makes_no_request(monkeypatch):
    monkeypatch.setattr(chinaq, "http_get", lambda url: pytest.fail("fetched"))
    monkeypatch.setattr(dramasq, "http_get", lambda url: pytest.fail("fetched"))
    assert sites.ChinaqSite().search("  ") == []
    assert sites.DramasqSite().search("") == []


# ---- dramasq --------------------------------------------------------------- #
DQ_PAGE = """<div class="main-content ">
  <div class="list-title ">「长相思」搜尋結果</div>
  <div class="list  clearfix"><ul>
    <li><a class="drama " href="/detail/202458112.html">長相思 第二季 第23集</a></li>
    <li><a class="drama " href="/detail/202389894.html">長相思 第一季 </a></li>
    <li><a class="drama " href="/detail/202379324.html">繁花 第30集完結</a></li>
  </ul></div>
</div>
<div class="sidebar "><div class="list clearfix">
  <li><a class="drama " href="/detail/1.html">別的劇 第1集</a></li>
</div></div>"""


def test_dramasq_parse_search_splits_title_from_status():
    got = dramasq.parse_search(DQ_PAGE)
    assert [(r.title, r.note, r.episodes) for r in got] == [
        ("長相思 第二季", "第23集", 23),
        ("長相思 第一季", "", None),
        ("繁花", "第30集完結", 30),
    ]
    assert got[0].url == "https://dramasq.io/detail/202458112.html"


def test_dramasq_search_urls_lead_to_its_catalog():
    url = dramasq.parse_search(DQ_PAGE)[0].url
    assert sites.DramasqSite().series_id(url) == 202458112


def test_dramasq_parse_search_without_results():
    assert dramasq.parse_search('<div class="main-content "><ul></ul></div>') == []


# ---- hkanime --------------------------------------------------------------- #
HK_INDEX = {
    "https://www.hkanime.com/play/葬送的芙莉蓮 S1/233": [
        {"name": "葬送的芙莉蓮 S1", "tag": "葬送的芙莉蓮,芙莉蓮", "remarks": "全集完 共28集"}],
    "https://www.hkanime.com/play/SPY×FAMILY 間諜家家酒 S3/283": [
        {"name": "SPY×FAMILY 間諜家家酒 S3", "tag": "間諜,家家酒", "remarks": "全集完 共13集"}],
    "https://www.hkanime.com/play/海賊王 [ViuTV]/588": [
        {"name": "海賊王 [ViuTV]", "tag": "海賊王,ONE PIECE", "remarks": "全集完 共517-819集"}],
}


def test_hk_search_matches_name_or_tag_case_insensitively():
    assert [r.title for r in hkanime.hk_search(HK_INDEX, "spy")] == ["SPY×FAMILY 間諜家家酒 S3"]
    assert [r.title for r in hkanime.hk_search(HK_INDEX, "one piece")] == ["海賊王 [ViuTV]"]


def test_hk_search_reports_the_note_and_count():
    (r,) = hkanime.hk_search(HK_INDEX, "芙莉蓮")
    assert (r.site, r.note, r.episodes) == ("hkanime", "全集完 共28集", 28)
    assert r.url == "https://www.hkanime.com/play/葬送的芙莉蓮 S1/233"


def test_hk_search_range_entries_have_no_count():
    (r,) = hkanime.hk_search(HK_INDEX, "海賊王")
    assert r.episodes is None


def test_hk_search_urls_lead_to_its_catalog():
    (r,) = hkanime.hk_search(HK_INDEX, "芙莉蓮")
    assert sites.HKAnimeSite().series_id(r.url) == 233


def test_hk_search_blank_query_matches_nothing():
    assert hkanime.hk_search(HK_INDEX, " ") == []


def test_hk_index_is_fetched_once_within_its_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(hkanime, "http_get",
                        lambda url, referer=None: calls.append(url) or '{"u": []}')
    monkeypatch.setattr(hkanime, "_hk_index", {"at": 0.0, "data": None})
    hkanime.hk_index("https://www.hkanime.com")
    hkanime.hk_index("https://www.hkanime.com")
    assert calls == ["https://www.hkanime.com/json-api"]
