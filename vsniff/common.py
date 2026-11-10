"""Shared constants, the user-facing error type, and small URL helpers."""
from urllib.parse import quote, urlparse, urlsplit, urlunsplit


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class VsniffError(Exception):
    """User-facing failure."""


INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def clean_text(s):
    """Strip zero-width characters and collapse whitespace.

    Site titles carry invisible characters (chinaq lists 尚公主 wrapped in
    U+200B zero-width spaces next to a plain 尚公主), which would otherwise
    make equal titles compare unequal.
    """
    return " ".join((s or "").translate(INVISIBLE).split())


def origin_of(url):
    pr = urlparse(url)
    return f"{pr.scheme}://{pr.netloc}"


def encode_url(url):
    """Percent-encode a URL path so ffmpeg/urllib accept non-ASCII paths."""
    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/,.-_~%"),
                       s.query, s.fragment))


def fetch_text(ctx, url, referer):
    """Fetch a URL's text through the browser session (shares TLS + referer).

    The referer is percent-encoded first: hkanime page URLs carry raw Chinese
    (/play/信蜂/304x0), which HTTP headers can't hold, and the request would
    fail (silently, below) — losing the resolution, length and ad stripping.
    """
    try:
        return ctx.request.get(
            url, headers={"Referer": encode_url(referer), "User-Agent": UA}).text()
    except Exception:
        return ""
