# vsniff

A general **streaming-video sniffer and downloader**. Point it at a video
page and it captures whatever stream the page plays, downloads it in full
quality, and saves a clean `.mp4` named the Sonarr/Jellyfin way:

```
Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 1080p.mp4
```

It works on any streaming page that plays HLS video in a browser. Three sites
get extra polish (automatic episode numbers, whole-series downloads and, for
chinaq and dramasq, source selection):

| Site | Auto episode # | Notes |
|------|:---:|-------|
| **chinaq.net** | ✅ | tries each playback source until one works |
| **hkanime.com** | ✅ | episode list + streams come from its play-api |
| **dramasq.io** | ✅ | tries each playback source until one works; no browser needed |
| **any other site** | — | works too; you supply `--episode` |

---

## What you need

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

## Setup (one time, ~10 minutes)

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

## Using it

The basic form:

```bash
./.venv/bin/vsniff "<PAGE_URL>" --series "<Show Name>" --season <N>
```

> **Always wrap the URL in "quotes"** — the `#` and special characters in
> these URLs confuse the Terminal otherwise.

### chinaq.net

```bash
./.venv/bin/vsniff \
  "https://chinaq.net/video/68261-7.html#sid=9" \
  --series "Blossoms of Power" --season 1
```

→ `Blossoms of Power - S01E07 - WEBDL - 1080p.mp4`

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

### While it downloads

You'll see a live progress bar:

```
  [##############------------------]  44.2%  10:48/24:30
```

### All options

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
  "https://chinaq.net/video/68261-7.html#sid=9" \
  --series "Blossoms of Power" --all --from 7 --out ~/Movies/Blossoms
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
- the **resolution** and **total length**, read from the playlist.

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
| The `#` in the URL breaks things | Wrap the whole URL in `"double quotes"`. |
| `ffmpeg: command not found` | `brew install ffmpeg` again. |
| `.venv/bin/vsniff: No such file or directory` | Your setup predates the `vsniff` command (it used to be `python vsniff.py`). Run `./.venv/bin/pip install -e .` once. |

---

## For developers

vsniff is a small Python package:

```
vsniff/
  cli.py          argument parsing; single-episode, --all and movie flows
  discovery.py    find one stream using a browser session
  sniff.py        capture an .m3u8 from a page's network traffic
  playlist.py     resolution / length / subtitles; serves ad-stripped copies
  ads.py          spliced-in ad detection (pure text processing)
  download.py     ffmpeg download with progress bar; subtitle sidecars
  naming.py       Sonarr/Jellyfin filenames; what's already on disk
  common.py       user agent, VsniffError, URL helpers
  sites/          one adapter per site, plus GenericSite (the fallback)
tests/            one test file per module
```

**Adding a site:** create `vsniff/sites/<site>.py` with a subclass of
`GenericSite` (see `base.py` for the methods you can override: `matches`,
`episode`, `discover`, and `catalog` + `supports_batch = True` for `--all`).
Then add an instance to `ADAPTERS` in `vsniff/sites/__init__.py`, ahead of
`GenericSite()`. `dramasq.py` is a good template for a site with several
sources per episode; `hkanime.py` for one with a single stream per episode.

**Running the tests:**

```bash
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest
```

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
