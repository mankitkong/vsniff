"""Bridge HTTP service: the Newznab and SABnzbd answers Sonarr parses."""
import re
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("opencc")

from fastapi.testclient import TestClient  # noqa: E402

from vsniff.bridge import sabnzbd  # noqa: E402
from vsniff.bridge.app import create_app  # noqa: E402
from vsniff.bridge.config import Config  # noqa: E402
from vsniff.bridge.store import Store  # noqa: E402

KEY = "k" * 32
NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}


class FakeResolver:
    def __init__(self, store):
        self.store, self.calls, self.repinned = store, [], []

    def _rel(self, season, episode, site="dramasq"):
        return self.store.save_release(
            tvdb_id=100, season=season, episode=episode, site=site,
            entry_url=f"https://{site}/detail/1.html", entry_title="尚公主",
            site_episode=episode, episode_url=f"https://{site}/vodplay/1/ep{episode}.html",
            episode_offset=0,
            title=f"Tigers Sniff the Rose S{season:02d}E{episode:02d} 1080p WEB-DL AAC-{site}",
            resolution="1080p", size=700 * 1024 * 1024)

    def releases(self, tvdb_id, season, episode=None, mode="browse"):
        self.calls.append((tvdb_id, season, episode, mode))
        return [self._rel(season, episode or 1)]

    def recent(self):
        return []

    def repin(self, release):
        self.repinned.append(release.id)


@pytest.fixture
def bridge(tmp_path):
    cfg = Config(api_key=KEY, sonarr_url="http://sonarr", sonarr_api_key="s", tmdb_api_key="",
                 data_dir=str(tmp_path / "data"), complete_dir=str(tmp_path / "complete"))
    store = Store(str(tmp_path / "bridge.db"))
    resolver = FakeResolver(store)
    app = create_app(cfg, store=store, resolver=resolver, start_workers=False)
    with TestClient(app) as client:
        yield client, store, resolver, cfg


def nz(client, **params):
    r = client.get("/api", params={"apikey": KEY, **params})
    assert r.status_code == 200  # Sonarr treats non-2xx as a transport error
    assert "html" not in r.headers["content-type"]  # "responded with html content"
    return ET.fromstring(r.content)


# ---- Newznab ---------------------------------------------------------------- #
def test_caps_advertise_id_based_tv_search(bridge):
    client, *_ = bridge
    caps = nz(client, t="caps")
    assert caps.tag == "caps"
    limits = caps.find("limits")
    assert limits.get("default") == "100" and limits.get("max") == "100"
    tv = caps.find("searching/tv-search")
    assert tv.get("available") == "yes"
    assert tv.get("supportedParams") == "tvdbid,season,ep"  # comma-separated, no spaces
    assert {c.get("id") for c in caps.iter("subcat")} >= {"5030", "5040", "5070"}


def test_bad_api_key_is_a_newznab_auth_error(bridge):
    client, *_ = bridge
    r = client.get("/api", params={"t": "caps", "apikey": "wrong"})
    err = ET.fromstring(r.content)
    assert err.tag == "error" and 100 <= int(err.get("code")) <= 199 and err.get("description")


def test_episode_search_items_have_what_sonarr_reads(bridge):
    client, _store, resolver, _cfg = bridge
    rss = nz(client, t="tvsearch", cat="5030,5040", extended="1", tvdbid="100",
             season="1", ep="7", offset="0", limit="100")
    assert resolver.calls == [(100, 1, 7, "browse")]
    (item,) = rss.findall("channel/item")
    assert item.findtext("title") == "Tigers Sniff the Rose S01E07 1080p WEB-DL AAC-dramasq"
    assert item.findtext("pubDate")  # required by Sonarr's parser
    enc = item.find("enclosure")
    assert enc.get("type") == "application/x-nzb" and int(enc.get("length")) > 0
    assert re.search(r"/nzb/[0-9a-f]{16}\?apikey=", enc.get("url"))
    attrs = {a.get("name"): a.get("value") for a in item.findall("newznab:attr", NS)}
    assert attrs["size"] == str(700 * 1024 * 1024) and attrs["tvdbid"] == "100"
    assert attrs["language"] == "Chinese"


def test_auto_indexer_asks_for_pinned_sources_only(bridge):
    client, _store, resolver, _cfg = bridge
    nz(client, t="tvsearch", tvdbid="100", season="1", ep="2", vsniff="auto")
    assert resolver.calls[-1] == (100, 1, 2, "auto")


def test_season_search_and_daily_episodes(bridge):
    client, _store, resolver, _cfg = bridge
    nz(client, t="tvsearch", tvdbid="100", season="2")
    assert resolver.calls[-1] == (100, 2, None, "browse")
    rss = nz(client, t="tvsearch", tvdbid="100", season="2026", ep="03/14")
    assert rss.findall("channel/item") == []


def test_rss_with_nothing_pinned_still_passes_sonarrs_test(bridge):
    client, *_ = bridge
    items = nz(client, t="tvsearch", cat="5030,5040", extended="1").findall("channel/item")
    assert len(items) == 1 and "not a real release" in items[0].findtext("title")


def test_later_pages_are_empty(bridge):
    client, *_ = bridge
    assert nz(client, t="tvsearch", tvdbid="100", season="1", offset="100").findall(
        "channel/item") == []


def test_title_only_searches_return_nothing(bridge):
    client, _store, resolver, _cfg = bridge
    assert nz(client, t="tvsearch", q="Tigers", season="1").findall("channel/item") == []
    assert resolver.calls == []


# ---- grab: nzb -> addfile -> queue -> history -------------------------------- #
def grab(client, cat="tv"):
    rss = nz(client, t="tvsearch", tvdbid="100", season="1", ep="7")
    url = rss.find("channel/item/enclosure").get("url")
    nzb = client.get(url[url.index("/nzb/"):])
    assert nzb.status_code == 200
    root = ET.fromstring(nzb.content)
    assert root.tag.endswith("nzb") and root.find("{*}file") is not None  # Sonarr's check
    r = client.post("/api", params={"mode": "addfile", "cat": cat, "priority": "-100",
                                    "apikey": KEY, "output": "json"},
                    files={"name": ("x.nzb", nzb.content, "application/x-nzb")})
    body = r.json()
    assert body["status"] is True and len(body["nzo_ids"]) == 1
    return body["nzo_ids"][0]


def sab(client, **params):
    return client.get("/api", params={"apikey": KEY, "output": "json", **params}).json()


def test_version_and_config_pass_sonarrs_checks(bridge):
    client, _store, _resolver, cfg = bridge
    assert re.match(r"\d+\.\d+\.\d+", sab(client, mode="version")["version"])
    conf = sab(client, mode="get_config")["config"]
    misc = conf["misc"]
    assert misc["complete_dir"] == cfg.complete_dir
    assert not (misc["enable_tv_sorting"] or misc["enable_movie_sorting"]
                or misc["enable_date_sorting"])
    assert misc["history_retention_option"] == "all"
    assert isinstance(misc["tv_categories"], list)
    names = {c["name"]: c["dir"] for c in conf["categories"]}
    assert names["*"] == "" and names["tv"] == "tv"
    assert not any(d.endswith("*") for d in names.values())


def test_bad_key_on_the_download_client(bridge):
    client, *_ = bridge
    r = client.get("/api", params={"mode": "version", "apikey": "nope", "output": "json"})
    assert r.json() == {"status": False, "error": "API Key Incorrect"}


def test_grabbed_release_appears_in_the_queue(bridge):
    client, _store, _resolver, _cfg = bridge
    nzo = grab(client)
    (slot,) = sab(client, mode="queue", start="0", limit="0", category="tv")["queue"]["slots"]
    assert slot["nzo_id"] == nzo and slot["cat"] == "tv" and slot["status"] == "Queued"
    assert slot["filename"] == "Tigers Sniff the Rose S01E07 1080p WEB-DL AAC-dramasq"
    assert re.fullmatch(r"\d+:\d{2}:\d{2}", slot["timeleft"])
    int(slot["percentage"]), int(slot["index"]), float(slot["mb"]), float(slot["mbleft"])
    assert sab(client, mode="queue", category="movies")["queue"]["slots"] == []


def test_completed_job_reports_its_folder_in_history(bridge):
    client, store, _resolver, _cfg = bridge
    nzo = grab(client)
    store.update_job(nzo, status="Completed", storage="/media/downloads/vsniff/tv/X",
                     size_bytes=123, finished_at=1.0, started_at=0.0)
    (slot,) = sab(client, mode="history", start="0", limit="60", category="tv")["history"]["slots"]
    assert (slot["status"], slot["storage"], slot["bytes"]) == (
        "Completed", "/media/downloads/vsniff/tv/X", 123)
    assert slot["name"] == "Tigers Sniff the Rose S01E07 1080p WEB-DL AAC-dramasq"
    assert sab(client, mode="queue", category="tv")["queue"]["slots"] == []


def test_failed_job_carries_its_message(bridge):
    client, store, _resolver, _cfg = bridge
    nzo = grab(client)
    store.update_job(nzo, status="Failed", fail_message="no working source", finished_at=1.0)
    (slot,) = sab(client, mode="history", category="tv")["history"]["slots"]
    assert slot["status"] == "Failed" and slot["fail_message"] == "no working source"


def test_delete_hides_a_job(bridge):
    client, *_ = bridge
    nzo = grab(client)
    assert sab(client, mode="queue", name="delete", del_files="1", value=nzo)["status"] is True
    assert sab(client, mode="queue", category="tv")["queue"]["slots"] == []


def test_sabnzbd_also_answers_under_its_url_base(bridge):
    client, *_ = bridge
    r = client.get("/sabnzbd/api", params={"mode": "version", "apikey": KEY, "output": "json"})
    assert r.json()["version"] == sabnzbd.VERSION


def test_addfile_rejects_an_nzb_the_bridge_did_not_make(bridge):
    client, *_ = bridge
    r = client.post("/api", params={"mode": "addfile", "cat": "tv", "apikey": KEY},
                    files={"name": ("x.nzb", b"<nzb><file/></nzb>", "application/x-nzb")})
    assert r.json()["status"] is False


def test_nzb_needs_the_key_and_a_known_release(bridge):
    client, *_ = bridge
    assert client.get("/nzb/0123456789abcdef").status_code == 403
    assert client.get("/nzb/0123456789abcdef", params={"apikey": KEY}).status_code == 404


def test_status_page_renders(bridge):
    client, *_ = bridge
    grab(client)
    r = client.get("/")
    assert r.status_code == 200 and "Tigers Sniff the Rose S01E07" in r.text
