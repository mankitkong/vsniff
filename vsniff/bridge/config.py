"""Bridge settings, from environment variables.

Secrets can be given directly or, better, as a path to a file holding them
(the *_FILE form), so they never appear in docker-compose.yml.

    VSNIFF_BRIDGE_API_KEY[_FILE]  key Sonarr must send (indexer + download client);
                                  if unset, one is generated on first start and
                                  kept in <VSNIFF_DATA_DIR>/api-key
    SONARR_URL                    e.g. http://sonarr:8989
    SONARR_API_KEY[_FILE]         Sonarr's API key. The file may be Sonarr's own
                                  config.xml: the key is read from its <ApiKey>.
    TMDB_API_KEY[_FILE]           TMDB v3 API key, for Chinese titles
    VSNIFF_DATA_DIR               where bridge.db lives          (default /data)
    VSNIFF_COMPLETE_DIR           finished downloads, as Sonarr sees the path
                                  (default /media/downloads/vsniff)
    VSNIFF_CATEGORIES             download categories            (default tv,movies)
    VSNIFF_WORKERS                parallel downloads             (default 1)
    VSNIFF_SEARCH_INTERVAL        seconds between searches per site (default 2)
"""
import logging
import os
import re
import secrets
from dataclasses import dataclass
from typing import Tuple

log = logging.getLogger("vsniff.bridge")
API_KEY_RX = re.compile(r"<ApiKey>\s*([^<\s]+)\s*</ApiKey>")


def secret(name, required=True):
    """A secret from NAME, or from the file NAME_FILE points to.

    A file that is an *arr config.xml yields its <ApiKey>, so Sonarr's key can
    be read from Sonarr's own config instead of copied anywhere.
    """
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            text = fh.read().strip()
        m = API_KEY_RX.search(text)
        return m.group(1) if m else text
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SystemExit(f"vsniff-bridge: set {name} or {name}_FILE")
    return value


def bridge_api_key(data_dir):
    """The configured bridge key, or one generated once into <data_dir>/api-key."""
    configured = secret("VSNIFF_BRIDGE_API_KEY", required=False)
    if configured:
        return configured
    path = os.path.join(data_dir, "api-key")
    if not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(16) + "\n")
        log.info("generated the bridge API key in %s", path)
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


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
        data_dir = env("VSNIFF_DATA_DIR", "/data")
        return cls(
            api_key=bridge_api_key(data_dir),
            sonarr_url=env("SONARR_URL", "http://sonarr:8989"),
            sonarr_api_key=secret("SONARR_API_KEY"),
            tmdb_api_key=secret("TMDB_API_KEY", required=False),
            data_dir=data_dir,
            complete_dir=env("VSNIFF_COMPLETE_DIR", "/media/downloads/vsniff"),
            categories=tuple(c.strip() for c in env("VSNIFF_CATEGORIES", "tv,movies").split(",")
                             if c.strip()),
            workers=int(env("VSNIFF_WORKERS", "1")),
            search_interval=float(env("VSNIFF_SEARCH_INTERVAL", "2")),
        )
