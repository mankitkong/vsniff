"""Building a Show from Sonarr (numbering) and TMDB (Chinese titles).

Sonarr's numbering is the one that matters — it's what Sonarr will ask for and
what it will import against — so seasons and episode counts come from Sonarr.
TMDB supplies what Sonarr lacks: the show's Chinese names per region (the
Hong Kong title can differ entirely from the mainland one) and season names
(遊郭篇) that sites use instead of season numbers.
"""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .matching import Show

log = logging.getLogger("vsniff.bridge")

TMDB = "https://api.themoviedb.org/3"
TITLE_LANGS = ("zh", "en")
SEASON_NAME_LANGS = ("zh-TW", "zh-HK", "zh-CN")
CACHE_TTL = 6 * 3600


def _get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Sonarr:
    """The few Sonarr API calls the bridge needs."""

    def __init__(self, url, api_key):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def _get(self, path, **params):
        q = f"?{urllib.parse.urlencode(params)}" if params else ""
        return _get_json(f"{self.url}/api/v3/{path}{q}", {"X-Api-Key": self.api_key})

    def series_by_tvdb(self, tvdb_id) -> Optional[dict]:
        found = self._get("series", tvdbId=tvdb_id)
        return found[0] if found else None

    def episodes(self, series_id) -> List[dict]:
        return self._get("episode", seriesId=series_id)


class Tmdb:
    def __init__(self, api_key):
        self.api_key = api_key

    def _get(self, path, **params):
        params["api_key"] = self.api_key
        return _get_json(f"{TMDB}/{path}?{urllib.parse.urlencode(params)}")

    def tv_id_for_tvdb(self, tvdb_id) -> Optional[int]:
        hits = self._get(f"find/{tvdb_id}", external_source="tvdb_id").get("tv_results")
        return hits[0]["id"] if hits else None

    def show(self, tmdb_id) -> dict:
        return self._get(f"tv/{tmdb_id}", append_to_response="translations")

    def localized(self, tmdb_id, language) -> dict:
        return self._get(f"tv/{tmdb_id}", language=language)


def tmdb_titles(details) -> List[str]:
    """Original + English name and every Chinese/English translation's name."""
    names = [details.get("original_name"), details.get("name")]
    for t in details.get("translations", {}).get("translations", []):
        if t.get("iso_639_1") in TITLE_LANGS:
            names.append(t.get("data", {}).get("name"))
    return [n for n in names if n]


def season_names(localized: List[dict], sonarr_seasons: Dict[int, int]) -> Dict[int, tuple]:
    """TMDB season names, kept only where TMDB's season agrees with Sonarr's.

    TMDB and TVDB number seasons differently for some shows; a name is only
    trusted for a season when both list the same episode count for it.
    """
    names: Dict[int, list] = {}
    for details in localized:
        for s in details.get("seasons", []):
            n, count, name = s.get("season_number"), s.get("episode_count"), s.get("name")
            if n in sonarr_seasons and count == sonarr_seasons[n] and name:
                if name not in names.setdefault(n, []):
                    names[n].append(name)
    return {n: tuple(v) for n, v in names.items()}


def sonarr_seasons(episodes: List[dict]) -> Dict[int, int]:
    """{season: episode count} from Sonarr's episode list, specials excluded."""
    counts: Dict[int, int] = {}
    for e in episodes:
        if e.get("seasonNumber", 0) > 0:
            counts[e["seasonNumber"]] = counts.get(e["seasonNumber"], 0) + 1
    return counts


class Metadata:
    """Show lookups by TVDB id, cached for CACHE_TTL."""

    def __init__(self, sonarr: Sonarr, tmdb: Optional[Tmdb]):
        self.sonarr, self.tmdb = sonarr, tmdb
        self._cache: Dict[int, tuple] = {}
        self._lock = threading.Lock()

    def show(self, tvdb_id) -> Optional[Show]:
        with self._lock:
            hit = self._cache.get(tvdb_id)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                return hit[1]
        show = self._build(tvdb_id)
        with self._lock:
            self._cache[tvdb_id] = (time.monotonic(), show)
        return show

    def _build(self, tvdb_id) -> Optional[Show]:
        series = self.sonarr.series_by_tvdb(tvdb_id)
        if series is None:
            log.info("tvdb %s isn't in Sonarr; nothing to match", tvdb_id)
            return None
        seasons = sonarr_seasons(self.sonarr.episodes(series["id"]))
        titles = [series["title"]] + [a["title"] for a in series.get("alternateTitles", [])]
        genres = {g.lower() for g in series.get("genres", [])}
        animation = bool(genres & {"animation", "anime"})
        names = {}
        tmdb_id = None
        if self.tmdb is not None:  # without a TMDB key, match on Sonarr's names only
            try:
                tmdb_id = series.get("tmdbId") or self.tmdb.tv_id_for_tvdb(tvdb_id)
            except Exception as e:  # noqa: BLE001
                log.warning("TMDB id lookup failed for tvdb %s: %s", tvdb_id, e)
        if tmdb_id:
            try:
                details = self.tmdb.show(tmdb_id)
                titles += tmdb_titles(details)
                animation = animation or any(g.get("id") == 16 for g in details.get("genres", []))
                names = season_names([self.tmdb.localized(tmdb_id, lang)
                                      for lang in SEASON_NAME_LANGS], seasons)
            except Exception as e:  # noqa: BLE001 - TMDB down: match on Sonarr's names alone
                log.warning("TMDB lookup failed for tvdb %s: %s", tvdb_id, e)
        unique = tuple(dict.fromkeys(t for t in titles if t))
        return Show(tvdb_id=tvdb_id, title=series["title"], year=series.get("year") or None,
                    titles=unique, animation=animation, seasons=seasons, season_names=names,
                    runtime=series.get("runtime") or None)
