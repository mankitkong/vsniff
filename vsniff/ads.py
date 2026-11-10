"""Spliced-in ad detection and removal for HLS media playlists.

Pure text processing: no network access, so it is easy to test in isolation.
"""
import re
from urllib.parse import urljoin, urlsplit


EXTINF_RX = re.compile(r"#EXTINF:([\d.]+)")


# ---- spliced-in ad removal ------------------------------------------------ #
# Some sources splice short ad clips into the episode's HLS playlist, each
# bracketed by #EXT-X-DISCONTINUITY. Two tells, both seen on chinaq sources:
#   * the clip's segments live in a different directory from the episode's
#     (e.g. /20261102/8EzXUOn8/10134kb/hls/ inside a /20261019/7cbiw8z4/... show)
#   * the clip shares the episode's directory but was encoded at another frame
#     rate (30fps ads in a 25fps episode), which shows in the EXTINF durations:
#     a segment always holds a whole number of frames.
# Discontinuities alone mean nothing: some sources split every scene cut into
# its own block, so a block is only dropped on one of the tells above, and only
# when it is short.
AD_MAX_BLOCK_S = 60      # longest run of segments we would ever call an ad
AD_MAX_SHARE = 0.2       # never strip more than this fraction of the episode
FRAME_RATES = (24000 / 1001, 24, 25, 30000 / 1001, 30, 50, 60000 / 1001, 60)
HEADER_TAGS = ("#EXTM3U", "#EXT-X-VERSION", "#EXT-X-TARGETDURATION",
               "#EXT-X-MEDIA-SEQUENCE", "#EXT-X-PLAYLIST-TYPE",
               "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-ALLOW-CACHE")
STATEFUL_TAGS = ("#EXT-X-KEY", "#EXT-X-MAP")  # apply until replaced
TAG_URI_RX = re.compile(r'URI="([^"]+)"')


def parse_media_playlist(text, url):
    """Split a media playlist into (header_lines, blocks).

    Each block is the run of segments between discontinuities; each segment is
    a dict with its absolute uri, duration, own tag lines, and the KEY/MAP tags
    in force for it (so dropping a block never strands the next one's key).
    """
    header, blocks, pending = [], [[]], []
    state = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "#EXT-X-ENDLIST":
            continue
        if line.startswith(HEADER_TAGS):
            header.append(line)
        elif line == "#EXT-X-DISCONTINUITY":
            if blocks[-1]:
                blocks.append([])
        elif line.startswith(STATEFUL_TAGS):
            tag = line.split(":", 1)[0]
            state[tag] = TAG_URI_RX.sub(
                lambda m: f'URI="{urljoin(url, m.group(1))}"', line)
        elif line.startswith("#"):
            pending.append(line)
        else:
            m = EXTINF_RX.match(next(
                (t for t in pending if t.startswith("#EXTINF")), ""))
            blocks[-1].append({
                "uri": urljoin(url, line),
                "duration": float(m.group(1)) if m else 0.0,
                "tags": pending,
                "state": dict(state),
            })
            pending = []
    return header, [b for b in blocks if b]


def _fits_frame_rate(duration, fps):
    frames = duration * fps
    return abs(frames - round(frames)) <= 0.003 * fps  # within 3ms of a frame


def _seg_dir(uri):
    return urlsplit(uri).path.rsplit("/", 1)[0]


def find_ad_blocks(blocks):
    """Indices of the blocks that look like spliced-in ads."""
    if len(blocks) < 2:
        return set()
    segs = [s for b in blocks for s in b]
    total = sum(s["duration"] for s in segs)
    if total <= 0:
        return set()

    by_dir = {}
    for s in segs:
        by_dir[_seg_dir(s["uri"])] = by_dir.get(_seg_dir(s["uri"]), 0) + s["duration"]
    main_dir = max(by_dir, key=by_dir.get)

    # The episode's frame rate: the lowest one that explains >=90% of the
    # airtime. Grids nest (every 25fps duration also fits 50fps, and a few 30fps
    # ad durations happen to as well), so the highest-scoring rate can be a
    # coarser grid that hides the ad; the lowest well-fitting rate is the real one.
    share = {fps: sum(s["duration"] for s in segs
                      if _fits_frame_rate(s["duration"], fps)) / total
             for fps in FRAME_RATES}
    fitting = [f for f in FRAME_RATES if share[f] >= 0.9]
    main_fps = min(fitting) if fitting else None

    ads = set()
    for i, block in enumerate(blocks):
        length = sum(s["duration"] for s in block)
        if length > AD_MAX_BLOCK_S:
            continue
        foreign = all(_seg_dir(s["uri"]) != main_dir for s in block)
        off_grid = main_fps is not None and sum(
            not _fits_frame_rate(s["duration"], main_fps) for s in block
        ) * 2 > len(block)
        if foreign or off_grid:
            ads.add(i)

    removed = sum(s["duration"] for i in ads for s in blocks[i])
    if len(ads) == len(blocks) or removed > total * AD_MAX_SHARE:
        return set()  # implausible: rather keep an ad than lose the episode
    return ads


def strip_ads(text, url):
    """(clean_playlist_text, removed_seconds, removed_blocks), or None if the
    playlist has no ad blocks. Segment and key URIs in the result are absolute,
    so it can be served from anywhere."""
    header, blocks = parse_media_playlist(text, url)
    ads = find_ad_blocks(blocks)
    if not ads:
        return None
    out = list(header) or ["#EXTM3U"]
    emitted = {}
    first = True
    for i, block in enumerate(blocks):
        if i in ads:
            continue
        if not first:
            out.append("#EXT-X-DISCONTINUITY")
        first = False
        for s in block:
            for tag, line in s["state"].items():
                if emitted.get(tag) != line:
                    out.append(line)
                    emitted[tag] = line
            out.extend(s["tags"])
            out.append(s["uri"])
    out.append("#EXT-X-ENDLIST")
    removed = sum(s["duration"] for i in ads for s in blocks[i])
    return "\n".join(out) + "\n", removed, len(ads)
