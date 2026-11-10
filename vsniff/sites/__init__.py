"""Site adapters. To add a site, write an adapter in its own module here and
list it in ADAPTERS, ahead of GenericSite (the catch-all)."""
from urllib.parse import urlparse

from .base import GenericSite, SearchResult, match_source
from .chinaq import ChinaqSite
from .dramasq import DramasqSite
from .hkanime import HKAnimeSite

__all__ = ["ADAPTERS", "ChinaqSite", "DramasqSite", "GenericSite",
           "HKAnimeSite", "SearchResult", "match_source", "pick_adapter"]


ADAPTERS = [ChinaqSite(), HKAnimeSite(), DramasqSite(), GenericSite()]


def pick_adapter(url):
    host = urlparse(url).netloc.lower()
    for a in ADAPTERS:
        if a.matches(host):
            return a
    return ADAPTERS[-1]
