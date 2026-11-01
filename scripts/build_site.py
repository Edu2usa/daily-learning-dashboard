#!/usr/bin/env python3
"""Generate the Daily Learning static dashboard.

Stdlib-only. Reads the canonical source tree (default: ../../daily-learning
relative to this script) and writes a complete static site into docs/.

Source data is never modified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
LINK_RE = re.compile(r"<?(https?://[^\s)>]+)>?")

SECTIONS = {
    "most useful ideas": "ideas",
    "concrete job change": "change",
    "public sources": "sources",
}


def parse_bots(path: Path) -> dict:
    """Parse bots.json into {bot_id: bot_dict}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    bots = {}
    for b in data.get("bots", []):
        bots[b["bot"]] = {
            "bot": b["bot"],
            "name": b.get("name", b["bot"]),
            "role": b.get("role", ""),
            "group": b.get("group", "business"),
            "schedule": b.get("schedule", ""),
        }
    return bots


def parse_books_ledger(path: Path) -> tuple[list[dict], list[str]]:
    """Parse books-read.md pipe table. Returns (rows, warnings).

    Duplicate (date, bot) rows and duplicate titles are reported as warnings
    and the first occurrence wins.
    """
    warnings: list[str] = []
    rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_titles: set[str] = set()
    if not path.exists():
        return rows, [f"missing ledger file: {path.name}"]
    lines = path.read_text(encoding="utf-8").splitlines()
    header_seen = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if set(line.replace(" ", "")) <= {"-", "|"}:
            continue  # separator row
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not header_seen:
            header_seen = True
            if cells and cells[0].lower() == "date":
                continue  # header row
        if len(cells) < 4:
            warnings.append(f"ledger row skipped (needs 4 columns): {line!r}")
            continue
        date, bot, title, author = cells[0], cells[1], cells[2], cells[3]
        key = (date, bot)
        if key in seen_keys:
            warnings.append(f"duplicate ledger row for {date}/{bot}: {title!r}")
            continue
        if title.lower() in seen_titles:
            warnings.append(f"duplicate book title across bots: {title!r}")
            continue
        seen_keys.add(key)
        seen_titles.add(title.lower())
        rows.append({"date": date, "bot": bot, "title": title, "author": author})
    return rows, warnings


def parse_entry(md_text: str) -> dict:
    """Parse one dated bot markdown file into a structured entry."""
    entry: dict = {"title": "", "author": "", "bot": "", "date": "",
                   "ideas": [], "change": "", "sources": [], "meta": {}}
    section = None
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if i == 0 and stripped.startswith("# "):
            title_line = stripped[2:].strip()
            if "—" in title_line:
                title, _, author = title_line.partition("—")
            elif " - " in title_line and len(title_line.split(" - ")) == 2:
                title, author = title_line.split(" - ")
            else:
                title, author = title_line, ""
            entry["title"] = title.strip()
            entry["author"] = author.strip()
        elif stripped.startswith("- **") and ":**" in stripped:
            key, _, val = stripped[4:].partition(":**")
            entry["meta"][key.strip().lower()] = val.strip()
        elif stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            section = SECTIONS.get(header, None)
        elif section == "ideas":
            m = BULLET_RE.match(stripped)
            if m and m.group(1):
                entry["ideas"].append(m.group(1).strip())
        elif section == "change" and stripped:
            entry["change"] = (entry["change"] + " " + stripped).strip()
        elif section == "sources":
            m = BULLET_RE.match(stripped)
            if m:
                urls = LINK_RE.findall(m.group(1))
                if urls:
                    entry["sources"].extend(urls)
        i += 1
    if entry["meta"].get("bot"):
        entry["bot"] = entry["meta"]["bot"]
    if entry["meta"].get("date"):
        entry["date"] = entry["meta"]["date"]
    return dict(entry, section=None)


def load_entries(source: Path) -> tuple[dict, list[str]]:
    """Load all dated entries. Returns ({date: {bot: entry}}, warnings)."""
    warnings: list[str] = []
    days: dict[str, dict[str, dict]] = {}
    if not source.is_dir():
        return days, [f"missing source directory: {source}"]
    for child in sorted(source.iterdir()):
        if not (child.is_dir() and DATE_RE.match(child.name)):
            continue
        date = child.name
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            warnings.append(f"invalid date directory ignored: {date}")
            continue
        for md in sorted(child.glob("*.md")):
            bot_id = md.stem
            entry = parse_entry(md.read_text(encoding="utf-8"))
            entry["date"] = date
            if not entry["bot"]:
                entry["bot"] = bot_id
            if entry["bot"] != bot_id:
                warnings.append(
                    f"{date}/{bot_id}.md: Bot metadata {entry['bot']!r} "
                    "does not match filename; using filename")
                entry["bot"] = bot_id
            if not entry["title"]:
                warnings.append(f"{date}/{bot_id}.md: missing title heading")
                entry["title"] = bot_id
            if not entry["ideas"]:
                warnings.append(f"{date}/{bot_id}.md: no 'Most useful ideas' found")
            if not entry["sources"]:
                warnings.append(f"{date}/{bot_id}.md: no 'Public sources' found")
            if bot_id in days.setdefault(date, {}):
                warnings.append(f"duplicate entry file for {date}/{bot_id}")
            days[date][bot_id] = entry
    return days, warnings


# --------------------------------------------------------------------------
# Derived data
# --------------------------------------------------------------------------

def compute_stats(days: dict, bots: dict) -> dict:
    dates = sorted(days)
    filed_days = len(dates)
    # Streak: consecutive scheduled learning days ending at the latest date.
    # Sunday is intentionally unscheduled; skip it only when no manual filing exists.
    streak = 0
    if dates:
        cursor = dt.date.fromisoformat(dates[-1])
        while True:
            key = cursor.isoformat()
            if key in days:
                streak += 1
                cursor -= dt.timedelta(days=1)
                continue
            if cursor.weekday() == 6:  # Sunday
                cursor -= dt.timedelta(days=1)
                continue
            break
    per_bot = {b: sum(1 for d in dates for e in [days[d].get(b)] if e) for b in bots}
    total_entries = sum(len(days[d]) for d in dates)
    return {"dates": dates, "filed_days": filed_days, "streak": streak,
            "per_bot": per_bot, "total_entries": total_entries,
            "total_books": total_entries}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def e(text: str) -> str:
    return html.escape(str(text), quote=True)


CSS = """:root{--olive:#6b7f3c;--olive-dark:#4c5c2a;--olive-light:#eef2e0;--ink:#1f2417;--ink-soft:#4a5240;--cream:#faf7ee;--card:#fffdf6;--line:#dfe4cf;--warn:#a4531f;--warn-bg:#fdf3e7}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--cream);color:var(--ink);line-height:1.55;font-size:16px}
header.site{background:var(--olive-dark);color:var(--cream);padding:1.1rem 1rem .9rem}
header.site h1{margin:0;font-size:1.35rem;letter-spacing:.02em}
header.site p{margin:.2rem 0 0;font-size:.85rem;color:#dfe7c8}
nav{display:flex;flex-wrap:wrap;gap:.4rem;padding:.5rem 1rem;background:var(--olive-light);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
nav a{color:var(--olive-dark);text-decoration:none;font-size:.85rem;font-weight:600;padding:.3rem .7rem;border-radius:999px;border:1px solid var(--line);background:var(--card)}
nav a[aria-current=page]{background:var(--olive);color:#fff;border-color:var(--olive)}
main{max-width:60rem;margin:0 auto;padding:1rem}
h2{font-size:1.1rem;color:var(--olive-dark);margin:1.6rem 0 .6rem;border-bottom:2px solid var(--olive);display:inline-block;padding-bottom:.15rem}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(8.5rem,1fr));gap:.6rem;margin:.8rem 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:.7rem;padding:.6rem .8rem}
.stat b{display:block;font-size:1.4rem;color:var(--olive-dark)}
.stat span{font-size:.75rem;color:var(--ink-soft)}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--olive);border-radius:.7rem;padding:.9rem 1rem;margin:.8rem 0}
.card.missing{border-left-color:var(--warn);background:var(--warn-bg)}
.card h3{margin:.1rem 0 .2rem;font-size:1.05rem}
.card .meta{font-size:.78rem;color:var(--ink-soft);margin-bottom:.5rem}
.card .meta a{color:var(--olive-dark)}
.card ul{margin:.35rem 0 .35rem 1.1rem;padding:0}
.card li{margin:.25rem 0;font-size:.92rem}
.change{background:var(--olive-light);border-radius:.5rem;padding:.55rem .7rem;font-size:.92rem;margin:.5rem 0}
.change b{color:var(--olive-dark)}
.tag{display:inline-block;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;background:var(--olive);color:#fff;border-radius:.3rem;padding:.1rem .45rem;vertical-align:middle}
.tag.personal{background:#8a6d3b}
.tag.missing{background:var(--warn)}
.day-label{font-size:.8rem;font-weight:700;color:var(--ink-soft);margin:1.4rem 0 .2rem;text-transform:uppercase;letter-spacing:.05em}
.searchbar{position:sticky;top:2.9rem;z-index:4;margin:.8rem 0;display:flex;gap:.5rem;flex-wrap:wrap}
.searchbar input{flex:1;min-width:12rem;padding:.55rem .8rem;border:1px solid var(--line);border-radius:.5rem;font-size:.95rem;background:#fff}
.searchbar select{padding:.55rem;border:1px solid var(--line);border-radius:.5rem;background:#fff}
.noresults{display:none;color:var(--warn);font-weight:600}
table.books{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:.5rem;overflow:hidden;font-size:.92rem}
table.books th,table.books td{text-align:left;padding:.5rem .7rem;border-bottom:1px solid var(--line)}
table.books th{background:var(--olive-light);color:var(--olive-dark);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em}
footer{margin:2rem 0 1.5rem;padding-top:1rem;border-top:1px solid var(--line);font-size:.78rem;color:var(--ink-soft)}
footer a{color:var(--olive-dark)}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid var(--olive);outline-offset:2px}
@media(min-width:48rem){body{font-size:17px}header.site h1{font-size:1.7rem}main{padding:1.4rem}}"""


def icon_png(size: int, color=(107, 127, 60)) -> bytes:
    """Render a simple solid-olive rounded look: solid square PNG (stdlib only)."""
    r, g, b = color
    row = b"\x00" + bytes([r, g, b]) * size
    raw = row * size
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#6b7f3c"/><path d="M18 16h20a6 6 0 0 1 6 6v26H24a6 6 0 0 1-6-6z" fill="#faf7ee"/><path d="M24 24h14M24 31h14M24 38h9" stroke="#4c5c2a" stroke-width="3" stroke-linecap="round"/></svg>"""


def source_link(entry: dict, base: str) -> str:
    path = f"{base}/{entry['date']}/{entry['bot']}.md"
    return f'<a href="{e(base.rstrip("/") + "/" + entry["date"] + "/" + entry["bot"] + ".md")}">{e(path)}</a>'


def render_card(entry: dict, bot: dict, base: str, missing: bool = False) -> str:
    if missing:
        return (f'<article class="card missing" data-bot="{e(bot["bot"])}" data-group="{e(bot["group"])}">'
                f'<span class="tag missing">Missing filing</span> '
                f'<h3>{e(bot["name"])}</h3>'
                f'<p class="meta">No learning filed for {e(entry["date"])}. '
                f'The morning brief reports missing files rather than inventing content.</p></article>')
    group = bot.get("group", "business")
    src = source_link(entry, base)
    ideas = "".join(f"<li>{e(i)}</li>" for i in entry["ideas"])
    sources = "".join(f'<li><a href="{e(u)}" rel="noopener">{e(u)}</a></li>' for u in entry["sources"])
    return (f'<article class="card" data-bot="{e(bot["bot"])}" data-group="{e(group)}" '
            f'data-title="{e(entry["title"].lower())}" data-text="{e((entry["title"] + " " + entry["author"] + " " + " ".join(entry["ideas"])).lower())}">'
            f'<span class="tag{" personal" if group == "personal" else ""}">{e("Personal" if group == "personal" else "Business")}</span> '
            f'<h3>{e(entry["title"])} — {e(entry["author"])}</h3>'
            f'<p class="meta">{e(bot["name"])} · {e(entry["date"])} · source file: {src}</p>'
            f'<p><b>Most useful ideas</b></p><ul>{ideas}</ul>'
            f'<div class="change"><b>Concrete job change:</b> {e(entry["change"])}</div>'
            f'<p class="meta"><b>Public sources:</b></p><ul>{sources}</ul></article>')


NAV = [("index.html", "Today", "index.html"), ("archive.html", "Archive", "archive.html"),
       ("by-bot.html", "By Bot", "by-bot.html"), ("books.html", "Books Read", "books.html")]


def shell(title: str, active: str, body: str, description: str) -> str:
    nav = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if href == active else ""}>{label}</a>'
        for href, label, _ in NAV)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{e(description)}">
<title>{e(title)}</title>
<link rel="stylesheet" href="styles.css">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icons/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icons/icon-192.png">
<script src="app.js" defer></script>
</head>
<body>
<a class="sr-only" href="#main">Skip to content</a>
<header class="site"><h1>Daily Learning</h1>
<p>One legitimate public-source book study per real LLM-backed bot, Monday–Saturday.</p></header>
<nav aria-label="Site">{nav}</nav>
<main id="main">
{body}
</main>
<footer>Static site · no tracking, no backend, no private data ·
built from the public <a href="https://github.com/Edu2usa/daily-learning-dashboard/tree/main/data">daily-learning</a> source tree.</footer>
</body>
</html>"""


def searchbar(page_id: str) -> str:
    return ('<div class="searchbar" role="search">'
            '<label class="sr-only" for="q-' + page_id + '">Filter cards</label>'
            f'<input id="q-{page_id}" type="search" placeholder="Filter ideas, books, bots…" autocomplete="off">'
            '<label class="sr-only" for="grp-' + page_id + '">Group</label>'
            '<select id="grp-' + page_id + '"><option value="">All groups</option>'
            '<option value="business">Business</option><option value="personal">Personal</option></select>'
            '<label class="sr-only" for="bot-' + page_id + '">Bot</label>'
            '<select id="bot-' + page_id + '"><option value="">All bots</option></select>'
            '<p class="noresults" id="nr-' + page_id + '">No cards match your filter.</p></div>')


def build_pages(source: Path, base: str) -> dict[str, str]:
    bots = parse_bots(source / "bots.json")
    ledger, ledger_warnings = parse_books_ledger(source / "books-read.md")
    days, warnings = load_entries(source)
    stats = compute_stats(days, bots)
    pages: dict[str, str] = {}

    # ---- index.html: Today ----
    today = stats["dates"][-1] if stats["dates"] else None
    parts = []
    parts.append('<section aria-label="Stats"><div class="stats">'
                 f'<div class="stat"><b>{stats["filed_days"]}</b><span>Days filed</span></div>'
                 f'<div class="stat"><b>{stats["streak"]}</b><span>Current streak (days)</span></div>'
                 f'<div class="stat"><b>{stats["total_entries"]}</b><span>Entries</span></div>'
                 f'<div class="stat"><b>{len(bots)}</b><span>Active bots</span></div>'
                 '</div></section>')
    if today:
        parts.append(f'<h2 id="today">Today — {e(today)}</h2>')
        parts.append(searchbar("today"))
        parts.append('<h2 id="business">Business bots</h2>')
        for bot_id, bot in bots.items():
            if bot.get("group") == "personal":
                continue
            entry = days.get(today, {}).get(bot_id)
            if entry:
                parts.append(render_card(entry, bot, base))
            else:
                parts.append(render_card({"date": today}, bot, base, missing=True))
    else:
        parts.append('<p>No dated filings found yet. Cards appear once the first '
                     '<code>YYYY-MM-DD</code> directory exists in the source tree.</p>')
    personal = [b for b in bots.values() if b["group"] == "personal"]
    if today and personal:
        parts.append('<h2 id="personal">Personal life</h2>')
        for bot in personal:
            entry = days.get(today, {}).get(bot["bot"])
            if entry:
                parts.append(render_card(entry, bot, base))
            else:
                parts.append(render_card({"date": today}, bot, base, missing=True))
    pages["index.html"] = shell("Daily Learning — Today", "index.html",
                                "\n".join(parts), "Daily book study dashboard for a team of LLM bots.")

    # ---- archive.html ----
    parts = [f'<h2>Archive — {stats["filed_days"]} day(s)</h2>', searchbar("archive")]
    for date in reversed(stats["dates"]):
        parts.append(f'<p class="day-label" id="{e(date)}">{e(date)}</p>')
        for bot_id, bot in bots.items():
            entry = days[date].get(bot_id)
            if entry:
                parts.append(render_card(entry, bot, base))
            else:
                parts.append(render_card({"date": date}, bot, base, missing=True))
    pages["archive.html"] = shell("Daily Learning — Archive", "archive.html",
                                  "\n".join(parts), "Full archive of daily learning filings.")

    # ---- by-bot.html ----
    parts = ["<h2>By bot</h2>", searchbar("bybot")]
    for bot_id, bot in bots.items():
        parts.append(f'<h3 class="day-label" id="bot-{e(bot_id)}">{e(bot["name"])} '
                     f'<span class="tag{" personal" if bot["group"] == "personal" else ""}">'
                     f'{e("Personal" if bot["group"] == "personal" else "Business")}</span></h3>'
                     f'<p class="meta">{e(bot["role"])} · schedule <code>{e(bot["schedule"])}</code> · '
                     f'{stats["per_bot"].get(bot_id, 0)} filing(s)</p>')
        for date in reversed(stats["dates"]):
            entry = days[date].get(bot_id)
            if entry:
                parts.append(render_card(entry, bot, base))
            else:
                parts.append(render_card({"date": date}, bot, base, missing=True))
    pages["by-bot.html"] = shell("Daily Learning — By Bot", "by-bot.html",
                                 "\n".join(parts), "Learning entries grouped by bot.")

    # ---- books.html ----
    rows = []
    seen: set[tuple[str, str]] = set()
    for r in ledger:
        if (r["date"], r["bot"]) in seen:
            continue
        seen.add((r["date"], r["bot"]))
        rows.append(f"<tr><td>{e(r['date'])}</td><td>{e(bots.get(r['bot'], {}).get('name', r['bot']))}</td>"
                    f"<td>{e(r['title'])}</td><td>{e(r['author'])}</td></tr>")
    if not rows:
        rows.append('<tr><td colspan="4">No books recorded yet.</td></tr>')
    pages["books.html"] = shell("Daily Learning — Books Read", "books.html",
                                '<h2>Books Read</h2><table class="books"><thead><tr>'
                                '<th scope="col">Date</th><th scope="col">Bot</th>'
                                '<th scope="col">Title</th><th scope="col">Author</th></tr></thead>'
                                f'<tbody>{"".join(rows)}</tbody></table>',
                                "Every book studied, from the canonical ledger.")
    return pages, warnings + ledger_warnings


def build_bot_filter_options(bots: dict) -> str:
    return "".join(f'<option value="{e(b["bot"])}">{e(b["name"])}</option>' for b in bots.values())


APP_JS = """(function () {
  "use strict";
  var bots = JSON.parse(document.getElementById("bot-options").textContent);
  document.querySelectorAll("select[id^=bot-]").forEach(function (sel) {
    bots.forEach(function (b) {
      var o = document.createElement("option");
      o.value = b.bot; o.textContent = b.name; sel.appendChild(o);
    });
  });
  document.querySelectorAll(".searchbar").forEach(function (bar) {
    var page = bar.id ? bar.id : bar.querySelector("input").id.replace(/^q-/, "");
    var q = bar.querySelector("input"), grp = bar.querySelector("select[id^=grp-]"),
        bot = bar.querySelector("select[id^=bot-]"), nr = bar.querySelector(".noresults");
    function apply() {
      var term = (q.value || "").toLowerCase(), g = grp.value, b = bot.value, shown = 0;
      document.querySelectorAll("article.card").forEach(function (c) {
        var ok = (!term || (c.getAttribute("data-text") || "").indexOf(term) !== -1)
              && (!g || c.getAttribute("data-group") === g)
              && (!b || c.getAttribute("data-bot") === b);
        c.hidden = !ok; if (ok) shown++;
      });
      document.querySelectorAll(".day-label").forEach(function (l) {
        var el = l.nextElementSibling, any = false;
        while (el && !el.classList.contains("day-label")) {
          if (el.tagName === "ARTICLE" && !el.hidden) { any = true; break; }
          el = el.nextElementSibling;
        }
        l.hidden = !any;
      });
      nr.style.display = shown === 0 ? "block" : "none";
    }
    [q, grp, bot].forEach(function (el) { el.addEventListener("input", apply); });
  });
})();
"""


def write_site(out: Path, pages: dict, bots: dict) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "icons").mkdir(parents=True)
    (out / ".nojekyll").touch()
    (out / "styles.css").write_text(CSS, encoding="utf-8")
    (out / "app.js").write_text(APP_JS.replace('"__BOT_OPTIONS__"', ""), encoding="utf-8")
    (out / "icons" / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
    (out / "icons" / "icon-192.png").write_bytes(icon_png(192))
    (out / "icons" / "icon-512.png").write_bytes(icon_png(512))
    (out / "manifest.webmanifest").write_text(json.dumps({
        "name": "Daily Learning",
        "short_name": "Learning",
        "start_url": "./index.html",
        "display": "standalone",
        "background_color": "#faf7ee",
        "theme_color": "#4c5c2a",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "icons/icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    }, indent=2), encoding="utf-8")
    options_html = ('<script type="application/json" id="bot-options">'
                    + json.dumps(list(bots.values())) + "</script>")
    for name, page in pages.items():
        (out / name).write_text(page.replace("</main>", options_html + "</main>"),
                                encoding="utf-8")


WORKFLOW = """name: Deploy Daily Learning to GitHub Pages
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Generate site
        run: python3 scripts/build_site.py --source data --source-base https://github.com/Edu2usa/daily-learning-dashboard/tree/main/data
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: docs
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
"""


def main(argv: list[str] | None = None) -> int:
    project = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=project.parent.parent / "daily-learning")
    ap.add_argument("--out", type=Path, default=project / "docs")
    ap.add_argument("--source-base", default="data",
                    help="relative URL base for source-file links in the site")
    args = ap.parse_args(argv)
    pages, warnings = build_pages(args.source, args.source_base)
    write_site(args.out, pages, parse_bots(args.source / "bots.json")
               if (args.source / "bots.json").exists() else {})
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"built {len(pages)} pages into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
