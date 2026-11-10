"""The Newznab side: what Sonarr's indexer client asks and how to answer.

Shaped by Sonarr 4's own parser (NewznabCapabilitiesProvider, RssParser,
NewznabRssParser), not the Newznab spec:
- caps: tv-search supportedParams must be comma-separated with no spaces;
  "tvdbid,season,ep" is enough for episode searches. No "q": a title search
  would only ever be Sonarr's fallback after the id search found nothing.
- items: the download link is read only from <enclosure> with type
  application/x-nzb; pubDate is required; size comes from newznab:attr size;
  newznab:attr language marks the audio as Chinese.
- errors go back as HTTP 200 + <error code description>; codes 100-199
  mean a bad API key.
"""
from email.utils import formatdate
from xml.sax.saxutils import escape, quoteattr

NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
CATEGORIES = [(5000, "TV", [(5030, "TV/SD"), (5040, "TV/HD"), (5070, "TV/Anime")])]
TV_HD, TV_SD = 5040, 5030
LANGUAGE = "Chinese"


def caps_xml():
    cats = "".join(
        f'<category id="{cid}" name="{name}">'
        + "".join(f'<subcat id="{sid}" name="{sname}"/>' for sid, sname in subs)
        + "</category>"
        for cid, name, subs in CATEGORIES)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<caps>'
            '<server title="vsniff bridge"/>'
            '<limits default="100" max="100"/>'
            '<searching>'
            '<search available="no"/>'
            '<tv-search available="yes" supportedParams="tvdbid,season,ep"/>'
            '<movie-search available="no"/>'
            '</searching>'
            f'<categories>{cats}</categories></caps>')


def error_xml(code, description):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<error code="{code}" description={quoteattr(description)}/>')


def _item(release, nzb_url, published):
    cat = TV_HD if release.resolution in ("720p", "1080p", "1440p", "2160p") else TV_SD
    attrs = {"size": release.size, "category": cat, "tvdbid": release.tvdb_id,
             "season": release.season, "episode": release.episode, "language": LANGUAGE}
    attr_xml = "".join(f'<newznab:attr name="{k}" value={quoteattr(str(v))}/>'
                       for k, v in attrs.items())
    return ("<item>"
            f"<title>{escape(release.title)}</title>"
            f'<guid isPermaLink="false">vsniff-{release.id}</guid>'
            f"<comments>{escape(release.entry_url)}</comments>"
            f"<pubDate>{formatdate(published, usegmt=True)}</pubDate>"
            f"<category>{cat}</category>"
            f"<description>{escape(release.entry_title)} ({release.site})</description>"
            f'<enclosure url={quoteattr(nzb_url)} length="{release.size}" '
            f'type="application/x-nzb"/>'
            f"{attr_xml}</item>")


def feed_xml(releases, nzb_url_for, offset=0):
    """RSS for releases; nzb_url_for(release) gives each enclosure's link."""
    items = "".join(_item(r, nzb_url_for(r), r.first_seen) for r in releases)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<rss version="2.0" xmlns:newznab="{NS}"><channel>'
            "<title>vsniff bridge</title><description>streaming sites via vsniff"
            "</description>"
            f'<newznab:response offset="{offset}" total="{len(releases)}"/>'
            f"{items}</channel></rss>")


def nzb_xml(release_id, title):
    """A placeholder NZB carrying only the release id.

    Sonarr checks just that the root is <nzb> with at least one <file>; the
    bridge then looks the release up by id, so nothing in the file is trusted.
    """
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
            f'<head><meta type="vsniff-release">{escape(release_id)}</meta>'
            f'<meta type="name">{escape(title)}</meta></head>'
            '<file poster="vsniff" date="0" subject="vsniff">'
            "<groups><group>alt.binaries.vsniff</group></groups>"
            '<segments><segment bytes="1" number="1">vsniff</segment></segments>'
            "</file></nzb>")
