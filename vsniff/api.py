"""Using vsniff from Python (e.g. a service that drives it) instead of the CLI.

    from vsniff import api

    results = api.search("尚公主")                     # every site that can search
    with api.Session() as s:
        episodes = s.catalog(results[0].url)          # [(episode, page_url)]
        info = s.probe(episodes[0][1])                # resolution/length, no download
        done = s.download_episode(episodes[0][1], "/media/downloads",
                                  series="Tigers Sniff the Rose", season=1,
                                  episode=episodes[0][0],
                                  on_progress=lambda done, total: ...)

A Session holds one headless browser, started on first use and reused for
every call, so a run of downloads doesn't relaunch Chromium each time.
Playwright's sync API is single-threaded: give each worker thread its own
Session. Failures raise VsniffError.
"""
import logging
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from .common import UA, VsniffError
from .discovery import discover_with_session
from .download import download_stream, save_subtitles
from .naming import build_filename
from .sites import ADAPTERS, SearchResult, pick_adapter

log = logging.getLogger("vsniff")

__all__ = ["Download", "SearchResult", "Session", "StreamInfo", "search", "searchable_sites"]


@dataclass(frozen=True)
class StreamInfo:
    """What discovery found for one episode, without downloading it."""
    source: str                 # the playback source that worked (e.g. "JYun")
    resolution: str             # "1080p", "720p", … or "unknown"
    duration: Optional[float]   # seconds, when the playlist states it
    subtitles: bool             # a separate subtitle track exists


@dataclass(frozen=True)
class Download:
    """A finished episode download."""
    path: str
    size: int                   # bytes
    resolution: str
    duration: Optional[float]
    source: str
    subtitles_path: Optional[str]


def searchable_sites():
    """Names of the sites search() can query."""
    return [a.name for a in ADAPTERS if a.supports_search]


def search(query, sites=None):
    """[SearchResult] from every searchable site (or only those named in sites).

    One site failing (down, layout changed) is logged and skipped rather than
    losing the other sites' results. Each site matches its own spelling only,
    so search a title's simplified and traditional forms both.
    """
    results = []
    for adapter in ADAPTERS:
        if not adapter.supports_search or (sites and adapter.name not in sites):
            continue
        try:
            results.extend(adapter.search(query))
        except Exception as e:  # noqa: BLE001 - any site failure is non-fatal here
            log.warning("search on %s failed for %r: %s", adapter.name, query, e)
    return results


class Session:
    """A reusable browser session for catalog / probe / download calls."""

    def __init__(self, show=False):
        self.show = show
        self._pw = self._browser = self._ctx = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._ctx = self._page = None

    def _session(self):
        """(page, ctx), launching the browser on first use."""
        if self._page is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not self.show)
            self._ctx = self._browser.new_context(user_agent=UA)
            self._page = self._ctx.new_page()
        return self._page, self._ctx

    def catalog(self, series_url) -> List[Tuple[int, str]]:
        """[(episode, episode_page_url)] for an entry, e.g. a SearchResult.url."""
        adapter = pick_adapter(series_url)
        if not adapter.supports_batch:
            raise VsniffError(f"listing episodes isn't supported for '{adapter.name}' URLs")
        page, ctx = self._session()
        return sorted(adapter.catalog(page, ctx, series_url))

    def _discover(self, episode_url, source, keep_ads):
        adapter = pick_adapter(episode_url)
        page, ctx = self._session()
        return discover_with_session(adapter, page, ctx, episode_url, source, keep_ads)

    def probe(self, episode_url, source=None) -> StreamInfo:
        """Find an episode's stream and report its quality, downloading nothing.

        Useful for comparing candidate sources before committing to one.
        """
        label, _pl, _ref, resolution, duration, subtitles = self._discover(
            episode_url, source, keep_ads=True)  # nothing to strip for a probe
        return StreamInfo(label, resolution, duration, bool(subtitles))

    def download_episode(self, episode_url, out_dir, *, series, season, episode,
                         quality="WEBDL", source=None, sub_lang="zh", keep_ads=False,
                         on_progress: Optional[Callable[[float, Optional[float]], None]] = None
                         ) -> Download:
        """Download one episode into out_dir, Sonarr/Jellyfin-named.

        The filename's resolution tag comes from the stream itself, so it is
        only known once discovery has run; the returned Download has the path.
        A subtitle track, when the stream has one, lands beside it as .srt.
        """
        label, playlist, referer, resolution, duration, subtitles = self._discover(
            episode_url, source, keep_ads)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(
            out_dir, build_filename(series, season, episode, quality, resolution))
        size = download_stream(playlist, referer, out_path, duration, on_progress)
        sub_path = save_subtitles(subtitles, referer, out_path, sub_lang) if subtitles else None
        return Download(out_path, size, resolution, duration, label, sub_path)
