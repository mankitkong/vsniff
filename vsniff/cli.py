"""Command line: single episodes, whole series (--all) and movies."""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

from .common import UA, VsniffError
from .discovery import discover_stream, discover_with_session
from .download import _fmt_time, download_stream, save_subtitles, subtitle_sidecar
from .naming import (build_filename, build_movie_filename, existing_episodes,
                     existing_movie, expand_out_dir, movie_name)
from .sites import pick_adapter


def filter_from(episodes, start):
    """Keep only episodes numbered >= start (inclusive). start=None keeps all."""
    if start is None:
        return list(episodes)
    return [e for e in episodes if e >= start]


def download_all(url, args):
    """Download every episode the site lists for a series into args.out.

    Returns 0 on full success, 1 if any episode failed.
    """
    adapter = pick_adapter(url)
    print(f"site: {adapter.name}")
    if not adapter.supports_batch:
        raise VsniffError("--all is only supported for chinaq.net, hkanime.com and dramasq.io")

    out_dir = expand_out_dir(args.out)
    os.makedirs(out_dir, exist_ok=True)

    downloaded, failed, present, subs_added = [], [], [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            by_ep = dict(adapter.catalog(page, ctx, url))
            available = sorted(by_ep)
            if not available:
                raise VsniffError("no episodes found for this series")

            wanted = filter_from(available, args.start)
            have = existing_episodes(out_dir, args.series, args.season)
            present = [e for e in wanted if e in have]
            missing = [e for e in wanted if e not in have]
            backfill = [e for e in present if not os.path.exists(
                subtitle_sidecar(os.path.join(out_dir, have[e]), args.sub_lang))]
            note = f", {len(backfill)} without subtitles" if backfill else ""
            print(f"catalog: {len(available)} episodes; "
                  f"{len(missing)} to download, {len(present)} already present"
                  f"{note}")

            for ep in missing:
                try:
                    ep_url = by_ep[ep]
                    print(f"episode {ep}: discovering...")
                    (label, m3u8, referer, resolution, duration,
                     subtitles) = discover_with_session(
                        adapter, page, ctx, ep_url, args.source, args.keep_ads)
                    fname = build_filename(
                        args.series, args.season, ep, args.quality, resolution)
                    out_path = os.path.join(out_dir, fname)
                    print(f"  -> {out_path}  (source={label}, {resolution})")
                    download_stream(m3u8, referer, out_path, duration)
                    if subtitles:
                        save_subtitles(subtitles, referer, out_path, args.sub_lang)
                    downloaded.append(ep)
                except Exception as e:
                    # Continue-on-failure: a single episode failing (no working
                    # source, ffmpeg error, or a browser/network error such as a
                    # Playwright timeout) must not abort the rest of the batch.
                    print(f"  episode {ep} failed: {e}", file=sys.stderr)
                    failed.append(ep)

            for ep in backfill:
                print(f"episode {ep}: already downloaded, fetching subtitles...")
                try:
                    (_lbl, _m3u8, referer, _res, _dur,
                     subtitles) = discover_with_session(
                        adapter, page, ctx, by_ep[ep], args.source,
                        keep_ads=True)  # video is already on disk
                    if not subtitles:
                        # Whether a series is packaged with a subtitle track is a
                        # property of the series, not of one episode (checked
                        # across 543 episodes of 5 series), so one miss means
                        # there is nothing to find in any of them. Bail out rather
                        # than re-discover hundreds of episodes to learn the same.
                        print("  no subtitle track on this series; "
                              "skipping the remaining episodes")
                        break
                    if save_subtitles(subtitles, referer,
                                      os.path.join(out_dir, have[ep]),
                                      args.sub_lang):
                        subs_added += 1
                except Exception as e:
                    # A missing sidecar is cosmetic next to an intact video, so a
                    # failure here is reported but does not fail the run.
                    print(f"  episode {ep} subtitles failed: {e}", file=sys.stderr)
        finally:
            browser.close()

    parts = [f"{len(downloaded)} downloaded", f"{len(present)} already present"]
    if subs_added:
        parts.append(f"{subs_added} subtitles backfilled")
    parts.append(f"{len(failed)} failed")
    summary = "done: " + ", ".join(parts)
    if failed:
        summary += f" (episodes: {', '.join(str(e) for e in failed)})"
    print(summary)
    return 1 if failed else 0


def download_movie(adapter, args):
    """Download one movie into `<out>/Title (Year)/Title (Year) - Q - RES.mp4`.

    That per-movie folder is what a Jellyfin Movies library expects, so a movie
    lands beside the series rather than being numbered into one. With --all it
    syncs like a series: an existing copy is kept and only a missing subtitle
    sidecar is fetched.
    """
    print(f"site: {adapter.name} (movie)")
    if args.all and not args.out:
        raise VsniffError("--all requires --out (the library directory to sync into)")
    for flag, value in (("--episode", args.episode), ("--from", args.start)):
        if value is not None:
            print(f"note: {flag} is ignored for a movie")

    url = adapter.movie_url(args.url)
    folder = os.path.join(expand_out_dir(args.out or "."),
                          movie_name(args.series, args.year))
    have = existing_movie(folder, args.series, args.year) if args.all else None
    if have and os.path.exists(subtitle_sidecar(os.path.join(folder, have), args.sub_lang)):
        print(f"already present: {have}")
        return 0

    print("discovering stream...")
    (_adapter, label, m3u8, referer, resolution, duration,
     subtitles) = discover_stream(
        url, args.source, show=args.show,
        keep_ads=args.keep_ads or bool(have))  # `have`: video already on disk
    if have:
        print(f"already present: {have}")
        if subtitles:
            save_subtitles(subtitles, referer, os.path.join(folder, have), args.sub_lang)
        return 0

    dur_txt = _fmt_time(duration) if duration else "unknown"
    print(f"stream: source={label}  resolution={resolution}  length={dur_txt}  "
          f"subtitles={'yes' if subtitles else 'none'}")
    os.makedirs(folder, exist_ok=True)
    out_path = os.path.join(folder, build_movie_filename(
        args.series, args.year, args.quality, resolution))
    print(f"downloading -> {out_path}")
    size = download_stream(m3u8, referer, out_path, duration)
    if subtitles:
        save_subtitles(subtitles, referer, out_path, args.sub_lang)
    print(f"done: {out_path}  ({size/1024/1024:.1f} MB)")
    return 0


def search_main(argv):
    """`vsniff search <title> [--site NAME ...]`: list matching entries."""
    from .api import search, searchable_sites  # keeps plain downloads import-light
    ap = argparse.ArgumentParser(
        prog="vsniff search",
        description="Search the supported sites for a series or movie by title. "
                    "Sites match their own spelling, so try simplified and "
                    "traditional forms (and a shorter piece of the title).")
    ap.add_argument("query", nargs="+", help="title to look for")
    ap.add_argument("--site", action="append", choices=searchable_sites(),
                    help="only search this site (repeatable; default: all)")
    args = ap.parse_args(argv)
    results = search(" ".join(args.query), sites=args.site)
    for r in results:
        note = f"  [{r.note}]" if r.note else ""
        print(f"{r.site:8} {r.title}{note}\n         {r.url}")
    print(f"{len(results)} result(s)")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "search":
        return search_main(argv[1:])
    ap = argparse.ArgumentParser(
        prog="vsniff",
        description="Sniff and download a streaming video as a Sonarr/Jellyfin-named MP4.")
    ap.add_argument("url", help="episode/play page URL")
    ap.add_argument("--series", "--title", dest="series", required=True,
                    help="English series (or movie) title")
    ap.add_argument("--season", type=int, default=1, help="season number (default 1)")
    ap.add_argument("--episode", type=int,
                    help="episode number (auto-detected for supported sites)")
    ap.add_argument("--quality", default="WEBDL", help="quality tag (default WEBDL)")
    ap.add_argument("--source", help="chinaq/dramasq: force a source by number (9) or name (ZYun)")
    ap.add_argument("--from", dest="start", type=int,
                    help="batch (--all) only: fetch episodes numbered N and above")
    ap.add_argument("--out", default=None,
                    help="output directory (default . for single mode; "
                         "REQUIRED with --all); supports ~ and $VARS, e.g. --out ~/Movies")
    ap.add_argument("--all", action="store_true",
                    help="download every available episode "
                         "(chinaq/hkanime/dramasq only; requires --out)")
    ap.add_argument("--sub-lang", dest="sub_lang", default="zh",
                    help="language code for the .srt sidecar (default zh)")
    ap.add_argument("--movie", action="store_true",
                    help="name it as a movie, 'Title (Year)/Title (Year) - ...mp4' "
                         "(automatic for hkanime 劇場版 entries)")
    ap.add_argument("--year", type=int,
                    help="movie release year, for the folder/filename")
    ap.add_argument("--keep-ads", dest="keep_ads", action="store_true",
                    help="don't strip ad clips spliced into the stream")
    ap.add_argument("--show", action="store_true", help="run the browser headful (debug)")
    args = ap.parse_args(argv)

    try:
        adapter = pick_adapter(args.url)
        if args.movie or adapter.is_movie(args.url):
            return download_movie(adapter, args)
        if args.year is not None:
            print("note: --year only applies to movies")

        if args.all:
            if not args.out:
                raise VsniffError(
                    "--all requires --out (the library directory to sync into)")
            if args.episode is not None:
                print("note: --episode is ignored with --all "
                      "(episodes come from the catalog)")
            return download_all(args.url, args)

        if args.start is not None:
            print("note: --from is ignored without --all")
        print(f"site: {adapter.name}")

        episode = args.episode if args.episode is not None else adapter.episode(args.url)
        if episode is None:
            raise VsniffError(
                f"could not auto-detect the episode number for a '{adapter.name}' URL; "
                f"pass it explicitly with --episode N")

        print("discovering stream...")
        (adapter, label, m3u8, referer, resolution, duration,
         subtitles) = discover_stream(args.url, args.source, show=args.show,
                                      keep_ads=args.keep_ads)
        dur_txt = _fmt_time(duration) if duration else "unknown"
        subs_txt = "yes" if subtitles else "none"
        print(f"stream: source={label}  resolution={resolution}  "
              f"length={dur_txt}  subtitles={subs_txt}")

        out_dir = expand_out_dir(args.out or ".")
        os.makedirs(out_dir, exist_ok=True)
        fname = build_filename(args.series, args.season, episode, args.quality, resolution)
        out_path = os.path.join(out_dir, fname)

        print(f"downloading -> {out_path}")
        size = download_stream(m3u8, referer, out_path, duration)
        if subtitles:
            save_subtitles(subtitles, referer, out_path, args.sub_lang)
        print(f"done: {out_path}  ({size/1024/1024:.1f} MB)")
        return 0
    except VsniffError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
