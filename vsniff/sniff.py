"""Generic browser sniffing (site-agnostic)."""


def sniff_once(page, nav_url, timeout_ms=12000):
    """Load nav_url, trigger playback, capture the first working .m3u8.

    Returns (m3u8_url, referer) or None.
    """
    hits = []

    def on_resp(resp):
        if ".m3u8" in resp.url and resp.status < 400:
            hits.append((resp.url, resp.request.headers.get("referer", "")))

    page.context.on("response", on_resp)
    try:
        page.goto("about:blank")
        page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
        for sel in ("video", ".dplayer", "#player", ".vjs-big-play-button", ".play"):
            try:
                page.click(sel, timeout=1000)
            except Exception:
                pass
        page.wait_for_timeout(timeout_ms)
    finally:
        page.context.remove_listener("response", on_resp)

    if not hits:
        return None
    # prefer a master playlist over a bitrate-specific variant
    masters = [h for h in hits
               if "master.m3u8" in h[0] or h[0].rstrip("/").endswith("/index.m3u8")]
    return (masters or hits)[0]
