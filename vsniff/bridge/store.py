"""The bridge's SQLite database: pins, offered releases, jobs, probe results.

- pins      the source chosen for a show's season; later episodes come from it
- releases  every release offered to Sonarr; the .nzb Sonarr fetches carries
            only a release id, so a grab is looked up here, never trusted
- jobs      downloads, in the shape Sonarr's SABnzbd client polls
- probes    resolution/length per site entry, so searches don't re-probe
"""
import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS pins (
    tvdb_id INTEGER NOT NULL, season INTEGER NOT NULL,
    site TEXT NOT NULL, entry_url TEXT NOT NULL, entry_title TEXT NOT NULL,
    episode_offset INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    pinned_at REAL NOT NULL,
    PRIMARY KEY (tvdb_id, season)
);
CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    tvdb_id INTEGER NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL,
    site TEXT NOT NULL, entry_url TEXT NOT NULL, entry_title TEXT NOT NULL,
    site_episode INTEGER NOT NULL, episode_url TEXT NOT NULL,
    episode_offset INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL, resolution TEXT NOT NULL, size INTEGER NOT NULL,
    first_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS releases_by_show ON releases (tvdb_id, season, episode);
CREATE TABLE IF NOT EXISTS jobs (
    nzo_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES releases(id),
    name TEXT NOT NULL, category TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,              -- Queued, Downloading, Completed, Failed
    done_seconds REAL NOT NULL DEFAULT 0, total_seconds REAL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    storage TEXT NOT NULL DEFAULT '', fail_message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL, started_at REAL, finished_at REAL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS probes (
    entry_url TEXT PRIMARY KEY,
    resolution TEXT NOT NULL, duration REAL, source TEXT NOT NULL,
    probed_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Pin:
    tvdb_id: int
    season: int
    site: str
    entry_url: str
    entry_title: str
    episode_offset: int
    reason: str
    pinned_at: float


@dataclass(frozen=True)
class Release:
    id: str
    tvdb_id: int
    season: int
    episode: int
    site: str
    entry_url: str
    entry_title: str
    site_episode: int
    episode_url: str
    episode_offset: int
    title: str
    resolution: str
    size: int
    first_seen: float


@dataclass(frozen=True)
class Job:
    nzo_id: str
    release_id: str
    name: str
    category: str
    priority: int
    status: str
    done_seconds: float
    total_seconds: Optional[float]
    size_bytes: int
    storage: str
    fail_message: str
    source: str
    added_at: float
    started_at: Optional[float]
    finished_at: Optional[float]
    deleted: int


@dataclass(frozen=True)
class Probe:
    entry_url: str
    resolution: str
    duration: Optional[float]
    source: str
    probed_at: float


def release_id(tvdb_id, season, episode, entry_url):
    """Stable id: the same episode from the same entry is the same release."""
    key = f"{tvdb_id}|{season}|{episode}|{entry_url}".encode()
    return hashlib.sha1(key).hexdigest()[:16]


class Store:
    """Thread-safe access to the bridge database (one connection, one lock)."""

    def __init__(self, path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._tx() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def _one(self, cls, sql, *args):
        with self._tx() as db:
            row = db.execute(sql, args).fetchone()
        return cls(**dict(row)) if row else None

    def _all(self, cls, sql, *args):
        with self._tx() as db:
            return [cls(**dict(r)) for r in db.execute(sql, args).fetchall()]

    # ---- pins ---- #
    def pin(self, tvdb_id, season) -> Optional[Pin]:
        return self._one(Pin, "SELECT * FROM pins WHERE tvdb_id=? AND season=?", tvdb_id, season)

    def pins(self) -> List[Pin]:
        return self._all(Pin, "SELECT * FROM pins ORDER BY tvdb_id, season")

    def set_pin(self, tvdb_id, season, site, entry_url, entry_title, episode_offset, reason):
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO pins VALUES (?,?,?,?,?,?,?,?)",
                       (tvdb_id, season, site, entry_url, entry_title, episode_offset,
                        reason, time.time()))
        return self.pin(tvdb_id, season)

    def drop_pin(self, tvdb_id, season):
        with self._tx() as db:
            db.execute("DELETE FROM pins WHERE tvdb_id=? AND season=?", (tvdb_id, season))

    def drop_pins(self, tvdb_id) -> int:
        """Forget every season's pin for a show; returns how many were dropped."""
        with self._tx() as db:
            return db.execute("DELETE FROM pins WHERE tvdb_id=?", (tvdb_id,)).rowcount

    # ---- releases ---- #
    def save_release(self, **fields) -> Release:
        """Insert or refresh a release, keeping its first_seen (RSS ordering)."""
        rid = release_id(fields["tvdb_id"], fields["season"], fields["episode"],
                         fields["entry_url"])
        with self._tx() as db:
            old = db.execute("SELECT first_seen FROM releases WHERE id=?", (rid,)).fetchone()
            first_seen = old["first_seen"] if old else time.time()
            row = dict(fields, id=rid, first_seen=first_seen)
            cols = ",".join(row)
            db.execute(f"INSERT OR REPLACE INTO releases ({cols}) VALUES "
                       f"({','.join('?' * len(row))})", tuple(row.values()))
        return self.release(rid)

    def release(self, rid) -> Optional[Release]:
        return self._one(Release, "SELECT * FROM releases WHERE id=?", rid)

    def recent_releases(self, limit) -> List[Release]:
        return self._all(Release, "SELECT * FROM releases ORDER BY first_seen DESC LIMIT ?", limit)

    # ---- jobs ---- #
    def add_job(self, nzo_id, release_id, name, category, priority) -> Job:
        with self._tx() as db:
            db.execute("INSERT INTO jobs (nzo_id, release_id, name, category, priority, "
                       "status, added_at) VALUES (?,?,?,?,?,'Queued',?)",
                       (nzo_id, release_id, name, category, priority, time.time()))
        return self.job(nzo_id)

    def job(self, nzo_id) -> Optional[Job]:
        return self._one(Job, "SELECT * FROM jobs WHERE nzo_id=?", nzo_id)

    def jobs(self, statuses, category=None) -> List[Job]:
        marks = ",".join("?" * len(statuses))
        sql = f"SELECT * FROM jobs WHERE deleted=0 AND status IN ({marks})"
        args = list(statuses)
        if category is not None:
            sql += " AND category=?"
            args.append(category)
        return self._all(Job, sql + " ORDER BY added_at", *args)

    def update_job(self, nzo_id, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        with self._tx() as db:
            db.execute(f"UPDATE jobs SET {sets} WHERE nzo_id=?", (*fields.values(), nzo_id))

    def next_queued(self) -> Optional[Job]:
        """Claim the oldest queued job for a worker (marks it Downloading)."""
        with self._tx() as db:
            row = db.execute("SELECT nzo_id FROM jobs WHERE deleted=0 AND status='Queued' "
                             "ORDER BY priority DESC, added_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='Downloading', started_at=? WHERE nzo_id=?",
                       (time.time(), row["nzo_id"]))
        return self.job(row["nzo_id"])

    def requeue_interrupted(self):
        """Jobs left Downloading by a restart go back to the queue."""
        with self._tx() as db:
            db.execute("UPDATE jobs SET status='Queued', done_seconds=0 "
                       "WHERE status='Downloading'")

    # ---- probes ---- #
    def probe(self, entry_url) -> Optional[Probe]:
        return self._one(Probe, "SELECT * FROM probes WHERE entry_url=?", entry_url)

    def save_probe(self, entry_url, resolution, duration, source):
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO probes VALUES (?,?,?,?,?)",
                       (entry_url, resolution, duration, source, time.time()))
