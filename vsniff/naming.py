"""Sonarr/Jellyfin file and folder naming, and finding what is already on disk."""
import os
import re


def existing_episodes(out_dir, series, season):
    """{episode: filename} already in out_dir as {series} - Sxx Exx - ... .mp4.

    The filename is kept, not just the number, because a sidecar has to be
    named after the file that is actually on disk — an episode grabbed earlier
    may carry a different resolution tag than the one we would build today.
    """
    if not os.path.isdir(out_dir):
        return {}
    prefix = f"{series} - S{season:02d}E"
    rx = re.compile(re.escape(prefix) + r"(\d+) - .*\.mp4$")
    found = {}
    for name in sorted(os.listdir(out_dir)):
        m = rx.match(name)
        if m:
            found[int(m.group(1))] = name
    return found


def build_filename(series, season, episode, quality, resolution):
    return f"{series} - S{season:02d}E{episode:02d} - {quality} - {resolution}.mp4"


def movie_name(title, year):
    """`Title (Year)` — Jellyfin's movie folder and file stem; year optional."""
    return f"{title} ({year})" if year else title


def build_movie_filename(title, year, quality, resolution):
    return f"{movie_name(title, year)} - {quality} - {resolution}.mp4"


def existing_movie(folder, title, year):
    """Filename of a copy of this movie already in its folder, or None.

    Matched by prefix, not rebuilt, because an earlier copy may carry a
    different resolution tag than the stream serves today.
    """
    if not os.path.isdir(folder):
        return None
    prefix = f"{movie_name(title, year)} - "
    for name in sorted(os.listdir(folder)):
        if name.startswith(prefix) and name.endswith(".mp4"):
            return name
    return None


def expand_out_dir(path):
    """Resolve an output dir for macOS/Linux: expand ~ / ~user and $VARS."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
