"""Shared constants, the user-facing error type, and small URL helpers."""
from urllib.parse import quote, urlparse, urlsplit, urlunsplit


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class VsniffError(Exception):
    """User-facing failure."""


def origin_of(url):
    pr = urlparse(url)
    return f"{pr.scheme}://{pr.netloc}"


def encode_url(url):
    """Percent-encode a URL path so ffmpeg/urllib accept non-ASCII paths."""
    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/,.-_~%"),
                       s.query, s.fragment))


def fetch_text(ctx, url, referer):
    """Fetch a URL's text through the browser session (shares TLS + referer)."""
    try:
        return ctx.request.get(
            url, headers={"Referer": referer, "User-Agent": UA}).text()
    except Exception:
        return ""
