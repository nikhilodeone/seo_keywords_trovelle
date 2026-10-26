import http.server
import threading
import unittest

from trovelle_seo.crawl import _evenly, _looks_like_product, classify, crawl_site
from trovelle_seo.fetch import Fetcher


def html(title, body="", nav=""):
    return f"<html lang='en'><head><title>{title}</title></head><body><nav>{nav}</nav><h1>{title}</h1>{body}</body></html>"


NAV = "".join(f"<a href='/collections/c{i}'>Cat {i}</a>" for i in range(1, 6))
SITE = {"/": html("Home", "<a href='/blogs/news'>Journal</a><a href='/products/home-pick'>Pick</a>"
                  "<a href='/cart'>Cart</a><a href='/pages/about-us'>About</a>", NAV)}
for i in range(1, 6):
    SITE[f"/collections/c{i}"] = html(f"Cat {i}", "".join(f"<a href='/products/p{i}-{j}'>P{i}{j}</a>" for j in range(3)), NAV)
    SITE[f"/blogs/news/post-{i}"] = html(f"Post {i}")
SITE["/blogs/news"] = html("News", "".join(f"<a href='/blogs/news/post-{i}'>Post {i}</a>" for i in range(1, 6)), NAV)
for i in range(1, 6):
    for j in range(3):
        SITE[f"/products/p{i}-{j}"] = html(f"Product {i}{j}")
SITE["/products/home-pick"] = html("Home pick")
for j in range(20):
    SITE[f"/products/s{j}"] = html(f"Sitemap product {j}")
SITEMAP = ("<urlset>" + "".join(f"<url><loc>http://{{h}}/products/s{j}</loc></url>" for j in range(20))
           + "".join(f"<url><loc>http://{{h}}/collections/c{i}</loc></url>" for i in range(1, 8))
           + "".join(f"<url><loc>http://{{h}}/blogs/news/post-{i}</loc></url>" for i in range(1, 6)) + "</urlset>")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        host = self.headers.get("Host")
        if path == "/robots.txt":
            body, code = b"User-agent: *\nAllow: /\n", 200
        elif path == "/sitemap.xml":
            body, code = SITEMAP.replace("{h}", host).encode(), 200
        elif path in SITE:
            body, code = SITE[path].encode(), 200
        else:
            body, code = b"nope", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


class Discovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def crawl(self, **limits):
        cfg = {"max_text_chars": 5000, "max_pages_per_site": 60,
               "discover": {"categories": 3, "posts": 2, "products": 4, **limits}}
        site = {"key": "t", "name": "T", "pages": [{"url": self.base + "/"}]}
        return crawl_site(site, Fetcher("test", delay=0, timeout=5), cfg, is_self=True)

    def roles(self, rec):
        out = {}
        for p in rec["pages"]:
            out[p["role"]] = out.get(p["role"], 0) + 1
        return out

    def test_reads_home_categories_blog_posts_and_ten_products(self):
        rec = self.crawl(categories=3, posts=2, products=10)
        r = self.roles(rec)
        self.assertEqual(r, {"home": 1, "collection": 3, "blog": 1, "post": 2, "product": 10})
        self.assertEqual(rec["status"], "ok")

    def test_junk_pages_and_duplicates_are_skipped(self):
        rec = self.crawl()
        urls = [p["url"] for p in rec["pages"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertFalse([u for u in urls if "/cart" in u or "about" in u])

    def test_categories_are_spread_across_the_menu(self):
        rec = self.crawl(categories=3)
        got = sorted(p["url"].rsplit("/", 1)[1] for p in rec["pages"] if p["role"] == "collection")
        self.assertEqual(got, ["c1", "c2", "c4"])          # not just the first three

    def test_sitemap_fills_when_pages_do_not_link_enough(self):
        rec = self.crawl(products=25)
        prods = [p["url"] for p in rec["pages"] if p["role"] == "product"]
        self.assertGreater(len(prods), 15)                 # 15 linked from categories, rest from the sitemap
        self.assertTrue([u for u in prods if "/products/s" in u])
        self.assertEqual(rec["sitemap"]["products"], 20)
        self.assertEqual(rec["sitemap"]["blog_posts"], 5)

    def test_link_lists_are_not_stored(self):
        rec = self.crawl()
        for p in rec["pages"]:
            self.assertNotIn("links", p)
            self.assertNotIn("nav_links", p)


class Classify(unittest.TestCase):
    def test_shopify(self):
        c = lambda u: classify(u, "shopify", {})  # noqa: E731
        self.assertEqual(c("https://x.in/collections/dresses"), "category")
        self.assertEqual(c("https://x.in/products/linen-set"), "product")
        self.assertEqual(c("https://x.in/collections/dresses/products/linen-set"), "product")
        self.assertEqual(c("https://x.in/blogs/news"), "blog-index")
        self.assertEqual(c("https://x.in/blogs/news/diwali-guide"), "post")
        self.assertIsNone(c("https://x.in/pages/about-us"))
        self.assertIsNone(c("https://x.in/cart"))

    def test_generic(self):
        c = lambda u: classify(u, "generic", {})  # noqa: E731
        self.assertEqual(c("https://x.in/blog/diwali-outfits/"), "post")
        self.assertEqual(c("https://x.in/blog/"), "blog-index")
        self.assertEqual(c("https://x.in/lehengas"), "category")
        self.assertEqual(c("https://x.in/whats-new/co-ord-sets"), "category")
        self.assertEqual(c("https://x.in/ivory-embroidered-co-ord-set-shpsa1234.html"), "product")
        self.assertEqual(c("https://x.in/co-ord-sets-under-20000-for-date-night-online"), "category")

    def test_helpers(self):
        self.assertEqual(_evenly(list(range(10)), 3), [0, 3, 6])
        self.assertEqual(_evenly([1, 2], 5), [1, 2])
        self.assertFalse(_looks_like_product("co-ord-sets-under-20000"))


if __name__ == "__main__":
    unittest.main()
