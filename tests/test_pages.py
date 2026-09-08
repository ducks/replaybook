from __future__ import annotations

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


REPO_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_DIR / "docs"
BENCHMARK_DATA_DIR = REPO_DIR / "benchmark-data"
SITE_DIR = REPO_DIR / "site"


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
    def test_every_page_includes_goatcounter(self) -> None:
        snippet = 'data-goatcounter="https://stats.replaybook.dev/count"'
        for page in DOCS_DIR.glob("*.html"):
            with self.subTest(page=page.name):
                self.assertIn(snippet, page.read_text())

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

    def test_benchmark_frontend_has_tracked_sources(self) -> None:
        expected_templates = {
            "benchmark-base.html",
            "benchmark-evidence.html",
            "benchmark-redirect.html",
            "benchmark-overview.html",
            "benchmarks.html",
            "benchmark-compare.html",
            "benchmark-coverage.html",
            "benchmark-explorer.html",
            "benchmark-model.html",
            "benchmark-models.html",
            "benchmark-provider.html",
            "benchmark-providers.html",
            "benchmark-scenario.html",
        }
        templates = SITE_DIR / "templates"
        template_names = {path.name for path in templates.glob("*.html")}
        self.assertTrue(expected_templates.issubset(template_names))
        self.assertEqual(
            (SITE_DIR / "static/style.css").read_text(),
            (DOCS_DIR / "style.css").read_text(),
        )
        publisher = (
            REPO_DIR / "integrations/host/publish_benchmarks.py"
        ).read_text()
        self.assertNotIn("<!doctype html>", publisher)

    def test_benchmark_pages_separate_current_and_history(self) -> None:
        current = (DOCS_DIR / "benchmarks.html").read_text()
        visual = (DOCS_DIR / "benchmark-visual.html").read_text()
        explorer = (DOCS_DIR / "benchmark-explorer.html").read_text()
        history = (DOCS_DIR / "benchmark-history.html").read_text()

        index = json.loads((BENCHMARK_DATA_DIR / "index.json").read_text())
        evidence = (DOCS_DIR / "benchmark-evidence.html").read_text()
        self.assertIn("What Replaybook measures", current)
        self.assertIn("The scoring contract", current)
        self.assertEqual(current, visual)
        self.assertNotIn('id="wall"', current)
        self.assertNotIn("Latest benchmark boundary", current)
        self.assertNotIn('id="data"', current)
        self.assertIn('href="benchmark-evidence.html"', current)
        self.assertIn("Where does the agent break?", evidence)
        self.assertIn("Whole-cohort summary", evidence)
        self.assertIn('id="inspector"', evidence)

        catalog = json.loads((BENCHMARK_DATA_DIR / "catalog.json").read_text())
        docs_catalog = json.loads((DOCS_DIR / "benchmark-catalog.json").read_text())
        self.assertEqual(catalog, docs_catalog)
        self.assertEqual(catalog["current_version"], index["current_version"])
        self.assertIn("window.location.search", explorer)
        self.assertIn("window.location.replace", explorer)
        self.assertIn("cost_per_repair_usd", evidence)
        self.assertIn('href="benchmark-catalog.json"', evidence)

        self.assertIn("DeepSeek V4 Flash 0731", history)
        self.assertIn("Host harness v2", history)
        self.assertIn("DeepSeek revision comparison", history)
        self.assertIn("51% fewer", history)
        self.assertIn("migration_not_applied", history)
        self.assertIn('class="badge archived"', history)

        self.assertIn("Evaluated, failed, and unavailable", current)
        self.assertIn("When results are comparable", current)
        self.assertIn("Benchmark tiers", current)

    def test_evidence_assets_and_navigation(self) -> None:
        for asset in ("evidence.js", "evidence.css"):
            self.assertEqual((SITE_DIR / "static" / asset).read_text(),
                             (DOCS_DIR / asset).read_text())
        for page in DOCS_DIR.glob("*.html"):
            html = page.read_text()
            if 'aria-label="Benchmark sections"' not in html:
                continue
            nav = html.split('aria-label="Benchmark sections"', 1)[1].split("</nav>", 1)[0]
            self.assertIn('href="benchmark-evidence.html"', nav)
            self.assertNotIn(">Compare</a>", nav)
            self.assertNotIn(">Explore</a>", nav)

    def test_evidence_interactions(self) -> None:
        import shutil
        import subprocess

        if not shutil.which("node"):
            self.skipTest("Node is required for the Evidence interaction tests")
        subprocess.run(["node", "tests/evidence.test.cjs"], cwd=REPO_DIR, check=True)
        subprocess.run(["node", "tests/models.test.cjs"], cwd=REPO_DIR, check=True)

    def test_core_pages_cover_current_workflows(self) -> None:
        home = (DOCS_DIR / "index.html").read_text()
        usage = (DOCS_DIR / "usage.html").read_text()
        scenarios = (DOCS_DIR / "scenarios.html").read_text()

        self.assertIn("Practice incidents", home)
        self.assertIn("Test infrastructure agents", home)
        self.assertIn("7 durable repairs in 12 trials", home)

        self.assertIn("replaybook remote", usage)
        self.assertIn("replaybook serve", usage)
        self.assertIn("run_host_matrix.py", usage)
        self.assertIn("--agent-adapter", usage)
        self.assertIn("unavailable", usage)

        self.assertIn("Docker scenario packs", scenarios)
        self.assertIn("Host-native evaluation scenarios", scenarios)
        self.assertIn("016-rails-pool-exhaustion", scenarios)
        self.assertIn(
            "benchmark-scenario.html?scenario=030-visual-metrics-regression",
            scenarios,
        )
        self.assertIn("scenario.toml", scenarios)
        self.assertIn("replaybook-build-scenario", scenarios)


if __name__ == "__main__":
    unittest.main()
