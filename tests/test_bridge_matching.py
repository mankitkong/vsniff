"""Bridge: Chinese title handling and the match gates (cases from live sites)."""
import pytest

pytest.importorskip("opencc")

from vsniff.bridge.matching import Candidate, Rejection, Show, evaluate, rank_candidates  # noqa: E402
from vsniff.bridge.textnorm import norm, parse_title, search_queries  # noqa: E402
from vsniff.sites import SearchResult  # noqa: E402


# ---- textnorm ---------------------------------------------------------------- #
@pytest.mark.parametrize("title, core, season, year, lang, movie", [
    ("SPY×FAMILY 間諜家家酒 S3", "SPY×FAMILY 間諜家家酒", 3, None, None, False),
    ("長相思 第二季", "長相思", 2, None, None, False),
    ("哈德森與雷尅斯第九季", "哈德森與雷尅斯", 9, None, None, False),
    ("哈德森與雷克斯 第九季 第九季", "哈德森與雷克斯", 9, None, None, False),
    ("偏愛之戀2", "偏愛之戀", 2, None, None, False),
    ("天命成凰2026", "天命成凰", None, 2026, None, False),
    ("玫瑰戰爭粵語", "玫瑰戰爭", None, None, "粵語", False),
    ("海賊王 大電影", "海賊王", None, None, None, True),
    ("鬼滅之刃 [TVB J2 配音版]", "鬼滅之刃", None, None, None, False),
    ("​尚公主​", "尚公主", None, None, None, False),
])
def test_parse_title(title, core, season, year, lang, movie):
    p = parse_title(title)
    assert (p.core, p.season, p.year, p.lang, p.movie) == (core, season, year, lang, movie)


def test_parse_title_keeps_conflicting_season_markers_in_the_core():
    assert parse_title("某劇 第一季 第二季").season is None


@pytest.mark.parametrize("a, b", [
    ("晚鞦寄情", "晚秋寄情"),           # chinaq's variant character
    ("哈德森與雷尅斯", "哈德森与雷克斯"),
    ("慶餘年", "庆余年"),               # traditional vs simplified
    ("天命·成凰", "天命成凰"),            # punctuation
    ("SPY×FAMILY", "spy family"),
])
def test_norm_treats_spellings_alike(a, b):
    assert norm(a) == norm(b)


def test_search_queries_cover_both_scripts_and_skip_season_pieces():
    full, pieces = search_queries(["庆余年", "Joy of Life", "慶餘年第二季"])
    assert {"庆余年", "慶餘年", "Joy of Life"} <= set(full)
    assert pieces == []  # 3-character cores make no pieces; "二季" must never be one


def test_search_queries_make_two_character_pieces_for_long_titles():
    _full, pieces = search_queries(["哈德森与雷克斯"])
    assert "克斯" in pieces and "哈德" in pieces


# ---- matching ---------------------------------------------------------------- #
def result(title, note="", site="hkanime", episodes=None, url=None):
    from vsniff.sites.base import episode_count
    return SearchResult(site, title, url or f"https://{site}/{title}", note,
                        episodes if episodes is not None else episode_count(note))


DEMON_SLAYER = Show(
    tvdb_id=348545, title="Demon Slayer: Kimetsu no Yaiba", year=2019,
    titles=("Demon Slayer: Kimetsu no Yaiba", "鬼滅の刃", "鬼灭之刃", "鬼滅之刃"),
    animation=True, seasons={1: 26, 2: 7, 3: 11, 4: 11, 5: 8},
    season_names={1: ("竈門炭治郎 立志篇", "立志篇"), 2: ("無限列車篇",), 3: ("遊郭篇",),
                  4: ("刀匠村篇",), 5: ("柱訓練篇",)})
DS_RESULTS = [result("鬼滅之刃 竈門炭治郎 立志篇", "全集完 共26集"),
              result("鬼滅之刃 無限列車篇", "全集完 共07集"),
              result("鬼滅之刃 遊郭篇", "全集完 共11集"),
              result("鬼滅之刃 [TVB J2 配音版]", "全集完 共26集")]


def accepted_titles(results, show, season):
    accepted, _ = rank_candidates(results, show, season)
    return [c.result.title for c in accepted]


def test_arc_entries_only_match_their_named_season():
    assert accepted_titles(DS_RESULTS, DEMON_SLAYER, 1) == [
        "鬼滅之刃 [TVB J2 配音版]", "鬼滅之刃 竈門炭治郎 立志篇"]
    assert accepted_titles(DS_RESULTS, DEMON_SLAYER, 3) == ["鬼滅之刃 遊郭篇"]


def test_a_season_with_the_same_episode_count_is_not_guessed():
    # S3 and S4 both have 11 episodes; only 遊郭篇 (S3) is on the site
    assert accepted_titles(DS_RESULTS, DEMON_SLAYER, 4) == []


def test_live_action_never_matches_an_anime_only_site():
    one_piece_live = Show(1, "ONE PIECE", 2023, ("ONE PIECE", "航海王", "海賊王"), False,
                          {1: 8, 2: 8})
    v = evaluate(result("海賊王 [ViuTV]", "全集完 共517-819集"), one_piece_live, 1)
    assert isinstance(v, Rejection) and "anime" in v.reason


def test_movie_entries_are_rejected_for_series():
    one_piece = Show(2, "One Piece", 1999, ("海賊王",), True, {1: 61})
    assert isinstance(evaluate(result("海賊王 大電影", "合集"), one_piece, 1), Rejection)
    assert isinstance(evaluate(result("海賊王 劇場版X", "劇場版"), one_piece, 1), Rejection)


SPY = Show(3, "SPY x FAMILY", 2022, ("SPY x FAMILY", "间谍过家家", "SPY×FAMILY 間諜家家酒"),
           True, {1: 25, 2: 12, 3: 13})


def test_explicit_season_markers_decide():
    results = [result(f"SPY×FAMILY 間諜家家酒 S{n}", f"全集完 共{c}集")
               for n, c in ((1, 25), (2, 12), (3, 13))]
    assert accepted_titles(results, SPY, 2) == ["SPY×FAMILY 間諜家家酒 S2"]


def test_all_seasons_in_one_entry_gets_an_offset():
    letter_bee = Show(4, "Tegami Bachi", 2009, ("信蜂", "テガミバチ"), True, {1: 25, 2: 25})
    c = evaluate(result("信蜂", "全集完 共50集"), letter_bee, 2)
    assert isinstance(c, Candidate) and c.offset == 25 and c.site_episode(1) == 26


def test_unmarked_entry_counts_as_the_first_season_only():
    two = Show(5, "Show", 2020, ("某劇",), False, {1: 20, 2: 20})
    assert isinstance(evaluate(result("某劇", "全20集", site="dramasq"), two, 1), Candidate)
    assert isinstance(evaluate(result("某劇", "全20集", site="dramasq"), two, 2), Rejection)


def test_titles_that_merely_start_with_the_show_name_are_rejected():
    fanhua = Show(6, "Blossoms Shanghai", 2023, ("繁花",), False, {1: 30})
    v = evaluate(result("繁花似錦", "全30集", site="dramasq"), fanhua, 1)
    assert isinstance(v, Rejection) and "isn't a season name" in v.reason


def test_near_miss_titles_are_rejected():
    show = Show(7, "The Ingenious One", 2025, ("藏海传",), False, {1: 40})
    assert isinstance(evaluate(result("藏海花", "第32集", site="dramasq"), show, 1), Rejection)


def test_airing_and_finished_episode_counts():
    show = Show(8, "Tigers Sniff the Rose", 2026, ("尚公主",), False, {1: 40})
    assert isinstance(evaluate(result("尚公主", "陸劇 更新至34集", site="chinaq"), show, 1),
                      Candidate)
    finished_short = evaluate(result("尚公主", "全20集", site="chinaq"), show, 1)
    assert isinstance(finished_short, Rejection) and "finished" in finished_short.reason
    too_many = evaluate(result("尚公主", "更新至60集", site="chinaq"), show, 1)
    assert isinstance(too_many, Rejection)


def test_year_suffix_must_fit_the_show():
    show = Show(9, "Destined to Be Phoenix", 2026, ("天命·成凰",), False, {1: 24})
    assert isinstance(evaluate(result("天命成凰2026", "更新至18集", site="chinaq"), show, 1),
                      Candidate)
    assert isinstance(evaluate(result("天命成凰2016", "全24集", site="chinaq"), show, 1),
                      Rejection)


def test_variant_spelling_matches_and_season_is_checked():
    show = Show(10, "Hudson & Rex", 2019, ("哈德森与雷克斯",), False, {8: 14, 9: 8})
    assert isinstance(evaluate(result("哈德森與雷尅斯第九季", "更新至06集", site="chinaq"),
                               show, 9), Candidate)
    assert isinstance(evaluate(result("哈德森與雷克斯 第八季", "", site="dramasq"), show, 9),
                      Rejection)


def test_duplicates_are_listed_once_and_exact_titles_rank_first():
    show = Show(11, "Show", 2026, ("尚公主",), False, {1: 40})
    a = result("尚公主", "更新至34集", site="chinaq", url="https://chinaq.net/voddetail/1104.html")
    accepted, _ = rank_candidates([a, a], show, 1)
    assert len(accepted) == 1


def test_unknown_season_is_rejected():
    show = Show(12, "Show", 2026, ("尚公主",), False, {1: 40})
    assert isinstance(evaluate(result("尚公主", site="chinaq"), show, 2), Rejection)


def test_a_fully_bracketed_title_keeps_its_name():
    p = parse_title("【我推的孩子】")
    assert p.core == "我推的孩子" and p.tags == []


OSHI = Show(421069, "Oshi no Ko", 2023, ("Oshi no Ko", "【我推的孩子】"), True,
            {1: 11, 2: 13, 3: 11, 4: 1})


def test_an_entry_holding_the_first_seasons_maps_them_with_offsets():
    entry = result("我推的孩子", "全集完 共24集")  # S1 (11) + S2 (13), S3 not added yet
    s1, s2 = evaluate(entry, OSHI, 1), evaluate(entry, OSHI, 2)
    assert isinstance(s1, Candidate) and s1.offset == 0
    assert isinstance(s2, Candidate) and s2.offset == 11 and s2.site_episode(1) == 12


def test_seasons_beyond_a_combined_entry_are_not_matched():
    assert isinstance(evaluate(result("我推的孩子", "全集完 共24集"), OSHI, 3), Rejection)


def test_styled_brackets_with_a_season_marker_keep_the_name():
    p = parse_title("【我推的孩子】 第二季")
    assert (p.core, p.season) == ("我推的孩子", 2)


def test_bracketed_tags_are_still_dropped_when_a_name_remains():
    p = parse_title("【粵語】某劇 第二季")
    assert (p.core, p.season, p.tags) == ("某劇", 2, ["粵語"])


def test_queries_use_the_name_without_styling_brackets():
    full, _ = search_queries(["【我推的孩子】", "Oshi no Ko"])
    assert "我推的孩子" in full and "【我推的孩子】" not in full
