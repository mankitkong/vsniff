"""From a Sonarr request (show, season[, episode]) to releases to offer.

Two modes, chosen by the indexer entry Sonarr calls through:
- browse: every source that has the episode, so interactive search can show
  them side by side;
- auto:   only the season's pinned source. The first time a season is asked
  for, the best candidate is pinned (highest resolution, then the
  closest title) and every later episode comes from it, so a season
  never mixes sources. A manual grab of another source re-pins.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .access import SiteAccess
from .matching import Candidate, Show, rank_candidates
from .metadata import Metadata
from .store import Release, Store
from .textnorm import search_queries

log = logging.getLogger("vsniff.bridge")

RES_ORDER = {"2160p": 5, "1440p": 4, "1080p": 3, "720p": 2, "480p": 1, "360p": 0}
# rough MB per minute of a WEB-DL at each resolution, for the size Sonarr shows
MB_PER_MIN = {"2160p": 45, "1440p": 25, "1080p": 15, "720p": 8, "480p": 4, "360p": 3}
CANDIDATE_TTL = 3600
PROBE_TTL = 7 * 86400


@dataclass(frozen=True)
class Quality:
    resolution: str
    duration: Optional[float]


def release_title(show_title, season, episode, resolution, site):
    """A name Sonarr parses: show, SxxEyy, resolution + WEB-DL, site as group."""
    res = f" {resolution}" if resolution in RES_ORDER else ""
    return f"{show_title} S{season:02d}E{episode:02d}{res} WEB-DL AAC-{site}"


def estimated_size(resolution, duration):
    if not duration or resolution not in MB_PER_MIN:
        return 0  # Sonarr skips its size check for size 0
    return int(duration / 60 * MB_PER_MIN[resolution] * 1024 * 1024)


class Resolver:
    def __init__(self, store: Store, metadata: Metadata, access: SiteAccess):
        self.store, self.metadata, self.access = store, metadata, access
        self._candidates: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()

    # ---- matching ---- #
    def candidates(self, show: Show, season) -> List[Candidate]:
        key = (show.tvdb_id, season)
        with self._lock:
            hit = self._candidates.get(key)
        if hit and time.monotonic() - hit[0] < CANDIDATE_TTL:
            return hit[1]
        full, pieces = search_queries(show.titles)
        results = []
        for q in full:
            results += self.access.search(q)
        for q in pieces:
            results += self.access.search(q, sites=["chinaq"])
        accepted, rejected = rank_candidates(results, show, season)
        for r in rejected:
            log.debug("tvdb %s S%s: rejected %s %r: %s", show.tvdb_id, season,
                      r.result.site, r.result.title, r.reason)
        log.info("tvdb %s S%s: %d candidate(s) from %d result(s)", show.tvdb_id, season,
                 len(accepted), len(results))
        with self._lock:
            self._candidates[key] = (time.monotonic(), accepted)
        return accepted

    # ---- quality ---- #
    def quality(self, entry_url, catalog) -> Quality:
        cached = self.store.probe(entry_url)
        if cached and time.time() - cached.probed_at < PROBE_TTL:
            return Quality(cached.resolution, cached.duration)
        first = catalog[min(catalog)]
        try:
            info = self.access.probe(first)
        except Exception as e:  # noqa: BLE001 - offer it unrated rather than not at all
            log.warning("probe failed for %s: %s", first, e)
            return Quality("unknown", None)
        self.store.save_probe(entry_url, info.resolution, info.duration, info.source)
        return Quality(info.resolution, info.duration)

    def _catalog(self, entry_url) -> Dict[int, str]:
        try:
            return self.access.catalog(entry_url)
        except Exception as e:  # noqa: BLE001 - delisted entries (dramasq 404s) etc.
            log.info("no episode list for %s: %s", entry_url, e)
            return {}

    # ---- releases ---- #
    def releases(self, tvdb_id, season, episode=None, mode="browse") -> List[Release]:
        show = self.metadata.show(tvdb_id)
        if show is None or season not in show.seasons:
            return []
        wanted = [episode] if episode else list(range(1, show.seasons[season] + 1))
        if mode == "auto":
            pin = self._pin_for(show, season)
            sources = [pin] if pin else []
        else:
            sources = [(c.result.site, c.result.url, c.result.title, c.offset)
                       for c in self.candidates(show, season)]
        out = []
        for site, entry_url, entry_title, offset in sources:
            catalog = self._catalog(entry_url)
            if not catalog:
                continue
            available = [e for e in wanted if e + offset in catalog]
            if not available:
                continue
            q = self.quality(entry_url, catalog)
            for e in available:
                out.append(self._release(show, season, e, site, entry_url, entry_title,
                                         offset, catalog[e + offset], q))
        return out

    def recent(self, per_pin=3) -> List[Release]:
        """Newest episodes of every pinned season, for Sonarr's RSS sync.

        Also the backstop for pin cleanup: a pinned show Sonarr no longer has
        (deleted while the webhook couldn't reach the bridge) loses its pins.
        """
        out = []
        for pin in self.store.pins():
            try:
                show = self.metadata.show(pin.tvdb_id)
            except Exception as e:  # noqa: BLE001 - Sonarr unreachable: keep the pin
                log.warning("couldn't check tvdb %s in Sonarr: %s", pin.tvdb_id, e)
                continue
            if show is None:
                self.forget_show(pin.tvdb_id, "no longer in Sonarr")
                continue
            if pin.season not in show.seasons:
                continue
            catalog = self._catalog(pin.entry_url)
            eps = [s - pin.episode_offset for s in sorted(catalog)]
            eps = [e for e in eps if 1 <= e <= show.seasons[pin.season]][-per_pin:]
            if not eps:
                continue
            q = self.quality(pin.entry_url, catalog)
            for e in eps:
                out.append(self._release(show, pin.season, e, pin.site, pin.entry_url,
                                         pin.entry_title, pin.episode_offset,
                                         catalog[e + pin.episode_offset], q))
        return out

    def _release(self, show, season, episode, site, entry_url, entry_title, offset,
                 episode_url, q: Quality) -> Release:
        return self.store.save_release(
            tvdb_id=show.tvdb_id, season=season, episode=episode, site=site,
            entry_url=entry_url, entry_title=entry_title, site_episode=episode + offset,
            episode_url=episode_url, episode_offset=offset,
            title=release_title(show.title, season, episode, q.resolution, site),
            # Sonarr's typical episode length beats the probe's: the probed
            # episode can be a feature-length opener (Oshi no Ko's first is 82 min)
            resolution=q.resolution,
            size=estimated_size(q.resolution, show.runtime * 60 if show.runtime else q.duration))

    # ---- pinning ---- #
    def _pin_for(self, show: Show, season):
        pin = self.store.pin(show.tvdb_id, season)
        if pin is not None:
            if self._catalog(pin.entry_url):
                return pin.site, pin.entry_url, pin.entry_title, pin.episode_offset
            log.info("pinned entry %s is gone; re-pinning tvdb %s S%s",
                     pin.entry_url, show.tvdb_id, season)
            self.store.drop_pin(show.tvdb_id, season)
        best = self._best(show, season)
        if best is None:
            return None
        c, q = best
        self.store.set_pin(show.tvdb_id, season, c.result.site, c.result.url, c.result.title,
                           c.offset, f"{q.resolution}; " + "; ".join(c.reasons))
        log.info("pinned tvdb %s S%s to %s %s (%s)", show.tvdb_id, season, c.result.site,
                 c.result.url, q.resolution)
        return c.result.site, c.result.url, c.result.title, c.offset

    def _best(self, show, season):
        """Highest-resolution candidate that lists episodes; ties keep match order."""
        scored = []
        for i, c in enumerate(self.candidates(show, season)):
            catalog = self._catalog(c.result.url)
            if not catalog:
                continue
            q = self.quality(c.result.url, catalog)
            scored.append((-RES_ORDER.get(q.resolution, -1), -len(catalog), i, c, q))
        if not scored:
            return None
        scored.sort(key=lambda t: t[:3])
        return scored[0][3], scored[0][4]

    def forget_show(self, tvdb_id, why):
        """Drop a show's pins and cached details (deleted from Sonarr)."""
        dropped = self.store.drop_pins(tvdb_id)
        forget = getattr(self.metadata, "forget", None)
        if forget:
            forget(tvdb_id)
        with self._lock:
            for key in [k for k in self._candidates if k[0] == tvdb_id]:
                del self._candidates[key]
        if dropped:
            log.info("dropped %d pin(s) for tvdb %s: %s", dropped, tvdb_id, why)
        return dropped

    def repin(self, release: Release, reason="grabbed manually"):
        """Make a grabbed release's entry the season's source from now on."""
        pin = self.store.pin(release.tvdb_id, release.season)
        if pin and pin.entry_url == release.entry_url:
            return
        self.store.set_pin(release.tvdb_id, release.season, release.site, release.entry_url,
                           release.entry_title, release.episode_offset, reason)
        log.info("re-pinned tvdb %s S%s to %s (%s)", release.tvdb_id, release.season,
                 release.entry_url, reason)
