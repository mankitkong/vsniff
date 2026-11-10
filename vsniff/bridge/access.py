"""Polite, cached access to the sites for the bridge.

Sonarr can fire many searches in a row (a season search, an RSS sync, the
UI's interactive search), and dramasq answers bursts with HTTP 429. So site
searches are paced per site and cached, episode lists are cached briefly, and
everything needing the browser runs on one dedicated thread — Playwright's
sync API is single-threaded, and one shared browser is plenty.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from .. import api
from ..sites import ADAPTERS, SearchResult

log = logging.getLogger("vsniff.bridge")


class _TTLCache:
    def __init__(self, ttl):
        self.ttl, self._data, self._lock = ttl, {}, threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key, value):
        with self._lock:
            self._data[key] = (time.monotonic(), value)


class SiteAccess:
    def __init__(self, min_interval=2.0, search_ttl=3600, catalog_ttl=1800,
                 browser_timeout=180):
        self.min_interval = min_interval
        self.browser_timeout = browser_timeout
        self._search_cache = _TTLCache(search_ttl)
        self._catalog_cache = _TTLCache(catalog_ttl)
        self._site_locks: Dict[str, threading.Lock] = {}
        self._last_call: Dict[str, float] = {}
        self._backoff_until: Dict[str, float] = {}
        self._browser = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vsniff-browser")
        self._session = None  # created on, and only used from, the browser thread

    # ---- search ---- #
    def search(self, query, sites=None) -> List[SearchResult]:
        """Search each named (or every searchable) site, paced and cached."""
        results = []
        for adapter in ADAPTERS:
            if not adapter.supports_search or (sites and adapter.name not in sites):
                continue
            results.extend(self._search_site(adapter, query))
        return results

    def _search_site(self, adapter, query):
        key = (adapter.name, query)
        cached = self._search_cache.get(key)
        if cached is not None:
            return cached
        lock = self._site_locks.setdefault(adapter.name, threading.Lock())
        with lock:
            now = time.monotonic()
            if now < self._backoff_until.get(adapter.name, 0):
                return []  # recently rate-limited: skip, don't cache the miss
            wait = self._last_call.get(adapter.name, 0) + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
            try:
                found = adapter.search(query)
            except Exception as e:  # noqa: BLE001 - one site failing mustn't fail the search
                if "429" in str(e):
                    self._backoff_until[adapter.name] = time.monotonic() + 60
                log.warning("search on %s failed for %r: %s", adapter.name, query, e)
                return []
            finally:
                self._last_call[adapter.name] = time.monotonic()
        self._search_cache.put(key, found)
        return found

    # ---- browser-thread work ---- #
    def _on_browser(self, fn, *args):
        def run():
            if self._session is None:
                self._session = api.Session()
            return fn(self._session, *args)
        return self._browser.submit(run).result(timeout=self.browser_timeout)

    def catalog(self, entry_url) -> Dict[int, str]:
        """{site episode: episode page url} for an entry (cached)."""
        cached = self._catalog_cache.get(entry_url)
        if cached is not None:
            return cached
        found = dict(self._on_browser(lambda s, u: s.catalog(u), entry_url))
        self._catalog_cache.put(entry_url, found)
        return found

    def probe(self, episode_url) -> api.StreamInfo:
        return self._on_browser(lambda s, u: s.probe(u), episode_url)

    def close(self):
        def stop(session):
            session.close()
        if self._session is not None:
            self._browser.submit(stop, self._session).result(timeout=30)
        self._browser.shutdown(wait=False)

