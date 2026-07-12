# vsniff

A general **streaming-video sniffer and downloader**. Point it at a video
page and it captures whatever stream the page plays, downloads it in full
quality, and saves a clean `.mp4` named the Sonarr/Jellyfin way:

```
Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 1080p.mp4
```

It works on any streaming page that plays HLS video in a browser. Two sites
get extra polish (automatic episode numbers and, for chinaq, source
selection):

| Site | Auto episode # | Notes |
|------|:---:|-------|
| **chinaq.net** | ✅ | tries each playback source until one works |
| **hkanime.com** | ✅ | source is encoded in the URL |
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

Open Terminal inside the folder that contains `vsniff.py` (the one this
README is in). For example, if it's in your home folder:

```bash
cd ~/chinaq-dl        # the folder name may differ; it's wherever vsniff.py lives
```

Every command below assumes you're in that folder. Get back to it any time
with the same `cd` command.

### Step 4 — Set up the tool's private workspace

Python projects keep their dependencies in a private `.venv` folder so they
don't clash with anything else on your Mac. Create it and install what's
needed:

```bash
python3 -m venv .venv                          # create the private workspace
./.venv/bin/pip install -r requirements.txt    # install Playwright
./.venv/bin/playwright install chromium        # download the automated browser (~100MB)
```

### Step 5 — Confirm it's ready

```bash
./.venv/bin/python vsniff.py -h
```

If you see a help message, setup is complete. 🎉

---

## Using it

The basic form:

```bash
./.venv/bin/python vsniff.py "<PAGE_URL>" --series "<Show Name>" --season <N>
```

> **Always wrap the URL in "quotes"** — the `#` and special characters in
> these URLs confuse the Terminal otherwise.

### chinaq.net

```bash
./.venv/bin/python vsniff.py \
  "https://chinaq.net/video/68261-7.html#sid=9" \
  --series "Blossoms of Power" --season 1
```

→ `Blossoms of Power - S01E07 - WEBDL - 1080p.mp4`

### hkanime.com

```bash
./.venv/bin/python vsniff.py \
  "https://www.hkanime.com/play/鋼之鍊金術師FA/120x0" \
  --series "Fullmetal Alchemist Brotherhood" --season 1
```

→ `Fullmetal Alchemist Brotherhood - S01E01 - WEBDL - 720p.mp4`
(the `x0` in the URL is the first episode → E01)

### Any other site

vsniff will still sniff and download; it just can't guess the episode number,
so provide it:

```bash
./.venv/bin/python vsniff.py "<page url>" --series "Some Show" --episode 3
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
| `--series` | ✅ | — | English show title (used in the filename) |
| `--season` | | `1` | Season number |
| `--episode` | | auto | Episode number (auto for chinaq/hkanime; required elsewhere) |
| `--quality` | | `WEBDL` | Quality tag in the filename |
| `--source` | | auto | **chinaq only:** force a source by number (`9`) or name (`ZYun`) |
| `--out` | | `.` (here) | Folder to save into; supports `~` and `$VARS`, e.g. `--out ~/Movies` |
| `--show` | | off | Show the browser window (for debugging) |

### chinaq sources (`--source`)

Each chinaq episode has several playback sources (the 片源1, 片源2 … tabs,
named `BYun`, `ZYun`, …). Some are dead at any time.

- **Do nothing** → vsniff tries each until one plays (honoring a `#sid=N` in
  your URL first).
- **Force one** → `--source 9` (number) or `--source ZYun` (name, any case).

hkanime encodes its source in the URL (the `120` in `120x0`), so `--source`
isn't needed there.

### Downloading several episodes

Run it once per episode, changing the number in the URL:

```bash
# chinaq: 68261-7, 68261-8, 68261-9 ...
for n in 7 8 9; do
  ./.venv/bin/python vsniff.py \
    "https://chinaq.net/video/68261-$n.html#sid=9" \
    --series "Blossoms of Power" --season 1
done

# hkanime: 120x0, 120x1, 120x2 ...  (episode = index + 1)
for i in 0 1 2; do
  ./.venv/bin/python vsniff.py \
    "https://www.hkanime.com/play/鋼之鍊金術師FA/120x$i" \
    --series "Fullmetal Alchemist Brotherhood" --season 1
done
```

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
2. **The right stream may be behind one of several sources.** On chinaq the
   default source is often dead and a different tab works.

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
extracting the episode number from the URL and, for chinaq, enumerating and
trying sources. A site with no adapter still works; you just pass `--episode`.
Adding a new site is a small adapter class in `vsniff.py`.

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

---

## Notes

- **For personal, offline use.** These sites host content they likely don't
  license, so keep downloads to yourself.
- **No DRM is bypassed.** vsniff only records and reassembles ordinary HLS
  streams a browser already plays. It will **not** work on DRM-protected
  services (Netflix, Disney+, etc.), by design.
