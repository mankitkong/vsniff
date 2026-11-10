"""vsniff bridge: lets Sonarr search the streaming sites and download from them.

It answers Sonarr as a Newznab indexer (search) and a SABnzbd download client
(downloads), matching Sonarr's shows to site entries by their Chinese titles
and running vsniff for each grabbed episode. Needs the `bridge` extra:
    pip install -e ".[bridge]"
"""
