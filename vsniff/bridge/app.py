"""The bridge's HTTP service: Newznab + SABnzbd endpoints for Sonarr.

    /api?t=...            Newznab (indexer) — caps, tvsearch
    /api?mode=...         SABnzbd (download client) — also at /sabnzbd/api
    /nzb/<release id>     the placeholder .nzb Sonarr fetches when it grabs
    /                     read-only status page: pins and recent jobs

Add the bridge to Sonarr as a Newznab indexer twice — "browse" (no tags,
interactive search only) and "auto" (tagged, all searches, Additional
Parameters "&vsniff=auto") — and once as a SABnzbd download client.
"""
import html
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import newznab, sabnzbd
from .access import SiteAccess
from .config import Config
from .metadata import Metadata, Sonarr, Tmdb
from .resolver import Resolver
from .store import Store
from .worker import Worker

log = logging.getLogger("vsniff.bridge")
XML = "application/xml; charset=utf-8"
RELEASE_IN_NZB = re.compile(r'<meta type="vsniff-release">([0-9a-f]{16})</meta>')


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def create_app(cfg: Config, *, store: Store = None, resolver: Resolver = None,
               start_workers=True) -> FastAPI:
    os.makedirs(cfg.data_dir, exist_ok=True)
    for c in cfg.categories:
        os.makedirs(os.path.join(cfg.complete_dir, c), exist_ok=True)
    store = store or Store(os.path.join(cfg.data_dir, "bridge.db"))
    access = None
    if resolver is None:
        access = SiteAccess(min_interval=cfg.search_interval)
        tmdb = Tmdb(cfg.tmdb_api_key) if cfg.tmdb_api_key else None
        resolver = Resolver(store, Metadata(Sonarr(cfg.sonarr_url, cfg.sonarr_api_key), tmdb),
                            access)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        store.requeue_interrupted()
        if start_workers:
            for _ in range(max(1, cfg.workers)):
                Worker(store, resolver, cfg.complete_dir, stop).start()
        yield
        stop.set()
        if access is not None:
            access.close()

    app = FastAPI(title="vsniff bridge", docs_url=None, redoc_url=None, lifespan=lifespan)

    def authorized(params):
        return secrets.compare_digest(params.get("apikey", ""), cfg.api_key)

    # ---------------- Newznab ---------------- #
    def newznab_api(request: Request):
        q = request.query_params
        if not authorized(q):
            return Response(newznab.error_xml(100, "Incorrect user credentials"), media_type=XML)
        t = q.get("t", "")
        if t == "caps":
            return Response(newznab.caps_xml(), media_type=XML)
        if t not in ("tvsearch", "search"):
            return Response(newznab.error_xml(202, f"No such function ({t})"), media_type=XML)
        mode = "auto" if q.get("vsniff") == "auto" else "browse"
        offset = _int(q.get("offset")) or 0
        tvdb_id, season = _int(q.get("tvdbid")), _int(q.get("season"))
        if offset:
            releases = []  # everything fits on the first page
        elif tvdb_id is not None and season is not None:
            ep = q.get("ep")
            episode = _int(ep) if ep else None
            if ep and episode is None:  # daily shows send ep=MM/dd: not supported
                releases = []
            else:
                releases = resolver.releases(tvdb_id, season, episode, mode=mode)
        elif tvdb_id is None and not q.get("q"):
            releases = resolver.recent() if mode == "auto" else []
            if not releases:
                # Sonarr's "Test" button needs one item from this request.
                releases = [placeholder()]
        else:
            releases = []  # title-only searches: matching needs the show's ids
        base = str(request.base_url).rstrip("/")
        feed = newznab.feed_xml(releases[:100],
                                lambda r: f"{base}/nzb/{r.id}?apikey={cfg.api_key}", offset)
        return Response(feed, media_type=XML)

    # ---------------- SABnzbd ---------------- #
    async def sab_api(request: Request):
        q = dict(request.query_params)
        if request.method == "POST":
            form = await request.form()
            q.update({k: v for k, v in form.items() if isinstance(v, str)})
        else:
            form = None
        if not authorized(q):
            return JSONResponse(sabnzbd.error("API Key Incorrect"))
        mode = q.get("mode")
        if mode == "version":
            return JSONResponse({"version": sabnzbd.VERSION})
        if mode == "get_config":
            return JSONResponse(sabnzbd.config(cfg.complete_dir, cfg.categories))
        if mode == "fullstatus":
            return JSONResponse({"status": {"completedir": cfg.complete_dir}})
        if mode == "addfile":
            return JSONResponse(await addfile(q, form))
        if mode == "queue":
            if q.get("name") == "delete":
                return JSONResponse(delete(q.get("value", "")))
            return JSONResponse(queue(q.get("category")))
        if mode == "history":
            if q.get("name") == "delete":
                return JSONResponse(delete(q.get("value", "")))
            return JSONResponse(history(q.get("category")))
        return JSONResponse(sabnzbd.error(f"not supported: {mode}"))

    async def addfile(q, form):
        upload = form.get("name") if form is not None else None
        if upload is None or isinstance(upload, str):
            return sabnzbd.error("no file")
        m = RELEASE_IN_NZB.search((await upload.read()).decode("utf-8", "replace"))
        release = store.release(m.group(1)) if m else None
        if release is None:
            return sabnzbd.error("not an nzb from this bridge")
        nzo_id = f"SABnzbd_nzo_{secrets.token_hex(6)}"
        store.add_job(nzo_id, release.id, release.title, q.get("cat") or "*",
                      _int(q.get("priority")) or 0)
        log.info("queued %s as %s", release.title, nzo_id)
        return {"status": True, "nzo_ids": [nzo_id]}

    def queue(category):
        jobs = store.jobs(("Queued", "Downloading"), category)
        slots = []
        for i, job in enumerate(jobs):
            release = store.release(job.release_id)
            slots.append(sabnzbd.queue_slot(job, release.size if release else 0, i))
        return {"queue": {"paused": False, "slots": slots}}

    def history(category):
        jobs = store.jobs(("Completed", "Failed"), category)
        jobs.sort(key=lambda j: j.finished_at or 0, reverse=True)
        return {"history": {"slots": [sabnzbd.history_slot(j) for j in jobs[:200]]}}

    def delete(value):
        for nzo_id in filter(None, value.split(",")):
            store.update_job(nzo_id, deleted=1)
        return {"status": True}

    def placeholder():
        return store.save_release(
            tvdb_id=0, season=0, episode=0, site="vsniff", entry_url="vsniff:status",
            entry_title="vsniff bridge", site_episode=0, episode_url="vsniff:status",
            episode_offset=0, title="vsniff bridge is up (not a real release)",
            resolution="unknown", size=0)

    @app.api_route("/api", methods=["GET", "POST"])
    async def api_route(request: Request):
        if "mode" in request.query_params or request.method == "POST":
            return await sab_api(request)
        return await _threaded(newznab_api, request)

    @app.api_route("/sabnzbd/api", methods=["GET", "POST"])
    async def sab_route(request: Request):
        return await sab_api(request)

    @app.get("/nzb/{rid}")
    def nzb(rid: str, request: Request):
        if not authorized(request.query_params):
            return Response("forbidden", status_code=403)
        release = store.release(rid)
        if release is None or release.tvdb_id == 0:
            return Response("unknown release", status_code=404)
        return Response(newznab.nzb_xml(release.id, release.title),
                        media_type="application/x-nzb",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{release.id}.nzb"'})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def status():
        return status_page(store)

    return app


async def _threaded(fn, *args):
    """Run blocking work (site searches, probes) off the event loop."""
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(fn, *args)


def status_page(store: Store) -> str:
    e = html.escape
    pins = "".join(
        f"<tr><td>{p.tvdb_id}</td><td>S{p.season:02d}</td><td>{e(p.site)}</td>"
        f"<td><a href='{e(p.entry_url)}'>{e(p.entry_title)}</a></td><td>{p.episode_offset}</td>"
        f"<td>{e(p.reason)}</td><td>{time.strftime('%Y-%m-%d', time.localtime(p.pinned_at))}</td></tr>"
        for p in store.pins())
    jobs = "".join(
        f"<tr><td>{e(j.name)}</td><td>{e(j.status)}</td><td>{e(j.source)}</td>"
        f"<td>{e(j.fail_message)}</td>"
        f"<td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(j.added_at))}</td></tr>"
        for j in store.jobs(("Queued", "Downloading", "Completed", "Failed"))[-50:][::-1])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>vsniff bridge</title>
<style>body{{font:14px system-ui,sans-serif;margin:16px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%;margin-bottom:24px}}
td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}}
@media (prefers-color-scheme:dark){{body{{color:#ddd;background:#111}}a{{color:#8ab4f8}}
td,th{{border-color:#333}}}}</style></head><body>
<h1>vsniff bridge</h1>
<h2>Pinned sources</h2><table><tr><th>TVDB</th><th>Season</th><th>Site</th><th>Entry</th>
<th>Offset</th><th>Why</th><th>Pinned</th></tr>{pins or '<tr><td colspan=7>none yet</td></tr>'}</table>
<h2>Recent jobs</h2><table><tr><th>Release</th><th>Status</th><th>Source</th><th>Error</th>
<th>Added</th></tr>{jobs or '<tr><td colspan=5>none yet</td></tr>'}</table></body></html>"""
