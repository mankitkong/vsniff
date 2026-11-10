"""The Python API: cross-site search and the reusable Session."""
import pytest

from vsniff import api
from vsniff.common import VsniffError
from vsniff.sites import SearchResult


class FakeSite:
    supports_search = True

    def __init__(self, name, results=(), error=None):
        self.name, self.results, self.error = name, list(results), error

    def search(self, query):
        if self.error:
            raise self.error
        return self.results


def test_search_merges_every_searchable_site(monkeypatch):
    a = SearchResult("a", "Show", "https://a/1")
    b = SearchResult("b", "Show", "https://b/1")
    monkeypatch.setattr(api, "ADAPTERS", [FakeSite("a", [a]), FakeSite("b", [b])])
    assert api.search("Show") == [a, b]


def test_search_can_be_limited_to_named_sites(monkeypatch):
    b = SearchResult("b", "Show", "https://b/1")
    monkeypatch.setattr(api, "ADAPTERS", [FakeSite("a", [SearchResult("a", "x", "u")]),
                                          FakeSite("b", [b])])
    assert api.search("Show", sites=["b"]) == [b]


def test_search_survives_one_site_failing(monkeypatch, caplog):
    b = SearchResult("b", "Show", "https://b/1")
    monkeypatch.setattr(api, "ADAPTERS", [FakeSite("a", error=OSError("down")),
                                          FakeSite("b", [b])])
    assert api.search("Show") == [b]
    assert "search on a failed" in caplog.text


def test_search_skips_sites_that_cannot_search(monkeypatch):
    site = FakeSite("generic", error=AssertionError("searched"))
    site.supports_search = False
    monkeypatch.setattr(api, "ADAPTERS", [site])
    assert api.search("Show") == []


def no_browser(session):
    session._session = lambda: ("page", "ctx")
    return session


def test_catalog_rejects_a_site_without_one():
    with pytest.raises(VsniffError, match="isn't supported"):
        no_browser(api.Session()).catalog("https://example.com/watch/1")


def test_probe_reports_quality_without_downloading(monkeypatch):
    seen = {}

    def discover(adapter, page, ctx, url, source, keep_ads):
        seen.update(url=url, keep_ads=keep_ads)
        return "JYun", "https://cdn/i.m3u8", "https://r/", "1080p", 2700.0, None

    monkeypatch.setattr(api, "discover_with_session", discover)
    info = no_browser(api.Session()).probe("https://dramasq.io/vodplay/1/ep1.html")
    assert info == api.StreamInfo("JYun", "1080p", 2700.0, False)
    assert seen == {"url": "https://dramasq.io/vodplay/1/ep1.html", "keep_ads": True}


def test_download_episode_names_the_file_and_reports_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "discover_with_session",
                        lambda *a: ("GYun", "https://cdn/i.m3u8", "https://r/",
                                    "1080p", 60.0, "https://cdn/subs.m3u8"))
    calls = {}

    def download(playlist, referer, out_path, duration, on_progress):
        calls["download"] = (playlist, out_path, duration)
        on_progress(30.0, duration)
        return 123_456

    monkeypatch.setattr(api, "download_stream", download)
    monkeypatch.setattr(api, "save_subtitles",
                        lambda sub, ref, video, lang: video[:-4] + f".{lang}.srt")
    progress = []
    done = no_browser(api.Session()).download_episode(
        "https://dramasq.io/vodplay/1/ep7.html", str(tmp_path / "out"),
        series="Show", season=2, episode=7,
        on_progress=lambda d, t: progress.append((d, t)))

    path = str(tmp_path / "out" / "Show - S02E07 - WEBDL - 1080p.mp4")
    assert done == api.Download(path, 123_456, "1080p", 60.0, "GYun",
                                path[:-4] + ".zh.srt")
    assert calls["download"] == ("https://cdn/i.m3u8", path, 60.0)
    assert progress == [(30.0, 60.0)]


def test_download_episode_without_subtitles(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "discover_with_session",
                        lambda *a: ("hkanime", "https://cdn/m.m3u8", "https://r/",
                                    "720p", None, None))
    monkeypatch.setattr(api, "download_stream", lambda *a: 99_999)
    monkeypatch.setattr(api, "save_subtitles", lambda *a: pytest.fail("no subtitle track"))
    done = no_browser(api.Session()).download_episode(
        "https://www.hkanime.com/play/x/1x0", str(tmp_path), series="S", season=1, episode=1)
    assert done.subtitles_path is None and done.path.endswith("S - S01E01 - WEBDL - 720p.mp4")


def test_close_without_a_browser_is_harmless():
    api.Session().close()
