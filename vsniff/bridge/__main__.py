"""`vsniff-bridge` / `python -m vsniff.bridge`: run the Sonarr bridge service."""
import argparse
import logging


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vsniff-bridge",
        description="Serve vsniff to Sonarr as a Newznab indexer + SABnzbd download client. "
                    "Configured by environment variables (see vsniff/bridge/config.py).")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8990)
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)

    import uvicorn

    from .app import create_app
    from .config import Config

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    summarize(cfg, args.port)
    # No access log: Sonarr puts the API key in every request's query string.
    uvicorn.run(create_app(cfg), host=args.host, port=args.port,
                log_level=args.log_level, access_log=False)
    return 0


def summarize(cfg, port):
    """Log where everything is and whether Sonarr/TMDB answer, at startup."""
    import os

    from .metadata import Sonarr, Tmdb

    log = logging.getLogger("vsniff.bridge")
    key_from = ("VSNIFF_BRIDGE_API_KEY" if os.environ.get("VSNIFF_BRIDGE_API_KEY")
                else os.environ.get("VSNIFF_BRIDGE_API_KEY_FILE")
                or os.path.join(cfg.data_dir, "api-key"))
    log.info("status page    http://<this host>:%s/", port)
    log.info("api key        %s  (Sonarr: indexer + download client)", key_from)
    log.info("database       %s", os.path.join(cfg.data_dir, "bridge.db"))
    log.info("downloads      %s/<category>/<release>/", cfg.complete_dir)
    try:
        Sonarr(cfg.sonarr_url, cfg.sonarr_api_key)._get("system/status")
        log.info("sonarr         %s  ok", cfg.sonarr_url)
    except Exception as e:  # noqa: BLE001 - report, keep serving
        log.error("sonarr         %s  NOT reachable: %s", cfg.sonarr_url, e)
    if not cfg.tmdb_api_key:
        log.warning("tmdb           no key: only Sonarr's names are searched (no Chinese titles)")
    else:
        try:
            Tmdb(cfg.tmdb_api_key)._get("configuration")
            log.info("tmdb           ok")
        except Exception as e:  # noqa: BLE001
            log.error("tmdb           NOT working: %s", e)


if __name__ == "__main__":
    raise SystemExit(main())
