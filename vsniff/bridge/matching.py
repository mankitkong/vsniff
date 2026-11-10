"""Deciding whether a site's search result is the season Sonarr asked for.

A wrong match is worse than none: releases are named after the Sonarr show,
so Sonarr can't notice it got a different video. Every check below is a gate
that rejects outright — title similarity alone let One Piece (live action)
match the anime and Demon Slayer S1 match the 遊郭篇 arc in testing.
"""
import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..sites import SearchResult
from .textnorm import norm, parse_title

FINISHED_RX = re.compile(r"完|全\s*\d+\s*[集期]")
SIMILAR = 0.9  # normalised-title ratio counted as "the same title"


@dataclass(frozen=True)
class Show:
    """What the bridge knows about a Sonarr series."""
    tvdb_id: int
    title: str
    year: Optional[int]
    titles: Tuple[str, ...]           # every name to match: Sonarr's + TMDB's
    animation: bool                   # TMDB genre Animation
    seasons: Dict[int, int]           # Sonarr numbering: season -> episodes (no specials)
    season_names: Dict[int, Tuple[str, ...]] = field(default_factory=dict)  # TMDB, e.g. 遊郭篇
    runtime: Optional[int] = None     # typical episode length in minutes (Sonarr)


@dataclass(frozen=True)
class Candidate:
    """A search result accepted for one season."""
    result: SearchResult
    season: int
    offset: int                 # episodes of earlier seasons in the same entry (0 normally)
    how: str                    # "exact" | "similar" | "prefix" (title + named season/arc)
    reasons: Tuple[str, ...]

    def site_episode(self, episode):
        """The site's episode number for a Sonarr episode of this season."""
        return episode + self.offset

    @property
    def rank(self):
        """Sort key, best first: exact titles, explicit seasons, counts that fit."""
        return ({"exact": 0, "similar": 1, "prefix": 2}[self.how],
                0 if any(r.startswith("season ") for r in self.reasons) else 1,
                self.result.url)


@dataclass(frozen=True)
class Rejection:
    result: SearchResult
    reason: str


def _title_match(core_norm, show):
    """(how, leftover) for the best title relation, or None.

    leftover is text after a show title the entry starts with — an arc or
    sub-title ("鬼滅之刃 遊郭篇"), which must then name the season.
    """
    best = None
    for title in show.titles:
        t = norm(parse_title(title).core)
        if not t:
            continue
        if core_norm == t:
            return "exact", ""
        if difflib.SequenceMatcher(None, core_norm, t).ratio() >= SIMILAR:
            best = best or ("similar", "")
        elif core_norm.startswith(t) and len(t) >= (2 if re.search(r"[^\x00-\x7f]", t) else 4):
            if best is None or best[0] == "prefix" and len(core_norm) - len(t) < len(best[1]):
                best = ("prefix", core_norm[len(t):])
    return best


def _season_named(leftover, show):
    """Seasons whose TMDB name the leftover title text refers to."""
    hits = set()
    for season, names in show.season_names.items():
        for name in names:
            n = norm(parse_title(name).core)
            if len(n) >= 2 and (n in leftover or leftover in n):
                hits.add(season)
    return hits


def _combined_span(episodes, seasons):
    """Last season N if `episodes` equals seasons 1..N added up (N >= 2), else None.

    Sites often list the seasons aired so far as one entry: hkanime's 我推的孩子
    holds 24 episodes = Oshi no Ko S1 (11) + S2 (13), before S3 was added.
    """
    if episodes is None:
        return None
    total = 0
    for s in sorted(seasons):
        total += seasons[s]
        if total == episodes and s > min(seasons):
            return s
        if total > episodes:
            return None
    return None


def evaluate(result, show, season):
    """Candidate if result is `season` of `show`, else Rejection with the reason."""
    if season not in show.seasons:
        return Rejection(result, f"Sonarr has no season {season}")
    p = parse_title(result.title)
    if p.movie or "劇場版" in result.note:
        return Rejection(result, "movie entry")
    if result.site == "hkanime" and not show.animation:
        return Rejection(result, "hkanime only carries anime; this show isn't animation")

    match = _title_match(norm(p.core), show)
    if match is None:
        return Rejection(result, f"title {p.core!r} isn't one of the show's names")
    how, leftover = match
    reasons = [f"title {how}"]

    if p.year and show.year and abs(p.year - show.year) > 1:
        return Rejection(result, f"year {p.year} vs show {show.year}")

    offset = 0
    expected = show.seasons[season]
    if leftover:
        named = _season_named(leftover, show)
        if named != {season}:
            what = f"names season {sorted(named)}" if named else "isn't a season name"
            return Rejection(result, f"extra title text {leftover!r} {what}")
        reasons.append(f"season {season} by name")
    if p.season is not None:
        if p.season != season:
            return Rejection(result, f"marked season {p.season}, want {season}")
        reasons.append(f"season {season} marked")
    elif not leftover and len(show.seasons) > 1:
        first = min(show.seasons)
        span = _combined_span(result.episodes, show.seasons)
        if span and season <= span and span > first:
            offset = sum(n for s, n in show.seasons.items() if s < season)
            expected = sum(n for s, n in show.seasons.items() if s <= span)
            reasons.append(f"seasons {first}-{span} in one entry ({expected} episodes)")
        elif season == first and (result.episodes is None
                                  or result.episodes <= show.seasons[first] + 1):
            reasons.append("unmarked entry taken as the first season")
        else:
            return Rejection(result, f"no season marker, and {result.episodes} episodes "
                                     f"doesn't single out season {season}")

    eps = result.episodes
    if eps is not None and expected:
        finished = bool(FINISHED_RX.search(result.note))
        slack = max(1, round(expected * 0.1))
        if finished and abs(eps - expected) > slack:
            return Rejection(result, f"finished with {eps} episodes, season has {expected}")
        if not finished and eps > expected + slack:
            return Rejection(result, f"{eps} episodes, season has only {expected}")
        reasons.append(f"{eps}/{expected} episodes")
    return Candidate(result, season, offset, how, tuple(reasons))


def rank_candidates(results, show, season) -> Tuple[List[Candidate], List[Rejection]]:
    """Accepted candidates (best first, duplicates removed) and the rejections."""
    accepted, rejected, seen = [], [], set()
    for r in results:
        if r.url in seen:
            continue
        seen.add(r.url)
        verdict = evaluate(r, show, season)
        (accepted if isinstance(verdict, Candidate) else rejected).append(verdict)
    accepted.sort(key=lambda c: c.rank)
    return accepted, rejected
