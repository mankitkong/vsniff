"""The fallback adapter every site builds on, and helpers shared by adapters."""
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

from ..common import UA, VsniffError
from ..sniff import sniff_once


@dataclass(frozen=True)
class SearchResult:
    """One series (or movie) entry a site's search returned.

    `url` is the entry's page: pass it to the adapter's catalog() to list the
    episodes. `note` is the site's own status text ("更新至34集", "全集完 共50集",
    "劇場版") and `episodes` the episode count or latest episode it states, when
    it states a single number.
    """
    site: str
    title: str
    url: str
    note: str = ""
    episodes: Optional[int] = None


EPISODES_RX = re.compile(r"(?:共|全|更新至|更新到\s*第?|第)\s*(\d+)\s*[集期](?!\s*-)")


def episode_count(note):
    """The single episode number a site's status text states, or None.

    "全集完 共50集" -> 50, "更新至34集" -> 34, "第23集" -> 23. A range such as
    hkanime's One Piece "共517-819集" is not a count, so it gives None (the
    regex never sees a bare "N集" there), and so does a date-numbered variety
    show ("更新至20261110期" reads as a date, not 20 million episodes).
    """
    m = EPISODES_RX.search(note or "")
    if not m:
        return None
    n = int(m.group(1))
    return None if n > 9999 else n


def http_get(url, referer=None, timeout=25):
    """GET a page's text with vsniff's user agent (for search/catalog APIs)."""
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


class GenericSite:
    """Fallback: sniff whatever the page plays; user supplies episode."""
    name = "generic"
    supports_batch = False
    supports_search = False

    def matches(self, host):
        return True

    def search(self, query):
        """[SearchResult] for entries matching query; [] where unsupported.

        Searches match the site's own spelling only, so callers wanting a
        title in either script should search both its simplified and
        traditional forms (e.g. via OpenCC) and merge the results.
        """
        return []

    def episode(self, url):
        return None  # unknown -> require --episode

    def is_movie(self, url):
        return False  # unknown -> only --movie turns movie naming on

    def movie_url(self, url):
        """The page to fetch a movie from; the URL itself unless the site bundles."""
        return url

    def discover(self, page, ctx, url, user_source):
        """Return (source_label, m3u8_url, referer)."""
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the page (nothing played)")
        return "default", found[0], found[1]


def match_source(sources, token):
    """Resolve a --source token (number or case-insensitive name) to a sid."""
    token = str(token).strip()
    if token.isdigit():
        sid = int(token)
        if sid in {s for s, _ in sources}:
            return sid
        raise VsniffError(f"source number {sid} not available "
                          f"(have: {', '.join(str(s) for s, _ in sources)})")
    for sid, name in sources:
        if name.lower() == token.lower():
            return sid
    names = ", ".join(name for _, name in sources)
    raise VsniffError(f"source name {token!r} not found (have: {names})")
