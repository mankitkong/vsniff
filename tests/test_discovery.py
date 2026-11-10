from vsniff import discovery


def test_discover_with_session_delegates(monkeypatch):
    calls = {}

    class FakeAdapter:
        def discover(self, page, ctx, url, user_source):
            calls["discover"] = (url, user_source)
            return ("SRC", "http://x/index.m3u8", "http://ref/")

    def fake_analyze(ctx, m3u8, referer, keep_ads):
        calls["analyze"] = (m3u8, referer, keep_ads)
        return ("1080p", 1416.0, "http://x/subs.m3u8", "http://127.0.0.1:9/clean-1.m3u8")

    monkeypatch.setattr(discovery, "analyze_playlist", fake_analyze)
    out = discovery.discover_with_session(
        FakeAdapter(), page=None, ctx=None,
        url="http://x/video/1-2.html#sid=6", user_source=None)
    # the (possibly ad-stripped) playlist replaces the raw stream URL
    assert out == ("SRC", "http://127.0.0.1:9/clean-1.m3u8", "http://ref/", "1080p", 1416.0,
                   "http://x/subs.m3u8")
    assert calls["discover"] == ("http://x/video/1-2.html#sid=6", None)
    assert calls["analyze"] == ("http://x/index.m3u8", "http://ref/", False)
