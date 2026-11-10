"""The fallback adapter every site builds on, and helpers shared by adapters."""
from ..common import VsniffError
from ..sniff import sniff_once


class GenericSite:
    """Fallback: sniff whatever the page plays; user supplies episode."""
    name = "generic"
    supports_batch = False

    def matches(self, host):
        return True

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
