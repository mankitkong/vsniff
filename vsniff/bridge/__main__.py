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
    # No access log: Sonarr puts the API key in every request's query string.
    uvicorn.run(create_app(Config.from_env()), host=args.host, port=args.port,
                log_level=args.log_level, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
