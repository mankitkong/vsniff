"""dramasq.io: several sources per episode, all listed by its /drq API."""
import json
import re
import urllib.request
from urllib.parse import quote, urlsplit

from ..common import UA, VsniffError, clean_text, origin_of
from ..sniff import sniff_once
from .base import GenericSite, SearchResult, episode_count, http_get, match_source


DQ_RESULT_RX = re.compile(r'<a class="drama[^"]*" href="(/detail/\d+\.html)">([^<]*)</a>')
# a result reads "長相思 第二季 第23集" or "繁花 第30集完結": the trailing part is status
DQ_STATUS_RX = re.compile(r"\s*((?:第\d+集)?\s*(?:完結|完结)?)\s*$")


def parse_search(html, origin="https://dramasq.io"):
    """[SearchResult] from a dramasq /search page (results list only)."""
    start = html.find('class="main-content')
    if start < 0:
        return []
    end = html.find('class="sidebar', start)
    block = html[start:end if end > 0 else None]
    results = []
    for href, text in DQ_RESULT_RX.findall(block):
        text = clean_text(text)
        m = DQ_STATUS_RX.search(text)
        note = m.group(1).strip() if m else ""
        title = text[:m.start()].strip() if m and note else text
        results.append(SearchResult("dramasq", title, origin + href, note,
                                    episode_count(note)))
    return results


DQ_SOURCE_RX = re.compile(
    r'v_data="([^"]*)"[^>]*>\s*<strong>[^<]*</strong>\s*<small>([^<]*)</small>')


def dq_api(origin, series_id, slug):
    """The parsed /drq/<seriesId>/<slug> JSON: every source for one episode.

    The player page fetches this itself, and it carries both the stream URLs
    (`video_plays`) and the rendered source tabs (`html_content`) with names.
    """
    req = urllib.request.Request(
        f"{origin}/drq/{series_id}/{slug}",
        headers={"User-Agent": UA,
                 "Referer": f"{origin}/vodplay/{series_id}/{slug}.html"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def dq_sources(data):
    """[(source_number, name, m3u8_url)] from a /drq response, in site order.

    Numbered by position like the page's 片源1, 片源2 … tabs. Names repeat (two
    different CDNs are both "JYun"), so the number is the unambiguous handle.
    """
    tabs = DQ_SOURCE_RX.findall(data.get("html_content") or "")
    if tabs:
        pairs = [(url, name.strip()) for url, name in tabs]
    else:  # tab markup changed: fall back to the bare list, unnamed
        pairs = [(p.get("play_data") or p.get("v_data") or "", p.get("src_site", "?"))
                 for p in data.get("video_plays") or []]
    return [(i, name, url) for i, (url, name) in enumerate(pairs, 1) if url]


def stream_reachable(url, referer):
    """True if url serves an HLS playlist. Several dramasq CDNs answer 403/404
    (region-locked or expired), so a source is checked before it is picked."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(64).lstrip(b"\xef\xbb\xbf").startswith(b"#EXTM3U")
    except Exception:
        return False


class DramasqSite(GenericSite):
    """dramasq: /vodplay/<seriesId>/ep<N>.html episodes, /detail/<seriesId>.html
    series pages.

    Like chinaq it offers several sources per episode, but its /drq API hands
    over every source's m3u8 directly — and the CDNs don't check tokens or
    referers — so sources are tried with plain HTTP instead of a browser.
    Sniffing the page stays as a fallback in case that API changes.
    """
    name = "dramasq"
    supports_batch = True
    supports_search = True
    ORIGIN = "https://dramasq.io"
    URL_RX = re.compile(r"/(?:vodplay|detail)/(?P<sid>\d+)(?:/(?P<slug>[^/.#?]+))?")
    EP_SLUG_RX = re.compile(r"^ep(\d+)$")
    MAX_UNLISTED = 10  # newest episodes the API serves before the listing shows them

    def matches(self, host):
        return re.search(r"(^|\.)dramasq\.[a-z]+$", host) is not None

    def search(self, query):
        """Search dramasq's catalog (deep: entries go back to 2018).

        The search is loose: it also returns near-misses (藏海传 finds 藏海花)
        and titles that merely contain the query, so check what comes back.
        Simplified queries match reliably; traditional ones only sometimes.
        """
        query = clean_text(query)
        if not query:
            return []
        return parse_search(http_get(f"{self.ORIGIN}/search?q={quote(query)}"), self.ORIGIN)

    def _parts(self, url):
        """(series_id, slug); slug is e.g. "ep7", "sp", "20261110", or None."""
        m = self.URL_RX.search(urlsplit(url).path)
        if not m:
            return None, None
        return int(m.group("sid")), m.group("slug")

    def series_id(self, url):
        return self._parts(url)[0]

    def episode(self, url):
        """N for an epN page; None for specials (sp), a 正片 (zheng_pian) feature
        or a daily show's date-keyed page — those need --episode or --movie."""
        _sid, slug = self._parts(url)
        m = self.EP_SLUG_RX.match(slug or "")
        return int(m.group(1)) if m else None

    def parse_episodes(self, html, series_id):
        """Episode numbers a detail page links to (epN pages only)."""
        rx = re.compile(rf"/vodplay/{series_id}/ep(\d+)\.html")
        return sorted({int(n) for n in rx.findall(html)})

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series.

        The detail page lags the API: an episode can be playable for a while
        before it is linked, so episodes past the last listed one are probed
        until the API has nothing.
        """
        series_id = self.series_id(url)
        if series_id is None:
            raise VsniffError("could not parse the dramasq series id from the URL")
        origin = origin_of(url)
        req = urllib.request.Request(f"{origin}/detail/{series_id}.html",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            eps = self.parse_episodes(resp.read().decode("utf-8", "replace"), series_id)
        if not eps:
            raise VsniffError(
                "dramasq lists no numbered episodes for this series (daily shows "
                "and specials have none); download pages one at a time with --episode")
        for ep in range(eps[-1] + 1, eps[-1] + 1 + self.MAX_UNLISTED):
            try:
                if not dq_sources(dq_api(origin, series_id, f"ep{ep}")):
                    break
            except Exception:
                break
            eps.append(ep)
        return [(ep, f"{origin}/vodplay/{series_id}/ep{ep}.html") for ep in eps]

    def discover(self, page, ctx, url, user_source):
        series_id, slug = self._parts(url)
        sources = []
        if series_id is not None and slug:
            try:
                sources = dq_sources(dq_api(origin_of(url), series_id, slug))
            except Exception as e:
                print(f"  note: dramasq API unavailable ({e}); sniffing the page")
        if sources:
            by_num = {n: (name, m3u8) for n, name, m3u8 in sources}
            order = [n for n, _, _ in sources]
            if user_source is not None:
                order = [match_source([(n, name) for n, name, _ in sources], user_source)]
            referer = url.split("#")[0]
            for n in order:
                label, m3u8 = by_num[n]
                print(f"  trying source {n} ({label}) ...", flush=True)
                if stream_reachable(m3u8, referer):
                    print(f"  -> live stream on {label}")
                    return label, m3u8, referer
            raise VsniffError("no working source found (all dramasq sources failed)")
        found = sniff_once(page, url)
        if not found:
            raise VsniffError("no stream found on the dramasq page")
        return "dramasq", found[0], found[1]
