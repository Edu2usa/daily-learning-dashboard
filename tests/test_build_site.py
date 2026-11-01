"""Unittest coverage for the Daily Learning dashboard generator.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_site as bs  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


BOT = {
    "version": 1,
    "bots": [
        {"bot": "alpha", "name": "Alpha", "role": "r", "group": "business",
         "schedule": "30 6 * * 1-6"},
        {"bot": "beta", "name": "Beta", "role": "r", "group": "personal",
         "schedule": "42 6 * * 1-6"},
    ],
}

ENTRY_MD = """# Test Book — Test Author

- **Bot:** alpha
- **Date:** 2026-11-01

## Most useful ideas

- Idea one
- Idea two

## Concrete job change

Do the thing differently.

## Public sources

- https://example.com/one
"""


class SourceMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make_source(self, entries=("alpha",), date="2026-11-01", ledger=None):
        src = self.root / "daily-learning"
        write(src / "bots.json", __import__("json").dumps(BOT))
        write(src / "books-read.md", ledger or (
            "date | bot | title | author\n--- | --- | --- | ---\n"
            "2026-11-01 | alpha | Test Book | Test Author\n"))
        for bot in entries:
            write(src / date / f"{bot}.md", ENTRY_MD.replace("- **Bot:** alpha",
                                                            f"- **Bot:** {bot}"))
        return src


class TestParsing(unittest.TestCase):
    def test_parse_bots(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bots.json"
            p.write_text(__import__("json").dumps(BOT), encoding="utf-8")
            bots = bs.parse_bots(p)
        self.assertEqual(set(bots), {"alpha", "beta"})
        self.assertEqual(bots["beta"]["group"], "personal")

    def test_parse_entry_fields(self):
        e = bs.parse_entry(ENTRY_MD)
        self.assertEqual(e["title"], "Test Book")
        self.assertEqual(e["author"], "Test Author")
        self.assertEqual(e["bot"], "alpha")
        self.assertEqual(e["date"], "2026-11-01")
        self.assertEqual(e["ideas"], ["Idea one", "Idea two"])
        self.assertIn("Do the thing differently.", e["change"])
        self.assertEqual(e["sources"], ["https://example.com/one"])

    def test_parse_ledger_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "books-read.md"
            p.write_text("date | bot | title | author\n--- | --- | --- | ---\n"
                         "2026-11-01 | alpha | A | X\n", encoding="utf-8")
            rows, warnings = bs.parse_books_ledger(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(warnings, [])

    def test_parse_ledger_duplicate_rows_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "books-read.md"
            p.write_text("date | bot | title | author\n--- | --- | --- | ---\n"
                         "2026-11-01 | alpha | A | X\n"
                         "2026-11-01 | alpha | A | X\n", encoding="utf-8")
            rows, warnings = bs.parse_books_ledger(p)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("duplicate" in w for w in warnings))


class TestMissingFiles(SourceMixin, unittest.TestCase):
    def test_missing_bot_file_reported_as_missing(self):
        src = self.make_source(entries=("alpha",))
        days, warnings = bs.load_entries(src)
        stats = bs.compute_stats(days, bs.parse_bots(src / "bots.json"))
        self.assertEqual(stats["dates"], ["2026-11-01"])
        self.assertEqual(stats["per_bot"]["beta"], 0)
        self.assertEqual(stats["streak"], 1)
        # The beta entry is absent; render must produce a missing card.
        bots = bs.parse_bots(src / "bots.json")
        html_out = bs.render_card({"date": "2026-11-01"}, bots["beta"],
                                  "../daily-learning", missing=True)
        self.assertIn("Missing filing", html_out)
        self.assertIn("Beta", html_out)

    def test_missing_source_directory(self):
        days, warnings = bs.load_entries(Path("/nonexistent/daily-learning"))
        self.assertEqual(days, {})
        self.assertEqual(len(warnings), 1)

    def test_missing_ledger_file(self):
        rows, warnings = bs.parse_books_ledger(Path("/nonexistent/books-read.md"))
        self.assertEqual(rows, [])
        self.assertTrue(any("missing ledger" in w for w in warnings))


class TestOutput(SourceMixin, unittest.TestCase):
    def test_full_site_build(self):
        src = self.make_source(entries=("alpha", "beta"))
        out = self.root / "docs"
        rc = bs.main(["--source", str(src), "--out", str(out)])
        self.assertEqual(rc, 0)
        for name in ["index.html", "archive.html", "by-bot.html", "books.html",
                     "styles.css", "app.js", "manifest.webmanifest",
                     "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png",
                     ".nojekyll"]:
            self.assertTrue((out / name).exists(), name)
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("Test Book", index)
        self.assertIn('href="styles.css"', index)  # relative URLs only
        self.assertNotIn('href="/', index)
        self.assertIn("Personal", index)
        self.assertIn("What this bot learned", index)
        self.assertIn("How this will help you", index)
        self.assertNotIn("source file:", index)
        self.assertEqual(index.count('data-bot="beta"'), 1,
                         "personal bot must appear only in the personal section")
        archive = (out / "archive.html").read_text(encoding="utf-8")
        self.assertIn("2026-11-01", archive)
        self.assertIn("data-bot=\"beta\"", archive)
        books = (out / "books.html").read_text(encoding="utf-8")
        self.assertIn("Test Book", books)
        manifest = (out / "manifest.webmanifest").read_text(encoding="utf-8")
        self.assertIn('"start_url"', manifest)

    def test_source_tree_unchanged(self):
        src = self.make_source(entries=("alpha",))
        before = {p.relative_to(src): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        bs.main(["--source", str(src), "--out", str(self.root / "docs")])
        after = {p.relative_to(src): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_missing_state_when_bot_absent(self):
        src = self.make_source(entries=("alpha",))
        out = self.root / "docs"
        bs.main(["--source", str(src), "--out", str(out)])
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("Missing filing", index)
        self.assertIn("No learning note was filed for November 1, 2026", index)

    def test_streak_over_multiple_days(self):
        src = self.make_source(entries=("alpha",), date="2026-11-01")
        write(src / "2026-11-02" / "alpha.md",
              ENTRY_MD.replace("2026-11-01", "2026-11-02"))
        days, _ = bs.load_entries(src)
        stats = bs.compute_stats(days, bs.parse_bots(src / "bots.json"))
        self.assertEqual(stats["dates"], ["2026-11-01", "2026-11-02"])
        self.assertEqual(stats["streak"], 2)

    def test_streak_skips_unscheduled_sunday(self):
        src = self.make_source(entries=("alpha",), date="2026-11-06")
        for day in ("2026-11-07", "2026-11-09"):
            write(src / day / "alpha.md", ENTRY_MD.replace("2026-11-01", day))
        days, _ = bs.load_entries(src)
        stats = bs.compute_stats(days, bs.parse_bots(src / "bots.json"))
        self.assertEqual(stats["streak"], 3)


if __name__ == "__main__":
    unittest.main()
