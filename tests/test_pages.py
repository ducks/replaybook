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

    def test_benchmark_page_separates_current_and_archived_results(self) -> None:
        page = (DOCS_DIR / "benchmarks.html").read_text()
        self.assertIn("DeepSeek V4 Flash 0731", page)
        self.assertIn("Host harness v2", page)
        self.assertIn("9/9", page)
        self.assertIn('class="badge archived"', page)


if __name__ == "__main__":
    unittest.main()
