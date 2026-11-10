"""Chinese title handling: normalising for comparison, splitting off season /
year / language markers, and the search queries worth sending each site.

Sites spell the same show differently: simplified vs traditional, Hong Kong
vs Taiwan wording, and on chinaq rare variant characters left over from a
careless conversion (晚鞦寄情 for 晚秋寄情). Comparisons therefore happen on a
normalised form: simplified, variant-folded, lower-case, with spaces and
punctuation removed.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

import opencc

from ..common import clean_text

_T2S = opencc.OpenCC("t2s")
_S2T = opencc.OpenCC("s2t")
_S2HK = opencc.OpenCC("s2hk")
_S2TW = opencc.OpenCC("s2tw")

# Rare variants chinaq uses that OpenCC's t2s leaves alone, folded to the
# simplified character (seen on the live site; a character table, not a list
# of shows). Unihan's variant data would be the general source if this grows.
VARIANTS = str.maketrans({
    "鋻": "鉴", "処": "处", "擧": "举", "儅": "当", "霛": "灵", "毉": "医",
    "隂": "阴", "尅": "克", "倣": "仿", "耑": "端", "鞦": "秋", "侷": "局",
    "曏": "向", "罈": "坛", "縯": "演", "鄕": "乡", "脩": "修", "郃": "合",
    "衚": "胡", "麪": "面", "鉄": "铁",
})

CJK_RX = re.compile(r"[㐀-鿿豈-﫿]")
NON_WORD_RX = re.compile(r"[\W_]+", re.UNICODE)

CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
             "九": 9, "十": 10}


def cn_number(s):
    """一..九十九 -> int (enough for season numbers); digits pass through."""
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        tens, _, ones = s.partition("十")
        return (CN_DIGITS.get(tens, 1)) * 10 + CN_DIGITS.get(ones, 0)
    return CN_DIGITS.get(s)


@lru_cache(maxsize=4096)
def norm(s):
    """Comparison form: simplified, variants folded, lower-case, no punctuation."""
    s = _T2S.convert(clean_text(s)).translate(VARIANTS).lower()
    return NON_WORD_RX.sub("", s)


def has_cjk(s):
    return bool(CJK_RX.search(s or ""))


# ---- qualifiers ------------------------------------------------------------ #
SEASON_PATTERNS = [
    re.compile(r"\bS(?:eason)?\s*0*(\d{1,2})\b", re.I),
    re.compile(r"第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[季部]"),
]
LANG_RX = re.compile(r"(粵語|粤语|國語|国语|普通話|普通话|粵配|粤配|國配|国配|原聲|原声|台語|台语)版?")
BRACKET_RX = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）")
TRAIL_YEAR_RX = re.compile(r"(?<=\S)\s*((?:19|20)\d{2})\s*$")
TRAIL_SEASON_RX = re.compile(r"(?<=[㐀-鿿])\s*([2-9])\s*$")  # 偏愛之戀2
MOVIE_RX = re.compile(r"劇場版|剧场版|大電影|大电影|電影版|电影版")


@dataclass
class Parsed:
    """A site title split into its show name and the qualifiers around it."""
    core: str                      # the name left once qualifiers are removed
    season: Optional[int] = None   # explicit season marker (S2, 第二季, trailing 2)
    year: Optional[int] = None     # trailing year (天命成凰2026)
    lang: Optional[str] = None     # 粵語 / 國語 …
    movie: bool = False
    tags: List[str] = field(default_factory=list)  # bracketed bits ([ViuTV])


def parse_title(title):
    """Split a site's title, e.g. "SPY×FAMILY 間諜家家酒 S3" -> core + season 3.

    Bracketed parts are usually tags ("海賊王 [ViuTV]"), but some shows style
    their name in brackets ("【我推的孩子】 第二季"): when dropping the brackets
    would leave no name, they're unwrapped instead.
    """
    p = _parse(clean_text(title), unwrap=False)
    if not p.core and BRACKET_RX.search(title or ""):
        p = _parse(clean_text(title), unwrap=True)
    return p


def _parse(t, unwrap):
    p = Parsed(core="")
    if unwrap:
        t = re.sub(r"[\[\]【】()（）]", " ", t)
    else:
        p.tags = [b.strip("[]【】()（） ") for b in BRACKET_RX.findall(t)]
        t = BRACKET_RX.sub(" ", t)
    m = LANG_RX.search(t)
    if m:
        p.lang = m.group(1)
        t = t[:m.start()] + " " + t[m.end():]
    if MOVIE_RX.search(t):
        p.movie = True
        t = MOVIE_RX.sub(" ", t)
    for rx in SEASON_PATTERNS:
        found = {cn_number(m.group(1)) for m in rx.finditer(t)}
        if len(found) == 1:  # a repeated marker ("第九季 第九季") is still one season
            p.season = found.pop()
            t = rx.sub(" ", t)
            break
    m = TRAIL_YEAR_RX.search(t)
    if m:
        p.year = int(m.group(1))
        t = t[:m.start()]
    if p.season is None:
        m = TRAIL_SEASON_RX.search(t)
        if m:
            p.season = int(m.group(1))
            t = t[:m.start()]
    p.core = clean_text(t).strip(" ：:·-–—|/")
    return p


# ---- queries --------------------------------------------------------------- #
def script_forms(title):
    """A Chinese title in every script a site might index it under."""
    t = clean_text(title)
    out = []
    for v in (t, _T2S.convert(t), _S2T.convert(t), _S2HK.convert(t), _S2TW.convert(t)):
        if v and v not in out:
            out.append(v)
    return out


def search_queries(titles, max_pieces=4):
    """(full_queries, piece_queries) to search with for a show's titles.

    full: every Chinese title in every script, plus Latin titles (hkanime's
    tags carry English names like "SPY"). pieces: two-character chunks of the
    Chinese titles' cores, for chinaq, whose variant spellings make a full
    title miss (寄情 still finds 晚鞦寄情). Season suffixes never become
    pieces ("二季" would match every second season on the site).
    """
    full, pieces = [], []
    for title in titles:
        title = clean_text(title)
        if not title:
            continue
        if has_cjk(title):
            name = parse_title(title).core or title  # 【我推的孩子】 -> 我推的孩子
            for form in script_forms(name):
                if form not in full:
                    full.append(form)
            core = "".join(CJK_RX.findall(name))
            if len(core) >= 4:
                for chunk in (core[-2:], core[:2]):
                    for form in script_forms(chunk):
                        if form not in pieces and len(pieces) < max_pieces:
                            pieces.append(form)
        elif title not in full:
            full.append(title)
    return full, pieces
