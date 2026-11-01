# Daily Learning Dashboard

A static, mobile-first dashboard for the public `data/` snapshot generated from the private daily-learning source
program: one legitimate public-source book study per real LLM-backed bot,
Monday–Saturday.

## What it shows

- **Today** — the latest filing date: one card per bot (book, author, most
  useful ideas, concrete job change, public sources), plus a dedicated
  **Personal life** section for personal-group bots.
- **Missing filing states** — any active bot without a filing for a date gets
  an explicit "Missing filing" card (never invented content, matching the
  source program's rule).
- **Archive** — every filed date, newest first.
- **By Bot** — every entry grouped per bot, with role, schedule, and count.
- **Books Read** — the canonical ledger rendered as a table.
- **Stats / streak** — days filed, current consecutive-day streak, entry and
  bot counts.
- **Search / filter** — a sticky text filter plus group and bot dropdowns.
  Content is fully readable without JavaScript; the filter is progressive
  enhancement only.

## Build

Python 3.10+, standard library only:

```bash
python3 scripts/build_site.py --source data --out docs
python3 scripts/build_site.py --source PATH   # custom canonical source tree
```

`scripts/build_site.py` is read-only toward the source tree; it parses
`bots.json`, `books-read.md`, and `YYYY-MM-DD/<bot>.md` files and writes the
complete site into `docs/`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Covers entry/ledger parsing, missing files and directories, duplicate ledger
rows/titles, streak math, full-site output, relative-URL-only pages, and that
the source tree is byte-for-byte unchanged after a build.

## Privacy

- No tracking, analytics, cookies, backends, or external requests from the
  site itself.
- No private data: only bot names/roles/schedules, book titles/authors,
  distilled ideas, and links to legitimately public sources are published.
- Source-file links point at the public repository (`--source-base` controls
  the base URL; default is a relative path for local browsing).

## Deploy (GitHub Pages)

1. Sync the allowlisted public learning files into `data/`.
2. Settings → Pages → Source: **Deploy from a branch**.
3. Select `main` and `/docs`; the local publisher regenerates and pushes both `data/` and `docs/`.
4. All URLs in the generated site are relative, so it works at any Pages base
   path. `docs/.nojekyll` is included so Jekyll processing is skipped.
5. A PWA manifest and icons (`manifest.webmanifest`, `icons/`) make the site
   installable.
