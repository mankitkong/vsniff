"""Bridge resolver + worker: pinning, offsets, RSS and downloads (all faked)."""
import os
import threading

import pytest

pytest.importorskip("opencc")

from vsniff.api import Download, StreamInfo  # noqa: E402
from vsniff.bridge import worker as worker_mod  # noqa: E402
from vsniff.bridge.matching import Show  # noqa: E402
from vsniff.bridge.resolver import Resolver, estimated_size, release_title  # noqa: E402
from vsniff.bridge.store import Store  # noqa: E402
from vsniff.sites import SearchResult  # noqa: E402

SHOW = Show(100, "Tigers Sniff the Rose", 2026, ("尚公主",), False, {1: 40})
CQ = "https://chinaq.net/voddetail/1104.html"
DQ = "https://dramasq.io/detail/202665685.html"


class FakeMetadata:
    def __init__(self, show=SHOW):
        self._show = show

    def show(self, tvdb_id):
        return self._show if tvdb_id == self._show.tvdb_id else None


class FakeAccess:
    def __init__(self, results, catalogs, probes):
        self.results, self.catalogs, self.probes = results, catalogs, probes
        self.probed = []

    def search(self, query, sites=None):
        return [r for r in self.results if not sites or r.site in sites]

    def catalog(self, url):
        if url not in self.catalogs:
            raise RuntimeError("404")
        return self.catalogs[url]

    def probe(self, episode_url):
        self.probed.append(episode_url)
        entry = next(u for u, cat in self.catalogs.items() if episode_url in cat.values())
        return self.probes[entry]


def eps(base, n, start=1):
    return {e: f"{base}/ep{e}" for e in range(start, start + n)}


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "b.db"))
    access = FakeAccess(
        results=[SearchResult("chinaq", "尚公主", CQ, "陸劇 更新至34集", 34),
                 SearchResult("dramasq", "尚公主", DQ, "第35集", 35)],
        catalogs={CQ: eps(CQ, 34), DQ: eps(DQ, 35)},
        probes={CQ: StreamInfo("XYun", "720p", 2700.0, False),
                DQ: StreamInfo("GYun", "1080p", 2700.0, False)})
    return store, access, Resolver(store, FakeMetadata(), access)


def test_release_title_and_size():
    assert release_title("Show", 1, 7, "1080p", "dramasq") == "Show S01E07 1080p WEB-DL AAC-dramasq"
    assert release_title("Show", 1, 7, "unknown", "x") == "Show S01E07 WEB-DL AAC-x"
    assert estimated_size("1080p", 2700) == 45 * 15 * 1024 * 1024
    assert estimated_size("unknown", 2700) == 0 and estimated_size("720p", None) == 0


def test_browse_offers_every_source_with_the_episode(setup):
    _store, _access, resolver = setup
    got = resolver.releases(100, 1, 35)  # only dramasq has episode 35 so far
    assert [(r.site, r.episode, r.resolution) for r in got] == [("dramasq", 35, "1080p")]
    both = resolver.releases(100, 1, 7)
    assert sorted(r.site for r in both) == ["chinaq", "dramasq"]


def test_probes_are_cached_per_entry(setup):
    _store, access, resolver = setup
    resolver.releases(100, 1, 7)
    resolver.releases(100, 1, 8)
    assert len(access.probed) == 2  # once per entry, not per episode


def test_auto_pins_the_best_quality_source_and_sticks_to_it(setup):
    store, _access, resolver = setup
    got = resolver.releases(100, 1, 7, mode="auto")
    assert [r.site for r in got] == ["dramasq"]  # 1080p beats chinaq's 720p
    pin = store.pin(100, 1)
    assert pin.site == "dramasq" and pin.entry_url == DQ and "1080p" in pin.reason
    assert [r.site for r in resolver.releases(100, 1, 8, mode="auto")] == ["dramasq"]


def test_a_vanished_pinned_entry_is_replaced(setup):
    store, access, resolver = setup
    resolver.releases(100, 1, 7, mode="auto")
    del access.catalogs[DQ]  # dramasq took it down
    assert [r.site for r in resolver.releases(100, 1, 7, mode="auto")] == ["chinaq"]
    assert store.pin(100, 1).site == "chinaq"


def test_a_manual_grab_repins_the_season(setup):
    store, _access, resolver = setup
    resolver.releases(100, 1, 7, mode="auto")
    chinaq_release = next(r for r in resolver.releases(100, 1, 7) if r.site == "chinaq")
    resolver.repin(chinaq_release)
    assert store.pin(100, 1).entry_url == CQ


def test_rss_lists_the_newest_pinned_episodes(setup):
    _store, _access, resolver = setup
    resolver.releases(100, 1, 1, mode="auto")
    assert [r.episode for r in resolver.recent(per_pin=3)] == [33, 34, 35]


def test_unknown_shows_and_seasons_give_nothing(setup):
    _store, _access, resolver = setup
    assert resolver.releases(999, 1, 1) == []
    assert resolver.releases(100, 2, 1) == []


def test_combined_entry_maps_episodes_through_the_offset(tmp_path):
    show = Show(200, "Tegami Bachi", 2009, ("信蜂",), True, {1: 25, 2: 25})
    url = "https://www.hkanime.com/play/信蜂/304"
    access = FakeAccess([SearchResult("hkanime", "信蜂", url, "全集完 共50集", 50)],
                        {url: eps(url, 50)}, {url: StreamInfo("hkanime", "720p", 1490.0, False)})
    resolver = Resolver(Store(str(tmp_path / "b.db")), FakeMetadata(show), access)
    (r,) = resolver.releases(200, 2, 1)
    assert (r.season, r.episode, r.site_episode, r.episode_url) == (2, 1, 26, f"{url}/ep26")


def test_delisted_entries_offer_nothing(setup):
    _store, access, resolver = setup
    access.catalogs.clear()
    assert resolver.releases(100, 1, 7) == []


# ---- worker ------------------------------------------------------------------ #
class FakeSession:
    def __init__(self, fail=None):
        self.fail, self.closed = fail, False

    def download_episode(self, url, out_dir, *, series, season, episode, on_progress=None, **kw):
        if self.fail:
            raise self.fail
        on_progress(10.0, 20.0)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{series} - S{season:02d}E{episode:02d} - WEBDL - 1080p.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\0" * 1000)
        return Download(path, 1000, "1080p", 2700.0, "GYun", None)

    def close(self):
        self.closed = True


def run_one(store, resolver, tmp_path, session, deleted=False):
    release = resolver.releases(100, 1, 7, mode="auto")[0]
    job = store.add_job("SABnzbd_nzo_x", release.id, release.title, "tv", 0)
    if deleted:
        store.update_job(job.nzo_id, deleted=1)
    w = worker_mod.Worker(store, resolver, str(tmp_path / "complete"), threading.Event())
    w._session = session
    w.process(store.next_queued() if not deleted else store.job(job.nzo_id))
    return store.job(job.nzo_id), release


def test_worker_completes_a_job_into_its_named_folder(setup, tmp_path):
    store, _access, resolver = setup
    job, release = run_one(store, resolver, tmp_path, FakeSession())
    folder = os.path.join(str(tmp_path / "complete"), "tv", release.title)
    assert (job.status, job.storage, job.size_bytes, job.source) == ("Completed", folder, 1000, "GYun")
    assert os.listdir(folder) == ["Tigers Sniff the Rose - S01E07 - WEBDL - 1080p.mp4"]


def test_worker_reports_failures_and_cleans_up(setup, tmp_path):
    store, _access, resolver = setup
    session = FakeSession(fail=RuntimeError("ffmpeg failed:\nServer returned 403"))
    job, release = run_one(store, resolver, tmp_path, session)
    assert job.status == "Failed" and job.fail_message == "Server returned 403"
    assert session.closed  # a fresh browser for the next job
    assert not os.path.exists(os.path.join(str(tmp_path / "complete"), "tv", release.title))


def test_worker_discards_a_job_sonarr_removed_meanwhile(setup, tmp_path):
    store, _access, resolver = setup
    job, release = run_one(store, resolver, tmp_path, FakeSession(), deleted=True)
    assert job.status != "Completed"
    assert not os.path.exists(os.path.join(str(tmp_path / "complete"), "tv", release.title))


def test_safe_folder_names():
    assert worker_mod.safe_folder('A/B: "C"?') == "A B C"
    assert worker_mod.safe_folder("...") == "vsniff-job"


def test_interrupted_downloads_are_requeued(setup):
    store, _access, resolver = setup
    release = resolver.releases(100, 1, 7)[0]
    store.add_job("n1", release.id, release.title, "tv", 0)
    store.next_queued()
    store.requeue_interrupted()
    assert store.job("n1").status == "Queued"


def test_size_uses_sonarrs_episode_length_over_the_probe(tmp_path):
    show = Show(100, "Tigers Sniff the Rose", 2026, ("尚公主",), False, {1: 40}, runtime=45)
    access = FakeAccess([SearchResult("dramasq", "尚公主", DQ, "第35集", 35)],
                        {DQ: eps(DQ, 35)}, {DQ: StreamInfo("GYun", "1080p", 82 * 60.0, False)})
    (r,) = Resolver(Store(str(tmp_path / "b.db")), FakeMetadata(show), access).releases(100, 1, 7)
    assert r.size == estimated_size("1080p", 45 * 60)


# ---- forgetting deleted shows ---------------------------------------------------- #
def test_forget_show_drops_every_pin_of_the_show(setup):
    store, _access, resolver = setup
    resolver.releases(100, 1, 7, mode="auto")
    store.set_pin(100, 2, "dramasq", DQ, "尚公主", 0, "test")
    store.set_pin(555, 1, "dramasq", DQ, "other", 0, "test")
    assert resolver.forget_show(100, "deleted") == 2
    assert [(p.tvdb_id, p.season) for p in store.pins()] == [(555, 1)]


def test_rss_drops_pins_of_shows_sonarr_no_longer_has(setup):
    store, _access, resolver = setup
    store.set_pin(999, 1, "dramasq", DQ, "gone", 0, "test")  # FakeMetadata only knows 100
    resolver.recent()
    assert store.pin(999, 1) is None


def test_rss_keeps_pins_when_sonarr_is_unreachable(setup):
    store, _access, resolver = setup
    store.set_pin(100, 1, "dramasq", DQ, "尚公主", 0, "test")

    class Down:
        def show(self, tvdb_id):
            raise OSError("connection refused")

    resolver.metadata = Down()
    assert resolver.recent() == []
    assert store.pin(100, 1) is not None


def test_a_show_missing_from_sonarr_is_not_cached():
    from vsniff.bridge.metadata import Metadata

    class FakeSonarr:
        def __init__(self):
            self.series = None

        def series_by_tvdb(self, tvdb_id):
            return self.series

        def episodes(self, series_id):
            return [{"seasonNumber": 1, "episodeNumber": 1}]

    sonarr = FakeSonarr()
    md = Metadata(sonarr, None)
    assert md.show(461400) is None
    sonarr.series = {"id": 66, "title": "When I Meet the Moon", "year": 2026, "genres": []}
    assert md.show(461400).title == "When I Meet the Moon"  # re-added: found at once
    md.forget(461400)
    sonarr.series = None
    assert md.show(461400) is None
