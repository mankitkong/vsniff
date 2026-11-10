"""Download workers: take queued jobs and run vsniff on them.

Each worker thread owns its own vsniff Session (one headless browser), since
Playwright's sync API can't be shared across threads. A job's files land in
<complete_dir>/<category>/<job name>/, the folder Sonarr imports from.
"""
import logging
import os
import re
import shutil
import threading
import time

from .. import api

log = logging.getLogger("vsniff.bridge")

PROGRESS_EVERY = 5  # seconds between progress writes to the database


def safe_folder(name):
    """A job name as a single path component (no separators or oddities)."""
    cleaned = " ".join(re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name).split()).strip(" .")
    return cleaned or "vsniff-job"


def show_title(release):
    """The series name the release title starts with."""
    marker = f" S{release.season:02d}E{release.episode:02d}"
    return release.title.split(marker, 1)[0]


def job_folder(complete_dir, category, name):
    return os.path.join(complete_dir, safe_folder(category) if category != "*" else "",
                        safe_folder(name))


class Worker(threading.Thread):
    def __init__(self, store, resolver, complete_dir, stop: threading.Event, idle=3.0):
        super().__init__(daemon=True, name="vsniff-worker")
        self.store, self.resolver = store, resolver
        self.complete_dir, self.stop, self.idle = complete_dir, stop, idle
        self._session = None

    def run(self):
        try:
            while not self.stop.is_set():
                job = self.store.next_queued()
                if job is None:
                    self.stop.wait(self.idle)
                    continue
                self.process(job)
        finally:
            if self._session is not None:
                self._session.close()

    def process(self, job):
        release = self.store.release(job.release_id)
        folder = job_folder(self.complete_dir, job.category, job.name)
        if release is None:
            self._fail(job, "release unknown to the bridge")
            return
        log.info("downloading %s (%s)", job.name, release.episode_url)
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] >= PROGRESS_EVERY:
                last[0] = now
                self.store.update_job(job.nzo_id, done_seconds=done, total_seconds=total)

        try:
            if self._session is None:
                self._session = api.Session()
            done = self._session.download_episode(
                release.episode_url, folder, series=show_title(release),
                season=release.season, episode=release.episode, on_progress=progress)
        except Exception as e:  # noqa: BLE001 - any failure is reported to Sonarr
            shutil.rmtree(folder, ignore_errors=True)
            self._fail(job, str(e).strip().splitlines()[-1][:300] if str(e).strip() else repr(e))
            if self._session is not None:  # a broken browser shouldn't poison the next job
                self._session.close()
                self._session = None
            return
        if self.store.job(job.nzo_id).deleted:
            shutil.rmtree(folder, ignore_errors=True)
            log.info("%s finished after Sonarr removed it; deleted", job.name)
            return
        size = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder))
        self.store.update_job(job.nzo_id, status="Completed", storage=folder,
                              size_bytes=size, source=done.source,
                              done_seconds=done.duration or 0, total_seconds=done.duration,
                              finished_at=time.time())
        self.resolver.repin(release)
        log.info("completed %s -> %s", job.name, done.path)

    def _fail(self, job, message):
        log.warning("failed %s: %s", job.name, message)
        self.store.update_job(job.nzo_id, status="Failed", fail_message=message,
                              finished_at=time.time())
