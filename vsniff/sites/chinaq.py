"""chinaq.net: several playback sources per episode, tried in turn."""
import re
from urllib.parse import quote

from ..common import VsniffError, clean_text, origin_of
from ..sniff import sniff_once
from .base import GenericSite, SearchResult, episode_count, http_get, match_source


CQ_CARD_RX = re.compile(r'<a href="(/voddetail/\d+\.html)">(.*?)</a>', re.S)
CQ_TITLE_RX = re.compile(r'class="title[^"]*">\s*([^<]+?)\s*</div>')
CQ_CAPTION_RX = re.compile(r'carousel-caption"><p>([^<]*)</p>')
CQ_REGION_RX = re.compile(r'icon-region">([^<]*)<')


def parse_search(html, origin="https://chinaq.net"):
    """[SearchResult] from a chinaq /vodsearch page.

    Only the results grid counts: the page also carries a "最近更新" sidebar
    of unrelated shows that links the same way.
    """
    start = html.find('class="thumb sizing')
    if start < 0:
        return []
    end = html.find('class="sidebar', start)
    block = html[start:end if end > 0 else None]
    results = []
    for href, card in CQ_CARD_RX.findall(block):
        title = CQ_TITLE_RX.search(card)
        if not title:
            continue
        note = " ".join(clean_text(m.group(1)) for m in
                        (CQ_REGION_RX.search(card), CQ_CAPTION_RX.search(card)) if m)
        results.append(SearchResult("chinaq", clean_text(title.group(1)),
                                    origin + href, note, episode_count(note)))
    return results


class ChinaqSite(GenericSite):
    name = "chinaq"
    supports_batch = True
    supports_search = True
    ORIGIN = "https://chinaq.net"

    def search(self, query):
        """Search chinaq's catalog (recent shows only; the site was rebuilt).

        Its titles are traditional and sometimes use odd variant characters
        (晚鞦寄情 for 晚秋寄情), so an exact title can miss where a two-character
        piece of it still hits.
        """
        query = clean_text(query)
        if not query:
            return []
        html = http_get(f"{self.ORIGIN}/vodsearch/-------------.html?wd={quote(query)}")
        return parse_search(html, self.ORIGIN)
    SOURCE_RX = re.compile(
        r'#sid=(\d+)"[^>]*onclick="changeSid\(\'\d+\'\)"><strong>[^<]*</strong><small>([^<]*)</small>'
    )

    def matches(self, host):
        return host.endswith("chinaq.net")

    def episode(self, url):
        m = re.search(r"/video/\d+-(\d+)\.html", url)
        return int(m.group(1)) if m else None

    def series_id(self, url):
        m = re.search(r"/video/(\d+)-\d+\.html", url) \
            or re.search(r"/voddetail/(\d+)\.html", url)
        return int(m.group(1)) if m else None

    def parse_episodes(self, html, series_id):
        """Parse voddetail HTML -> {sid: {episode numbers}} for this series."""
        rx = re.compile(rf"/video/{series_id}-(\d+)\.html#sid=(\d+)")
        by_sid = {}
        for ep, sid in rx.findall(html):
            by_sid.setdefault(int(sid), set()).add(int(ep))
        return by_sid

    def _sources(self, html):
        tabs = [(int(s), n.strip()) for s, n in self.SOURCE_RX.findall(html)]
        tabs.sort()
        if not tabs:
            raise VsniffError("no playback sources found on the chinaq page")
        return tabs

    def _order(self, sources, user_source, hash_sid):
        sids = [s for s, _ in sources]
        if user_source is not None:
            return [match_source(sources, user_source)]
        if hash_sid in sids:
            return [hash_sid] + [s for s in sids if s != hash_sid]
        return sids

    def catalog(self, page, ctx, url):
        """[(episode, episode_page_url)] for every episode of this series."""
        series_id = self.series_id(url)
        if series_id is None:
            raise VsniffError("could not parse the chinaq series id from the URL")
        origin = origin_of(url)
        hm = re.search(r"#sid=(\d+)", url)
        preferred_sid = int(hm.group(1)) if hm else None

        page.goto(f"{origin}/voddetail/{series_id}.html",
                  wait_until="domcontentloaded", timeout=30000)
        by_sid = self.parse_episodes(page.content(), series_id)
        items = []
        for ep in available_episodes(by_sid):
            ep_url = f"{origin}/video/{series_id}-{ep}.html"
            if preferred_sid is not None and ep in by_sid.get(preferred_sid, set()):
                ep_url += f"#sid={preferred_sid}"
            items.append((ep, ep_url))
        return items

    def discover(self, page, ctx, url, user_source):
        base = url.split("#")[0]
        hm = re.search(r"#sid=(\d+)", url)
        hash_sid = int(hm.group(1)) if hm else None

        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        sources = self._sources(page.content())
        names = dict(sources)
        for sid in self._order(sources, user_source, hash_sid):
            label = names.get(sid, "?")
            print(f"  trying source sid={sid} ({label}) ...", flush=True)
            found = sniff_once(page, f"{base}#sid={sid}")
            if found:
                print(f"  -> live stream on {label}")
                return label, found[0], found[1]
        raise VsniffError("no working source found (all chinaq sources failed)")


def available_episodes(by_sid):
    """Sorted union of every episode number across all sources."""
    eps = set()
    for episodes in by_sid.values():
        eps |= episodes
    return sorted(eps)
