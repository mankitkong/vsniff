"""vsniff — streaming sites as a source for Sonarr.

The Sonarr bridge (vsniff.bridge, run as `vsniff-bridge`) lets Sonarr search
and download from Chinese streaming sites as if they were a usenet indexer and
download client. Underneath, and from the `vsniff` command line, vsniff
drives a headless browser to *discover* the live video stream a page plays
(the stream URLs are usually hotlink-protected and tokened, so they must be
captured from a real browser session), then hands the captured playlist +
referer to ffmpeg to download and mux into a Sonarr/Jellyfin-named .mp4.

The core sniff-and-capture is site-agnostic. Thin per-site *adapters*
(vsniff.sites) add niceties: pulling the episode number out of the URL, and
(for chinaq and dramasq) trying each playback source until one plays. Sites
without an adapter still work — you just provide --episode yourself.

    vsniff "https://www.hkanime.com/play/<name>/120x0" \\
        --series "Fullmetal Alchemist Brotherhood" --season 1
    vsniff "https://chinaq.net/video/1104-7.html#sid=3" \\
        --series "Tigers Sniff the Rose" --source 3
    vsniff "https://dramasq.io/vodplay/202614591/ep1.html" \\
        --series "Fall in Love with the Moon"
    vsniff search 长相思                     # find page URLs by title

From Python, see vsniff.api (search, and a Session for catalog/probe/download).

Layout:
    bridge/      the Sonarr bridge service (Newznab + SABnzbd endpoints)
    api          Python API: search; Session for catalog / probe / download
    cli          argument parsing; single, --all, movie and search flows
    discovery    find one stream in a browser session
    sniff        capture an .m3u8 from a page's network traffic
    playlist     resolution (ffprobe fallback)/length/subtitles; ad-stripped copies
    ads          spliced-in ad detection (pure text processing)
    download     ffmpeg download with progress bar or callback; subtitle sidecars
    naming       Sonarr/Jellyfin filenames; what is already on disk
    sites/       one adapter per site
"""
__version__ = "0.2.0"
