from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


REPO_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_DIR / "docs"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag not in {"a", "link"}:
            return
        attribute = "href"
        values = dict(attrs)
        if values.get(attribute):
            self.links.append(str(values[attribute]))


class PagesTests(unittest.TestCase):
    def test_local_links_exist(self) -> None:
        for page in DOCS_DIR.glob("*.html"):
            parser = LinkParser()
            parser.feed(page.read_text())
            for link in parser.links:
                parsed = urlparse(link)
                if parsed.scheme or parsed.netloc or link.startswith("#"):
                    continue
                target = DOCS_DIR / parsed.path
                with self.subTest(page=page.name, link=link):
                    self.assertTrue(target.is_file(), f"missing local link: {target}")

    def test_every_page_links_to_benchmarks(self) -> None:
        for page in DOCS_DIR.glob("*.html"):
            with self.subTest(page=page.name):
                self.assertIn('href="benchmarks.html"', page.read_text())

    def test_pages_workflow_deploys_docs(self) -> None:
        workflow = (REPO_DIR / ".github/workflows/pages.yml").read_text()
        self.assertIn("actions/configure-pages@v5", workflow)
        self.assertIn("actions/upload-pages-artifact@v4", workflow)
        self.assertIn("path: docs", workflow)
        self.assertIn("actions/deploy-pages@v4", workflow)

    def test_benchmark_pages_separate_current_history_and_methodology(self) -> None:
        current = (DOCS_DIR / "benchmarks.html").read_text()
        history = (DOCS_DIR / "benchmark-history.html").read_text()
        methodology = (DOCS_DIR / "benchmark-methodology.html").read_text()

        self.assertIn("Host harness v5", current)
        self.assertIn("94/105", current)
        self.assertIn("20260809.0.0", current)
        self.assertIn("GPT-5.6 Luna", current)
        self.assertIn("DeepSeek V4 Pro", current)
        self.assertNotIn('class="badge archived"', current)

        self.assertIn("DeepSeek V4 Flash 0731", history)
        self.assertIn("Host harness v2", history)
        self.assertIn("DeepSeek revision comparison", history)
        self.assertIn("51% fewer", history)
        self.assertIn("migration_not_applied", history)
        self.assertIn('class="badge archived"', history)

        self.assertIn("Evaluated, failed, and unavailable", methodology)
        self.assertIn("Price per durable repair", methodology)
        self.assertIn("v5", methodology)

    def test_core_pages_cover_current_workflows(self) -> None:
        home = (DOCS_DIR / "index.html").read_text()
        usage = (DOCS_DIR / "usage.html").read_text()
        scenarios = (DOCS_DIR / "scenarios.html").read_text()

        self.assertIn("Practice incidents", home)
        self.assertIn("Test infrastructure agents", home)
        self.assertIn("15 durable repairs in 15 trials", home)

        self.assertIn("replaybook remote", usage)
        self.assertIn("replaybook serve", usage)
        self.assertIn("run_host_matrix.py", usage)
        self.assertIn("--agent-adapter", usage)
        self.assertIn("unavailable", usage)

        self.assertIn("Docker scenario packs", scenarios)
        self.assertIn("Host-native evaluation scenarios", scenarios)
        self.assertIn("016-rails-pool-exhaustion", scenarios)
        self.assertIn("scenario.toml", scenarios)
        self.assertIn("replaybook-build-scenario", scenarios)


if __name__ == "__main__":
    unittest.main()
