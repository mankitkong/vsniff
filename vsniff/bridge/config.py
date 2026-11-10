"""Bridge settings, from environment variables.

Secrets can be given directly or, better, as a path to a file holding them
(the *_FILE form), so they never appear in docker-compose.yml.

    VSNIFF_BRIDGE_API_KEY[_FILE]  key Sonarr must send (indexer + download client)
    SONARR_URL                    e.g. http://sonarr:8989
    SONARR_API_KEY[_FILE]         Sonarr's API key (Settings > General)
    TMDB_API_KEY[_FILE]           TMDB v3 API key, for Chinese titles
    VSNIFF_DATA_DIR               where bridge.db lives          (default /data)
    VSNIFF_COMPLETE_DIR           finished downloads, as Sonarr sees the path
                                  (default /media/downloads/vsniff)
    VSNIFF_CATEGORIES             download categories            (default tv,movies)
    VSNIFF_WORKERS                parallel downloads             (default 1)
    VSNIFF_SEARCH_INTERVAL        seconds between searches per site (default 2)
"""
import os
from dataclasses import dataclass
from typing import Tuple


def secret(name, required=True):
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read().strip()
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SystemExit(f"vsniff-bridge: set {name} or {name}_FILE")
    return value


@dataclass(frozen=True)
class Config:
    api_key: str
    sonarr_url: str
    sonarr_api_key: str
    tmdb_api_key: str
    data_dir: str = "/data"
    complete_dir: str = "/media/downloads/vsniff"
    categories: Tuple[str, ...] = ("tv", "movies")
    workers: int = 1
    search_interval: float = 2.0

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(
            api_key=secret("VSNIFF_BRIDGE_API_KEY"),
            sonarr_url=env("SONARR_URL", "http://sonarr:8989"),
            sonarr_api_key=secret("SONARR_API_KEY"),
            tmdb_api_key=secret("TMDB_API_KEY", required=False),
            data_dir=env("VSNIFF_DATA_DIR", "/data"),
            complete_dir=env("VSNIFF_COMPLETE_DIR", "/media/downloads/vsniff"),
            categories=tuple(c.strip() for c in env("VSNIFF_CATEGORIES", "tv,movies").split(",")
                             if c.strip()),
            workers=int(env("VSNIFF_WORKERS", "1")),
            search_interval=float(env("VSNIFF_SEARCH_INTERVAL", "2")),
        )
