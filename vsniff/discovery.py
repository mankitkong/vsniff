"""Discovery orchestration: find one stream using a browser session."""
from playwright.sync_api import sync_playwright

from .common import UA
from .playlist import analyze_playlist
from .sites import pick_adapter


def discover_with_session(adapter, page, ctx, url, user_source, keep_ads=False):
    """Discover one stream using an already-open browser session.

    Returns (source_label, playlist, referer, resolution, duration, subtitles);
    playlist is the stream URL, or a local ad-stripped copy of it.
    """
    label, m3u8, referer = adapter.discover(page, ctx, url, user_source)
    resolution, duration, subtitles, playlist = analyze_playlist(
        ctx, m3u8, referer, keep_ads)
    return label, playlist, referer, resolution, duration, subtitles


def discover_stream(url, user_source, show=False, keep_ads=False):
    """Return (adapter,) + everything discover_with_session found."""
    adapter = pick_adapter(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            found = discover_with_session(
                adapter, page, ctx, url, user_source, keep_ads)
            return (adapter,) + found
        finally:
            browser.close()
