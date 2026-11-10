# vsniff

**Streaming sites as a source for Sonarr.** vsniff lets Sonarr find and
download shows from Chinese streaming sites the same way it uses torrent and
usenet indexers: they show up in Sonarr's searches, grabs download in the
background, and finished episodes are imported into your library (and so into
Jellyfin) like any other release.

```
Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 1080p.mp4
```

There are two ways to use it:

- **[The Sonarr bridge](#sonarr-bridge)** (the main one): a small service
  Sonarr talks to as a Newznab indexer and a SABnzbd download client. It
  matches Sonarr's shows to the sites by their Chinese titles and runs vsniff
  for every episode Sonarr grabs.
- **[The command line](#command-line-what-you-need)**: point it at a video
  page and it downloads that episode (or the whole series) as a
  Sonarr/Jellyfin-named `.mp4`. Handy for one-offs, and how vsniff started.

Under the hood it captures whatever HLS stream a page plays in a real browser,
so it works on any such page. Three sites get extra polish (automatic episode
numbers, whole-series downloads, search by title — which is what lets Sonarr
use them — and, for chinaq and dramasq, source selection):

| Site | Auto episode # | Search | Notes |
|------|:---:|:---:|-------|
| **chinaq.net** | ✅ | ✅ | tries each playback source until one works; recent shows only |
| **hkanime.com** | ✅ | ✅ | Cantonese-dubbed anime; episode list + streams come from its play-api |
| **dramasq.io** | ✅ | ✅ | tries each playback source until one works; deep back catalogue (anime too); no browser needed |
| **any other site** | — | — | command line only; you supply `--episode` |

---

## Sonarr bridge

```
            search (Newznab)                    title search, episode lists
 Sonarr ───────────────────▶ vsniff bridge ───────────────────────────▶ chinaq
        ◀── releases ───────                                          hkanime
            grab (SABnzbd)              runs vsniff per episode        dramasq
        ───────────────────▶            ───────────────────────────▶
        ◀── "Completed" ────   <downloads>/tv/<release name>/Show - S01E07 - WEBDL - 1080p.mp4
 import ──▶ your library ──▶ Jellyfin
```

### What it does

- **Finds the show on each site.** Sonarr only knows a show by its ids and
  English/romanised names, while the sites list Chinese titles. The bridge
  gathers every name for the show — Sonarr's, plus TMDB's Chinese titles per
  region (the Hong Kong name can differ entirely: 間諜家家酒, not 間諜過家家) —
  and searches each site in simplified and traditional script.
- **Only accepts a result it can prove is the right season.** A wrong match
  is worse than none, because the release is named after Sonarr's show and
  Sonarr can't tell it got a different video. A result is rejected unless:
  its name is one of the show's names (spelling variants, simplified vs
  traditional and punctuation don't matter); it isn't a movie; it's on
  hkanime only if the show is animation; any extra words name the season
  (TMDB season names like 遊郭篇); a season marker (S2, 第二季) matches; and
  its episode count fits the season. Sites that list several seasons as one
  entry (信蜂, 50 episodes = S1 + S2) are mapped with an offset.
- **Names releases so Sonarr understands them:**
  `Tigers Sniff the Rose S01E07 1080p WEB-DL AAC-dramasq` — the resolution
  is measured from the stream (one probe per entry, cached), and the release
  group is the site.
- **Keeps a season on one source.** For automatic grabs it pins each season
  to its best source (highest resolution, then closest title) the first time
  it's asked, and every later episode — including ones that air months later —
  comes from that pin. If the pinned page disappears it re-pins; if you
  manually grab a different source, that becomes the pin.
- **Shows what it's doing** on a status page at `http://<host>:8990/`:
  pinned sources and recent downloads.

### Running it (Docker)

The image bundles Chromium, ffmpeg and vsniff. Add the service next to Sonarr
in the same `docker-compose.yml`:

```yaml
  vsniff-bridge:
    build: ./projects/vsniff                       # path to this repository
    container_name: vsniff-bridge
    user: "1000:1001"                              # same user/group as Sonarr's files
    environment:
      - SONARR_URL=http://sonarr:8989
      - SONARR_API_KEY_FILE=/keys/sonarr-config.xml   # key read from Sonarr's own config
      - TMDB_API_KEY_FILE=/keys/tmdb-key              # optional, for Chinese titles
      - VSNIFF_COMPLETE_DIR=/media/downloads/vsniff
    volumes:
      - ./vsniff-bridge:/data                      # api-key + bridge.db
      - ./sonarr/config/config.xml:/keys/sonarr-config.xml:ro
      - ./tmdb-key:/keys/tmdb-key:ro               # a file holding your TMDB v3 key
      - /path/to/media:/media                      # the same mount Sonarr has
    ports:
      - "8990:8990"
    restart: unless-stopped
```

```bash
docker compose up -d --build vsniff-bridge
docker logs vsniff-bridge        # says where everything is and whether Sonarr/TMDB answer
```

There are no keys to copy by hand: Sonarr's key is read from Sonarr's own
`config.xml` (so it stays right if you regenerate it), and the bridge makes
its own key on first start. A [TMDB](https://www.themoviedb.org/settings/api)
key is optional but strongly recommended — it's where the Chinese titles come
from. Two things must line up:

- **Same media path in both containers.** Sonarr imports what the bridge
  writes to `VSNIFF_COMPLETE_DIR`, so the media folder has to be mounted at the
  same place in both.
- **Same user as Sonarr's files** (`user:`), so Sonarr can move what the
  bridge downloaded.

**Where to look**

| What | Where |
|------|-------|
| Is it running, and what is it doing? | Status page: `http://<host>:8990/` (pinned sources, recent downloads) |
| Startup checks, searches, rejected matches, download errors | `docker logs vsniff-bridge` (`--log-level debug` in the command for every rejection) |
| The key Sonarr needs | `./vsniff-bridge/api-key` |
| Pins, jobs, probe cache | `./vsniff-bridge/bridge.db` (SQLite) |
| Finished downloads | `<VSNIFF_COMPLETE_DIR>/tv/<release name>/` — Sonarr moves them into the library |
| Settings | the `environment:` block above; every variable is listed below |

**Settings**

| Variable | Default | Meaning |
|----------|---------|---------|
| `SONARR_URL` | `http://sonarr:8989` | Where the bridge reads show details from |
| `SONARR_API_KEY[_FILE]` | — (required) | Sonarr's API key; the file may be Sonarr's `config.xml` |
| `TMDB_API_KEY[_FILE]` | — | TMDB v3 key for Chinese titles; without it only Sonarr's names are searched |
| `VSNIFF_BRIDGE_API_KEY[_FILE]` | generated into `<VSNIFF_DATA_DIR>/api-key` | The key Sonarr sends to the bridge |
| `VSNIFF_COMPLETE_DIR` | `/media/downloads/vsniff` | Finished downloads (one folder per category) |
| `VSNIFF_CATEGORIES` | `tv,movies` | Download categories offered to Sonarr |
| `VSNIFF_WORKERS` | `1` | Downloads at a time |
| `VSNIFF_SEARCH_INTERVAL` | `2` | Seconds between searches on one site (dramasq rate-limits bursts) |
| `VSNIFF_DATA_DIR` | `/data` | Where `api-key` and `bridge.db` live |

`*_FILE` variants read the value from a file, so keys stay out of
`docker-compose.yml`. Without Docker: `pip install -e ".[bridge]"`, set the
same variables, and run `vsniff-bridge` (port 8990).

### Setting up Sonarr

**1. The download client** — Settings → Download Clients → + → **SABnzbd**:

| Field | Value |
|-------|-------|
| Name | `vsniff` |
| Host / Port | `vsniff-bridge` / `8990` |
| API Key | the contents of `vsniff-bridge/api-key` |
| Category | `tv` |

Press **Test** (it should pass with no warnings), then Save.

**2. The indexer — add it twice**, so every show can be browsed but only the
shows you choose are downloaded automatically. Settings → Indexers → + →
**Newznab** → Custom:

| Field | vsniff (browse) | vsniff (auto) |
|-------|-----------------|---------------|
| URL | `http://vsniff-bridge:8990` | `http://vsniff-bridge:8990` |
| API Key | `vsniff-bridge/api-key` | `vsniff-bridge/api-key` |
| Categories | TV/SD, TV/HD | TV/SD, TV/HD |
| Enable RSS | off | on |
| Enable Automatic Search | off | on |
| Enable Interactive Search | on | on |
| Additional Parameters (advanced) | — | `&vsniff=auto` |
| Tags | — | `vsniff` |
| Download Client (advanced) | `vsniff` | `vsniff` |

- **vsniff (browse)** answers interactive searches (the person icon) for any
  show, listing every site that has the episode. Nothing is grabbed unless
  you pick it.
- **vsniff (auto)** only serves shows tagged `vsniff`, and only from each
  season's pinned source. Tag a show (its Edit dialog → Tags → `vsniff`) to
  have new episodes downloaded through vsniff automatically.

### Limitations

- **TV only** for now; Radarr (movies) isn't wired up yet.
- **Searches by id only.** Sonarr's title-only fallback searches return
  nothing, as do daily shows (dated episodes) and Sonarr's *anime* series type
  (which searches by absolute episode number) — use the *standard* type.
- **Absolute-numbered entries aren't mapped yet**: hkanime's One Piece
  (episodes 517–819) won't line up with Sonarr's seasons.
- **Releases are all marked Chinese.** dramasq also carries anime in its
  original Japanese with subtitles; the release group (`-dramasq` vs
  `-hkanime`, which is Cantonese dubs) tells them apart.
- **Sizes are estimates** (from the episode length and resolution); the real
  size is known once downloaded. Progress in Sonarr's queue is by length.

---

## Command line: what you need

This guide assumes a **Mac** with nothing installed and no prior Python
experience. You'll paste a handful of commands into the **Terminal** app
(press `Cmd+Space`, type "Terminal", press Enter). Each command is one line —
paste it, press Enter, wait, then do the next.

You'll install four things:

| Thing | What it is | Why |
|-------|------------|-----|
| Homebrew | A package installer for Mac | Installs the rest |
| Python | The language vsniff is written in | Runs the tool |
| ffmpeg | A video toolkit | Assembles the download into an mp4 |
| Playwright + Chromium | An automated browser | Finds the real video stream |

---

## Command line: setup (one time, ~10 minutes)

### Step 1 — Install Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

If it finishes by printing two lines starting with `echo` and `eval`, copy
and run those two lines (they add Homebrew to your PATH). Then check:

```bash
brew --version
```

If you see "command not found," close and reopen Terminal and try again.

### Step 2 — Install Python and ffmpeg

```bash
brew install python ffmpeg
```

Confirm:

```bash
python3 --version      # expect: Python 3.x.x
ffmpeg -version        # expect: ffmpeg version ...
```

### Step 3 — Go into the project folder

Open Terminal inside the project folder (the one this README is in, which
contains `pyproject.toml` and a `vsniff` folder). For example, if it's in your home folder:

```bash
cd ~/vsniff        # the folder name may differ; it's wherever this README lives
```

Every command below assumes you're in that folder. Get back to it any time
with the same `cd` command.

### Step 4 — Set up the tool's private workspace

Python projects keep their dependencies in a private `.venv` folder so they
don't clash with anything else on your Mac. Create it and install what's
needed:

```bash
python3 -m venv .venv                          # create the private workspace
./.venv/bin/pip install -e .                   # install vsniff and Playwright
./.venv/bin/playwright install chromium        # download the automated browser (~100MB)
```

### Step 5 — Confirm it's ready

```bash
./.venv/bin/vsniff -h
```

If you see a help message, setup is complete. 🎉

---

## Command line: using it

The basic form:

```bash
./.venv/bin/vsniff "<PAGE_URL>" --series "<Show Name>" --season <N>
```

> **Always wrap the URL in "quotes"** — the `#` and special characters in
> these URLs confuse the Terminal otherwise.

### chinaq.net

```bash
./.venv/bin/vsniff \
  "https://chinaq.net/video/1104-7.html#sid=3" \
  --series "Tigers Sniff the Rose" --season 1
```

→ `Tigers Sniff the Rose - S01E07 - WEBDL - 1080p.mp4`

### hkanime.com

```bash
./.venv/bin/vsniff \
  "https://www.hkanime.com/play/鋼之鍊金術師FA/120x0" \
  --series "Fullmetal Alchemist Brotherhood" --season 1
```

→ `Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 720p.mp4`
(the `x0` in the URL is the first episode → E01)

### dramasq.io

```bash
./.venv/bin/vsniff \
  "https://dramasq.io/vodplay/202614591/ep1.html" \
  --series "Fall in Love with the Moon" --season 1 --source SYun
```

→ `Fall in Love with the Moon - S01E01 - WEBDL - 720p.mp4`
(the `ep1` in the URL is the episode number; `--source` is optional, see
[dramasq sources](#dramasq-sources---source))

### Any other site

vsniff will still sniff and download; it just can't guess the episode number,
so provide it:

```bash
./.venv/bin/vsniff "<page url>" --series "Some Show" --episode 3
```

### Finding a show (`vsniff search`)

Look a title up on every supported site and get page URLs to download from:

```bash
./.venv/bin/vsniff search 长相思
```

```
dramasq  長相思 第二季  [第23集]
         https://dramasq.io/detail/202458112.html
dramasq  長相思 第一季
         https://dramasq.io/detail/202389894.html
2 result(s)
```

The part in `[brackets]` is the site's own status: episodes so far
(`第23集`, `更新至34集`), a finished count (`全集完 共50集`) or `劇場版` for a
movie. Any result URL works with `--all`. Add `--site chinaq` (repeatable) to
search only some sites.

Each site matches its own spelling, so if a title doesn't turn up:

- **Try both scripts.** dramasq matches simplified reliably (庆余年) but
  traditional only sometimes (慶餘年); chinaq only matches traditional.
- **Try a shorter piece.** chinaq spells some characters with rare variants
  (晚鞦寄情 for 晚秋寄情), so the full title misses where `寄情` hits.
- **Try the Hong Kong name** on hkanime, which can differ entirely from the
  mainland one (間諜家家酒, not 間諜過家家), or the English/romaji name.
- **A result can have nothing left to play.** dramasq keeps pages for shows it
  has taken down (長相思 第一季 above); `--all` then reports no episodes.

### While it downloads

You'll see a live progress bar:

```
  [##############------------------]  44.2%  10:48/24:30
```

### All options

(For searching, see [`vsniff search`](#finding-a-show-vsniff-search) above;
`vsniff search -h` lists its two options.)

| Option | Required? | Default | Meaning |
|--------|-----------|---------|---------|
| `url` | ✅ | — | The video page URL |
| `--series` / `--title` | ✅ | — | English show or movie title (used in the filename) |
| `--season` | | `1` | Season number |
| `--episode` | | auto | Episode number (auto for chinaq/hkanime/dramasq; required elsewhere) |
| `--quality` | | `WEBDL` | Quality tag in the filename |
| `--source` | | auto | **chinaq/dramasq:** force a source by number (`9`) or name (`ZYun`) |
| `--out` | | `.` (here) | Folder to save into; supports `~` and `$VARS`, e.g. `--out ~/Movies` |
| `--all` | | off | Download every episode of the series (chinaq/hkanime/dramasq; needs `--out`) |
| `--from` | | — | With `--all`: start at episode N and keep going |
| `--sub-lang` | | `zh` | Language code for the `.srt` sidecar |
| `--movie` | | auto | Name it as a movie (automatic for hkanime movie entries) |
| `--year` | | — | Movie release year, used in the folder and filename |
| `--keep-ads` | | off | Don't strip ad clips spliced into the stream (see below) |
| `--show` | | off | Show the browser window (for debugging) |

### chinaq sources (`--source`)

Each chinaq episode has several playback sources (the 片源1, 片源2 … tabs,
named `BYun`, `ZYun`, …). Some are dead at any time.

- **Do nothing** → vsniff tries each until one plays (honoring a `#sid=N` in
  your URL first).
- **Force one** → `--source 9` (number) or `--source ZYun` (name, any case).

### dramasq sources (`--source`)

dramasq works the same way, with more sources per episode (often 8–12,
片源1 … 片源12). Several of them are reliably dead (403/404) at any time, so
vsniff checks each one in order and takes the first that answers.

- **Do nothing** → first working source, in the site's order.
- **Force one** → `--source 10` (number) or `--source SYun` (name). Two
  sources can share a name (e.g. two `JYun`s on different servers); a name
  picks the first, so use the number to reach the other.

The sources differ in quality — on the episodes checked, `SYun` carried a
1920×800 stream at about twice the bitrate of the default — so it can be
worth forcing one.

dramasq only numbers regular episodes (`.../ep7.html`). Specials (`sp`), a
`zheng_pian` (正片, "full feature") entry and daily shows keyed by date
(`.../20261110.html`) still download, but need `--episode N` (or `--movie`),
and `--all` skips them.

hkanime has one stream per episode (its URL is `<seriesId>x<episodeIndex>` —
`120x0` is the first episode of series 120), so `--source` isn't used there.

### Movies and specials

hkanime lists a series' movies (劇場版) as their own entries, separate from the
episodes. vsniff spots these automatically and names them as movies instead of
numbering them into a season:

```bash
./.venv/bin/vsniff \
  "https://www.hkanime.com/play/百變小櫻 相約在香港 /357" \
  --title "Cardcaptor Sakura The Movie" --year 1999 --out ~/media/movies
```

→ `~/media/movies/Cardcaptor Sakura The Movie (1999)/Cardcaptor Sakura The Movie (1999) - WEBDL - 720p.mp4`

That per-movie folder is the layout a Jellyfin **Movies** library expects, so
point `--out` at your Movies library root and each movie gets its own folder
and proper movie metadata. The site doesn't publish release years, so pass
`--year` yourself; it's optional but helps Jellyfin find the right film.

- **Other sites:** add `--movie` to get the same naming anywhere.
- **Bundles:** a couple of hkanime entries hold several separate films (e.g.
  all the Naruto movies in one entry). vsniff won't guess which you mean; it
  lists each film's URL so you can grab them one at a time, each with its own
  `--title` and `--year`.
- **`--all`** works on a movie too: it keeps a copy that's already there and
  only fetches a missing subtitle file.

### Subtitles

Some streams carry subtitles as a separate track rather than burning them
into the picture. `ffmpeg -c copy` throws that track away, so vsniff saves it
alongside the video as a `.srt` file:

```
Ace of Diamond - S01E01 - WEBDL - 720p.mp4
Ace of Diamond - S01E01 - WEBDL - 720p.zh.srt
```

This is the sidecar naming Jellyfin looks for (`<video name>.<language>.srt`),
so the track appears in the subtitle menu after a library scan — no extra
setup, and the `.mp4` itself is untouched. Change the language code with
`--sub-lang` (e.g. `--sub-lang yue`).

It's automatic and best effort: episodes with a subtitle track get a sidecar,
episodes without one just download as before.

`--all` also **backfills** — an episode you already downloaded before subtitle
support existed gets its sidecar fetched without re-downloading the video, so
re-running the same command over an existing library is enough to fill the
gaps. The sidecar is named after the file actually on disk, so an episode
grabbed at a different resolution still pairs up. Series with no subtitle
track are detected on the first episode and skipped, rather than re-checking
every episode to learn the same thing.

Which sites have them:

| Site | Separate subtitle track? |
|------|--------------------------|
| **hkanime.com** | Sometimes — roughly 4 in 10 series carry one |
| **chinaq.net** | No — subtitles are burned into the picture, so there's nothing to save |
| **dramasq.io** | Not seen so far — subtitles are burned into the picture |

### Spliced-in ads

Some chinaq and dramasq sources splice ~15–20s gambling ads into the middle of
the video itself (seen on chinaq's DYun and Uyun, and dramasq's MYun and YYun).
dramasq and chinaq share many of the same upstream servers, so a source with
ads on one usually has them on the other too. vsniff removes them before downloading and
says so:

```
  ads: removed 4 spliced-in block(s), 71s total
```

A clip is only treated as an ad when it is short (≤60s), sits between
discontinuity markers, **and** either comes from a different folder on the
server than the episode or was encoded at a different frame rate (e.g. a 30fps
ad in a 25fps episode). If that would remove more than 20% of the episode,
nothing is removed. If you ever spot a missing scene, re-run with `--keep-ads`
to download the stream untouched.

### Downloading a whole series (`--all`)

Point vsniff at *any* episode of the series and add `--all`. It reads the
site's episode list, skips whatever is already in `--out`, and downloads the
rest. One episode failing doesn't stop the others.

```bash
# hkanime — every episode
./.venv/bin/vsniff \
  "https://www.hkanime.com/play/鋼之鍊金術師FA/120x0" \
  --series "Fullmetal Alchemist Brotherhood" --season 1 \
  --all --out ~/Movies/FMAB

# dramasq — every episode
./.venv/bin/vsniff \
  "https://dramasq.io/detail/202614591.html" \
  --series "Fall in Love with the Moon" --all --out ~/Movies/FallInLove

# chinaq — from episode 7 onwards, forcing a source
./.venv/bin/vsniff \
  "https://chinaq.net/video/1104-7.html#sid=3" \
  --series "Tigers Sniff the Rose" --all --from 7 --out ~/Movies/TigersSniffTheRose
```

`--out` is required with `--all`, and re-running the same command is safe: it
picks up where it left off. Add `--from N` to skip everything before episode N.

dramasq's series page can trail the site by an episode or two; vsniff also
checks for newer episodes past the last one listed, so it can pick up episodes
the series page doesn't link to yet.

Episode numbers come from the site's own labels, so a series that starts
part-way through a long run (hkanime's One Piece opens at EP517) is named
`S01E517`, not `S01E01`.

---

## How it works

Streaming sites don't hand you a downloadable file. The video is delivered as
**HLS**: a small text "playlist" (`.m3u8`) that lists hundreds of a few-second
chunks, which the browser fetches and plays back to back. To download the
video you collect all the chunks and reassemble them — the same thing browser
extensions like Video DownloadHelper do internally.

Two complications these sites add, which vsniff handles automatically:

1. **The stream URL is hidden and protected.** The link is tokened and only
   works when requested by the real player, with the exact "referer" header
   the site sends. You can't just read it off the page.
2. **The right stream may be behind one of several sources.** On chinaq and
   dramasq the default source is often dead and a different tab works.

vsniff works in two stages:

### Stage 1 — Discover (a real browser)

It launches an automated, invisible **Chromium** browser (via Playwright) and
loads the page exactly as a human would — for chinaq, trying each source in
turn. Because it's a genuine browser session, the player runs normally and
vsniff quietly **watches the network traffic**. The moment a working playlist
request goes by, it captures:

- the real `.m3u8` URL (with its token),
- the exact **referer** the browser sent (which the CDN requires),
- the **resolution** and **total length**, read from the playlist. When a
  single-quality playlist doesn't state its resolution (common on dramasq),
  `ffprobe` reads it from the first chunk instead — a second or two, not a
  download.

It never guesses tokens or headers — it observes the real ones the browser
produces. This is why it's site-agnostic: any page that plays HLS in a
browser exposes its stream this way.

### Stage 2 — Download + assemble (ffmpeg, with a progress bar)

It hands the captured playlist and referer to **ffmpeg**, which downloads
every chunk, decrypts if needed, and muxes them into one `.mp4` — copying the
video as-is (no re-encoding, so it's fast and lossless). ffmpeg reports its
progress, which vsniff renders as a live bar against the known total length.

### Site adapters

The sniff core is generic. Small **adapters** add per-site conveniences —
extracting the episode number from the URL, listing a series' episodes and,
for chinaq and dramasq, enumerating and trying sources. Where a site has an
API that hands over the stream directly (hkanime's play-api, dramasq's `/drq`
endpoint), the adapter uses it and only falls back to sniffing if it fails. A site with no adapter still works; you just pass `--episode`.
Adding a new site means writing one adapter module in `vsniff/sites/` and
listing it in `ADAPTERS` in `vsniff/sites/__init__.py` (see
[For developers](#for-developers)).

### The pipeline at a glance

```
page URL ─▶ [ browser: (try sources,) sniff the network ]
                        │
                        ├─▶ real .m3u8 + referer + resolution + length
                        │
                        ▼
             [ ffmpeg: fetch all chunks, mux, report progress ]
                        │
                        ▼
   "Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 720p.mp4"
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `command not found: brew` | Close and reopen Terminal, or re-run Homebrew's PATH lines. |
| `no working source found` / `no stream found` | Every source was dead, or the page needs interaction. Open it in a normal browser to confirm it plays, then retry. Try `--show` to watch. |
| `could not auto-detect the episode number` | The site has no adapter — add `--episode N`. |
| `vsniff search` finds nothing | Try the other script (simplified/traditional), a shorter piece of the title, or the Hong Kong/English name — see [Finding a show](#finding-a-show-vsniff-search). |
| `dramasq lists no numbered episodes` for a search result | The show was taken down; dramasq keeps its page but nothing plays. |
| Filename says `unknown` instead of `720p`/`1080p` | Neither the playlist nor `ffprobe` could tell the resolution; check `ffprobe -version` works. |
| The `#` in the URL breaks things | Wrap the whole URL in `"double quotes"`. |
| `ffmpeg: command not found` | `brew install ffmpeg` again. |
| `.venv/bin/vsniff: No such file or directory` | Your setup predates the `vsniff` command (it used to be `python vsniff.py`). Run `./.venv/bin/pip install -e .` once. |

**Sonarr bridge**

| Problem | Fix |
|---------|-----|
| Indexer **Test** fails | Check the URL (`http://vsniff-bridge:8990`, reachable from Sonarr's container) and the API key; `docker logs vsniff-bridge` shows why. |
| Download client Test warns about the category | Set the category to one in `VSNIFF_CATEGORIES` (default `tv`). |
| A show you know is on a site finds nothing | The bridge logs every rejected result with its reason at debug level: run with `--log-level debug`. Often the site's title differs from every name Sonarr and TMDB have for the show. |
| Searches are slow the first time | Each site is searched in several spellings, paced to avoid rate limits, and each source is probed once for its resolution. Results are cached, so repeats are quick. |
| Sonarr imports nothing after "Completed" | The download folder must be the same path in both containers (see [Running it](#running-it-docker)). |
| Wrong source pinned for a season | Grab the episode you want from interactive search; that source becomes the season's pin. Pins are listed on the status page. |

---

## For developers

vsniff is a small Python package:

```
vsniff/
  api.py          using vsniff from Python: search, Session (catalog/probe/download)
  cli.py          argument parsing; single-episode, --all, movie and search flows
  discovery.py    find one stream using a browser session
  sniff.py        capture an .m3u8 from a page's network traffic
  playlist.py     resolution (ffprobe fallback) / length / subtitles; ad-stripped copies
  ads.py          spliced-in ad detection (pure text processing)
  download.py     ffmpeg download with progress bar or callback; subtitle sidecars
  naming.py       Sonarr/Jellyfin filenames; what's already on disk
  common.py       user agent, VsniffError, URL and text helpers
  sites/          one adapter per site, plus GenericSite (the fallback)
  bridge/         the Sonarr bridge service (see below)
tests/            one test file per module (test_bridge_* for the bridge)
```

**Adding a site:** create `vsniff/sites/<site>.py` with a subclass of
`GenericSite` (see `base.py` for the methods you can override: `matches`,
`episode`, `discover`, `catalog` + `supports_batch = True` for `--all`, and
`search` + `supports_search = True` for `vsniff search`). Keep each site's
parsing in a plain function over the page text (`parse_search`,
`parse_episodes`) so it can be tested without the network.
Then add an instance to `ADAPTERS` in `vsniff/sites/__init__.py`, ahead of
`GenericSite()`. `dramasq.py` is a good template for a site with several
sources per episode; `hkanime.py` for one with a single stream per episode.

**Using vsniff from Python:** `vsniff.api` exposes what the CLI does, for a
program that drives vsniff (the Sonarr bridge is built on it):

```python
from vsniff import api

results = api.search("尚公主")                     # [SearchResult(site, title, url, note, episodes)]
with api.Session() as s:                          # one headless browser, reused
    episodes = s.catalog(results[0].url)          # [(episode, page_url)]
    info = s.probe(episodes[0][1])                # StreamInfo(source, resolution, duration, subtitles)
    done = s.download_episode(
        episodes[0][1], "/media/downloads",
        series="Tigers Sniff the Rose", season=1, episode=episodes[0][0],
        on_progress=lambda done_s, total_s: print(done_s, total_s))
    print(done.path, done.resolution, done.subtitles_path)
```

- `search()` asks every searchable site and skips (and logs) one that fails,
  so a site being down doesn't lose the others' results.
- `probe()` finds the working stream and reports its quality without
  downloading, for comparing sources before choosing one.
- `download_episode()` names the file from the stream's real resolution and
  returns the path; `on_progress(done_seconds, total_seconds)` replaces the
  terminal progress bar (`total_seconds` may be `None`).
- A `Session` starts its browser on first use. Playwright is single-threaded:
  give each worker thread its own `Session`. Errors raise `VsniffError`.

**The Sonarr bridge** lives in `vsniff/bridge/`:

```
vsniff/bridge/
  app.py          FastAPI service: Newznab + SABnzbd endpoints, status page
  newznab.py      caps / release feed / placeholder .nzb, shaped by Sonarr's parser
  sabnzbd.py      version / config / queue / history answers Sonarr's client checks
  resolver.py     Sonarr request -> releases; picks and remembers each season's pin
  matching.py     the gates a search result must pass to count as a show's season
  textnorm.py     Chinese title normalising, season/year/language markers, queries
  metadata.py     Sonarr (numbering) + TMDB (Chinese titles, season names)
  access.py       paced, cached site searches; one browser thread for lists/probes
  worker.py       download workers (one vsniff Session each)
  store.py        SQLite: pins, releases, jobs, probes
  config.py       settings from environment variables
```

The protocol details (which fields Sonarr reads, which answers make its Test
pass) come from Sonarr 4's own client code, noted at the top of `newznab.py`
and `sabnzbd.py`.

**Running the tests:**

```bash
./.venv/bin/pip install -e ".[bridge,dev]"
./.venv/bin/python -m pytest
```

Without the `bridge` extra the bridge tests are skipped.

When a test stubs out a function, patch it on the module that *calls* it
(e.g. `monkeypatch.setattr(dramasq, "sniff_once", ...)`, not on
`vsniff.sniff`), since each module imports the names it uses.

---

## Notes

- **For personal, offline use.** These sites host content they likely don't
  license, so keep downloads to yourself.
- **No DRM is bypassed.** vsniff only records and reassembles ordinary HLS
  streams a browser already plays. It will **not** work on DRM-protected
  services (Netflix, Disney+, etc.), by design.
