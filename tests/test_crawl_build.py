import functools
import http.server
import json
import shutil
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from trovelle_seo import build, crawl

REPO = Path(__file__).resolve().parent.parent
PAGES = {
    "/": "<html lang='en'><head><title>Home | Shop</title></head><body><h1>Designer co-ord sets</h1>"
         "<a href='/blog/a-1/'>a</a><a href='/blog/b-2/'>b</a></body></html>",
    "/blog/a-1/": "<html><head><title>Diwali outfit ideas</title></head><body><h1>Diwali outfits</h1></body></html>",
    "/blog/b-2/": "<html><head><title>Second</title></head><body></body></html>",
    "/private/x": "<html><head><title>Secret</title></head></html>",
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            body, code = b"User-agent: *\nDisallow: /private/\n", 200
        elif self.path in PAGES:
            body, code = PAGES[self.path].encode(), 200
        else:
            body, code = b"nope", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


class CrawlBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def make_root(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for d in ("config", "data", "trovelle_seo"):
            shutil.copytree(REPO / d, root / d, ignore=shutil.ignore_patterns("__pycache__", "live"))
        base = f"http://127.0.0.1:{self.port}"
        comp = {"self": {"key": "trovelle", "name": "Trovélle", "pages": [{"role": "home", "url": base + "/"}]},
                "competitors": [{"key": "aza", "name": "Aza", "seeded": True,
                                 "pages": [{"url": base + "/"}, {"url": base + "/private/x"}, {"url": base + "/missing"}],
                                 "follow": {"include": r"/blog/[a-z0-9-]+/$", "max": 5}},
                                {"key": "pernia", "name": "Pernia", "seeded": True,
                                 "pages": [{"url": base + "/private/x"}]}]}
        (root / "config" / "competitors.json").write_text(json.dumps(comp), encoding="utf-8")
        s = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
        s["crawl"]["delay_seconds"] = 0
        s["crawl"]["timeout_seconds"] = 5
        (root / "config" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        return root

    def test_crawl_respects_robots_follows_links_and_never_raises(self):
        root = self.make_root()
        snap = crawl.run(root, log=lambda *a: None)
        aza = snap["sites"]["aza"]
        by = {p["url"].split(str(self.port))[1]: p for p in aza["pages"]}
        self.assertEqual(by["/"]["status"], 200)
        self.assertEqual(by["/private/x"]["status"], -1)          # robots.txt disallow honoured
        self.assertEqual(by["/missing"]["status"], 404)
        self.assertIn("/blog/a-1/", by)                            # followed from the homepage
        self.assertNotIn("links", by["/"])                         # raw link list is not stored
        self.assertEqual(aza["status"], "partial")
        self.assertEqual(snap["sites"]["pernia"]["status"], "blocked")
        self.assertTrue((root / "data" / "live" / "snapshot.json").exists())

    def test_failed_recrawl_keeps_previous_good_data(self):
        root = self.make_root()
        crawl.run(root, log=lambda *a: None)
        comp = json.loads((root / "config" / "competitors.json").read_text(encoding="utf-8"))
        comp["competitors"][0]["pages"] = [{"url": f"http://127.0.0.1:{self.port}/private/x"}]
        comp["competitors"][0].pop("follow")
        (root / "config" / "competitors.json").write_text(json.dumps(comp), encoding="utf-8")
        snap = crawl.run(root, log=lambda *a: None)
        self.assertEqual(snap["sites"]["aza"]["status"], "partial")
        self.assertIn("last_attempt", snap["sites"]["aza"])

    def test_build_end_to_end_and_history_is_one_row_per_day(self):
        root = self.make_root()
        crawl.run(root, log=lambda *a: None)
        for _ in range(2):
            data = build.assemble(root, now=datetime(2026, 10, 26, 12, 0, tzinfo=build.IST))
        out = build.render(root, data)
        html = out.read_text(encoding="utf-8")
        self.assertNotIn(build.PLACEHOLDER, html)
        self.assertEqual(len(data["keywords"]), 90)
        self.assertEqual(len((root / "data" / "live" / "history.jsonl").read_text().strip().splitlines()), 1)
        self.assertEqual(data["meta"]["live_competitors"], 1)      # aza partial; pernia blocked
        aza = {s["key"]: s for s in data["sites"]}["aza"]
        self.assertEqual(aza["status"], "partial")

    def test_seed_files_parse_and_are_consistent(self):
        data = build.assemble(REPO, write_history=False)
        self.assertGreaterEqual(len(data["keywords"]), 80)
        self.assertEqual(len(data["aeo"]), 22)
        self.assertEqual(len(data["plan"]), 22)
        names = {r["keyword"] for r in data["keywords"]}
        self.assertEqual(len(names), len(data["keywords"]), "duplicate keywords in keywords.csv")
        missing = [x["keyword"] for x in data["plan"] + data["aeo"] if x["keyword"] and x["kw"] is None]
        self.assertEqual(missing, [], "plan/AEO rows reference keywords that are not tracked")


if __name__ == "__main__":
    unittest.main()
