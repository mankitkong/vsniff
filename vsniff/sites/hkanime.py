"""hkanime.com: one stream per episode, straight from its play-api."""
import json
import re
import urllib.request
from functools import lru_cache

from ..common import UA, VsniffError, encode_url, origin_of
from ..sniff import sniff_once
from .base import GenericSite


HK_EP_RX = re.compile(r"^\s*EP\s*#?\s*0*(\d+)", re.I)


def hk_episode_numbers(labels):
    """Episode number for each hkanime playlist label, in site order.

    Labels are usually "EP01 <title>", but the site also carries movies with no
    number at all, merged double episodes ("EP01-02 ..."), and series that open
    part-way through a long run (One Piece starts at EP517). Trust the labels
    only when every one parses into a strictly ascending run — otherwise they
    can repeat, and two episodes would fight over one filename — and fall back
    to the 1-based position in the list.
    """
    nums = []
    for label in labels:
        m = HK_EP_RX.match(label)
        nums.append(int(m.group(1)) if m else None)
    if None not in nums and all(a < b for a, b in zip(nums, nums[1:])):
        return nums
    return list(range(1, len(labels) + 1))


@lru_cache(maxsize=8)
def hk_api(origin, series_id):
    """The parsed play-api JSON for an hkanime entry.

    Cached: a batch run asks for the same entry once per episode, and the movie
    check and the episode list read the same response.
    """
    req = urllib.request.Request(
        f"{origin}/play-api/{series_id}",
        headers={"User-Agent": UA, "Referer": f"{origin}/"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def hk_playurl(origin, series_id):
    """[(label, m3u8_url)] for an hkanime entry, in site order."""
    block = (hk_api(origin, series_id).get("playurl") or [{}])[0]
    return [(label, encode_url(m3u8))
            for label, m3u8 in block.items() if label.strip() and m3u8]


class HKAnimeSite(GenericSite):
    """hkanime is a single-page app: /play/<slug>/<seriesId>x<epIndex>.

    Its /play-api/<seriesId> JSON is both the episode catalog and the source of
    a playable master.m3u8 for every episode, so one request covers the whole
    series and no per-episode sniffing is needed. Sniffing stays as a fallback
    in case that endpoint changes.
    """
    name = "hkanime"
    supports_batch = True
    URL_RX = re.compile(r"^(?P<prefix>.*/play/[^/]+)/(?P<sid>\d+)(?:x(?P<idx>\d+))?$")

    def matches(self, host):
        return host.endswith("hkanime.com")

    def _parts(self, url):
        """(url_prefix, series_id, ep_index); ids are None when unparseable."""
        m = self.URL_RX.match(url.split("#")[0].split("?")[0].rstrip("/"))
        if not m:
            return None, None, None
        idx = m.group("idx")
        return (m.group("prefix"), int(m.group("sid")),
                int(idx) if idx is not None else None)

    def series_id(self, url):
        return self._parts(url)[1]

    def _numbers(self, url, series_id):
        return hk_episode_numbers(
            [label for label, _ in hk_playurl(origin_of(url), series_id)])

    def episode(self, url):
        _prefix, series_id, idx = self._parts(url)
        if series_id is None or idx is None:
            return None
        try:
            nums = self._numbers(url, series_id)
        except Exception:
            return idx + 1  # API unreachable: assume a plain 1..N series
        return nums[idx] if idx < len(nums) else None

    def is_movie(self, url):
        """True for entries the site files as a movie (remarks "劇場版")."""
        _prefix, series_id, _idx = self._parts(url)
        if series_id is None:
            return False
        try:
            info = (hk_api(origin_of(url), series_id).get("info") or [{}])[0]
        except Exception:
            return False  # can't tell -> keep series naming; --movie still works
        return "劇場版" in (info.get("remarks") or "")

    def movie_url(self, url):
        """Pick the one video a movie entry holds.

        Nearly every movie entry holds exactly one video, so a bare entry URL
        (/play/<slug>/357) resolves to it. A few bundle several separate films
        (all 13 Naruto movies in one entry), and each needs its own title and
        year, so those must be picked by index rather than guessed at.
        """
        prefix, series_id, idx = self._parts(url)
        if series_id is None or idx is not None:
            return url
        episodes = hk_playurl(origin_of(url), series_id)
        if len(episodes) == 1:
            return f"{prefix}/{series_id}x0"
        listing = "\n".join(f"  {prefix}/{series_id}x{i}  {label.strip()}"
                            for i, (label, _) in enumerate(episodes))
        raise VsniffError(
            f"this entry bundles {len(episodes)} separate movies; download each "
            f"by its own URL with its own --title and --year:\n{listing}")

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series."""
        prefix, series_id, _idx = self._parts(url)
        if series_id is None:
            raise VsniffError("could not parse the hkanime series id from the URL")
        nums = self._numbers(url, series_id)
        if not nums:
            raise VsniffError("hkanime lists no episodes for this series")
        return [(ep, f"{prefix}/{series_id}x{i}") for i, ep in enumerate(nums)]

    def discover(self, page, ctx, url, user_source):
        if user_source is not None:
            print("  note: --source is ignored for hkanime (source is in the URL)")
        _prefix, series_id, idx = self._parts(url)
        if series_id is not None and idx is not None:
            try:
                episodes = hk_playurl(origin_of(url), series_id)
            except Exception as e:
                print(f"  note: play-api unavailable ({e}); sniffing the page")
                episodes = []
            if idx < len(episodes):
                return "hkanime", episodes[idx][1], url
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the hkanime page")
        return "hkanime", found[0], found[1]
