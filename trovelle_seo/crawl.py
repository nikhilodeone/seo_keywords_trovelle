"""Crawl Trovelle and its competitors and write data/live/snapshot.json.

For every site the crawler reads:
  * the homepage (and any pages pinned in config/competitors.json)
  * category pages     (from the site menu, then the sitemap)
  * blog posts         (from the blog index, then the sitemap)
  * product pages      (from the category pages, then the sitemap)
with the counts set by crawl.discover in config/settings.json (default 12 / 8 / 10).

Rules:
  * never fail the run because one site is down or blocks us; record the status and move on
  * a blocked or errored competitor keeps its seed scores (handled in scoring.py)
  * obey robots.txt, wait between requests to the same host, identify ourselves
  * different sites are crawled in parallel (one polite fetcher per site)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .extract import extract_page
from .fetch import Fetcher

COMPETITOR_TEXT_CAP = 6000     # characters of body text kept per page (competitors)
PRODUCT_TEXT_CAP = 1500        # product pages only need the title, price and first lines
BLOCK_CODES = {401, 403, 429}
DEFAULT_LIMITS = {"categories": 12, "posts": 8, "products": 10}
BLOG_ROOTS = {"blog", "blogs", "journal", "stories", "magazine", "style-guide", "lookbook"}
EXCLUDE_RX = re.compile(
    r"(?:^|/)(?:login|signin|sign-in|register|cart|checkout|account|wishlist|contact(?:-us)?|faqs?|"
    r"terms[a-z-]*|privacy[a-z-]*|returns?[a-z-]*|refund[a-z-]*|shipping[a-z-]*|track[a-z-]*|"
    r"polic(?:y|ies)|about[a-z-]*|careers?|store-locator|stores?|search|gift-cards?)(?:$|/)"
    r"|\.(?:jpe?g|png|gif|webp|svg|pdf|xml|js|css|ico|json|zip)$", re.I)
KIND_TO_ROLE = {"category": "collection", "post": "post", "product": "product", "blog-index": "blog"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_url(u: str) -> str:
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc.lower()}{p.path}".rstrip("/") or u


def _host(u: str) -> str:
    h = urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _clean(u: str) -> str:
    return u.split("#")[0].split("?")[0]


def _segs(u: str) -> list[str]:
    return [s for s in urlparse(u).path.split("/") if s]


def _evenly(seq: list, n: int) -> list:
    """n items spread across the list (so we sample the whole menu, not just its first block)."""
    if n <= 0:
        return []
    if len(seq) <= n:
        return list(seq)
    return [seq[int(i * len(seq) / n)] for i in range(n)]


def _looks_like_product(slug: str) -> bool:
    """Product slugs on custom (non-Shopify) stores end in .html or an id like 'shpsa123' or '1048213'."""
    if slug.count("-") < 3:
        return False
    if slug.endswith(".html"):
        return True
    return bool(re.search(r"-(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]{5,}$", slug) or re.search(r"-\d{6,}$", slug))


def classify(url: str, platform: str, rx: dict) -> str | None:
    """Return 'category', 'product', 'post', 'blog-index' or None for a same-site URL."""
    segs = _segs(url)
    if not segs:
        return None
    path = urlparse(url).path
    if EXCLUDE_RX.search(path):
        return None
    for kind, key in (("post", "post"), ("product", "product"), ("category", "category")):
        if rx.get(key) and rx[key].search(url):
            return kind
    root = segs[0].lower()
    if root == "blogs":
        return "blog-index" if len(segs) <= 2 else "post"
    if root in BLOG_ROOTS:
        return "blog-index" if len(segs) == 1 else "post"
    if platform == "shopify":
        if "products" in segs and segs[-2:-1] == ["products"]:
            return "product"
        if root == "collections" and len(segs) == 2:
            return "category"
        return None
    if len(segs) == 1 and _looks_like_product(segs[0].lower()):
        return "product"
    return "category" if len(segs) <= 2 else None


# --------------------------------------------------------------------------- fetching
def _fetch_page(fetcher: Fetcher, url: str, role: str, text_cap: int) -> dict:
    status, body, final, note = fetcher.get(url)
    rec: dict = {"url": url, "role": role, "status": status}
    if status == 200 and body:
        try:
            rec.update(extract_page(body, final, max_text_chars=text_cap))
            rec["url"] = url
            rec["final_url"] = final
        except Exception as e:  # parser bug on odd markup: record, don't crash
            rec["status"] = 0
            rec["note"] = f"parse error: {e}"
    elif note:
        rec["note"] = note
    return rec


def _locs(xml: str) -> list[str]:
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def read_sitemap(fetcher: Fetcher, url: str, max_children: int = 6, max_urls: int = 20000) -> dict:
    """All URLs from a sitemap or sitemap index. Best effort; never raises."""
    out: dict = {"url": url, "ok": False, "urls": []}
    status, body, _, _ = fetcher.get(url)
    if status != 200:
        out["status"] = status
        return out
    locs = _locs(body)
    kids = [u for u in locs if re.search(r"\.xml(\?|$)", u)]
    if kids:
        used = 0
        for kid in kids:
            if used >= max_children or len(out["urls"]) >= max_urls:
                break
            if re.search(r"image|video|news", kid, re.I):
                continue
            used += 1
            s, b, _, _ = fetcher.get(kid)
            if s == 200:
                out["urls"].extend(_locs(b))
    else:
        out["urls"] = locs
    out["urls"] = list(dict.fromkeys(out["urls"]))[:max_urls]
    out["ok"] = True
    return out


def _sitemap_stats(sm: dict, platform: str, rx: dict) -> dict:
    counts = {"products": 0, "collections": 0, "blog_urls": []}
    for u in sm.get("urls", []):
        k = classify(u, platform, rx)
        if k == "product":
            counts["products"] += 1
        elif k == "category":
            counts["collections"] += 1
        elif k == "post":
            counts["blog_urls"].append(u)
    other = [u for u in sm.get("urls", []) if "/products/" not in u][:800]
    return {"url": sm.get("url"), "ok": sm.get("ok", False), "other_urls": other, "products": counts["products"],
            "collections": counts["collections"], "blog_posts": len(counts["blog_urls"]),
            "blog_urls": counts["blog_urls"][:30]}


# --------------------------------------------------------------------------- one site
def crawl_site(site: dict, fetcher: Fetcher, crawl_cfg: dict, is_self: bool) -> dict:
    page_cap = crawl_cfg["max_text_chars"] if is_self else min(crawl_cfg["max_text_chars"], COMPETITOR_TEXT_CAP)
    max_pages = int(crawl_cfg.get("max_pages_per_site", 60))
    d = site.get("discover") or {}
    enabled = d.get("enabled", True)
    lim = {**DEFAULT_LIMITS, **(crawl_cfg.get("discover") or {}), **(d.get("limits") or {})}
    rx = {k: re.compile(d[f"{k}_regex"]) for k in ("category", "post", "product") if d.get(f"{k}_regex")}

    pages: list[dict] = []
    seen: set[str] = set()
    side: dict[str, dict] = {}      # url -> {"links": [...], "nav": [...]} kept only while crawling

    def fetch(url: str, role: str) -> dict | None:
        key = _norm_url(url)
        if key in seen or len(pages) >= max_pages:
            return None
        seen.add(key)
        cap = PRODUCT_TEXT_CAP if role == "product" else page_cap
        raw = _fetch_page(fetcher, url, role, cap)
        side[url] = {"links": raw.pop("links", []), "nav": raw.pop("nav_links", [])}
        pages.append(raw)
        return raw

    def have(role: str) -> int:
        return sum(1 for p in pages if p["role"] == role)

    # 1. pinned pages from the config (homepage first)
    for p in site.get("pages", []):
        role = p.get("role")
        if not role:
            k = classify(p["url"], "shopify" if "/collections/" in p["url"] or "/blogs/" in p["url"] else "generic", rx)
            role = "home" if not _segs(p["url"]) else KIND_TO_ROLE.get(k or "", "page")
        fetch(p["url"], role)

    notes: list[str] = []
    platform = d.get("platform") or "generic"
    sm_info: dict = {"ok": False, "urls": []}
    fetched_ok = [p for p in pages if p["status"] == 200]
    if enabled and fetched_ok:
        base = fetched_ok[0]["url"]
        origin = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
        host = _host(base)
        pool_links = [u for s in side.values() for u in s["links"]]
        pool_nav = [u for s in side.values() for u in s["nav"]]
        if not d.get("platform"):
            platform = "shopify" if any("/collections/" in u for u in pool_links + pool_nav) else "generic"

        def same_site(urls):
            return [_clean(u) for u in dict.fromkeys(urls) if _host(u) == host]

        sm_info = read_sitemap(fetcher, d.get("sitemap") or site.get("sitemap") or origin + "/sitemap.xml")
        sm_urls = same_site(sm_info["urls"])
        sm_by = {"category": [], "product": [], "post": [], "blog-index": []}
        for u in sm_urls:
            k = classify(u, platform, rx)
            if k:
                sm_by[k].append(u)

        def fresh(urls):
            return [u for u in urls if _norm_url(u) not in seen]

        def of_kind(urls, kind):
            return [u for u in same_site(urls) if classify(u, platform, rx) == kind]

        # 2. category pages: menu first (spread across the whole menu), then sitemap
        want = max(0, int(lim["categories"]) - have("collection"))
        cats = _evenly(fresh(of_kind(pool_nav, "category")), want)
        if len(cats) < want:
            extra = [u for u in fresh(sm_by["category"]) if u not in cats]
            cats += _evenly(extra, want - len(cats))
        if len(cats) < want:
            extra = [u for u in fresh(of_kind(pool_links, "category")) if u not in cats]
            cats += _evenly(extra, want - len(cats))
        for u in cats:
            fetch(u, "collection")

        # 3. blog index, then posts (index page links first, then sitemap)
        if have("blog") == 0:
            idx = (of_kind(pool_nav + pool_links, "blog-index") or sm_by["blog-index"])
            if d.get("blog_index"):
                idx = [d["blog_index"]]
            for u in fresh(idx)[:1]:
                fetch(u, "blog")
        blog_links = [u for p in pages if p["role"] == "blog" and p["status"] == 200
                      for u in side.get(p["url"], {}).get("links", [])]
        want = max(0, int(lim["posts"]) - have("post"))
        posts = fresh(of_kind(blog_links, "post"))[:want]
        if len(posts) < want:
            posts += _evenly([u for u in fresh(of_kind(pool_links, "post") + sm_by["post"]) if u not in posts],
                             want - len(posts))
        for u in posts:
            fetch(u, "post")

        # 4. product pages: spread across the category pages, then sitemap
        want = max(0, int(lim["products"]) - have("product"))
        per_page = [fresh(of_kind(side.get(p["url"], {}).get("links", []), "product"))
                    for p in pages if p["role"] in ("collection", "home") and p["status"] == 200]
        picks: list[str] = []
        i = 0
        while len(picks) < want and any(i < len(lst) for lst in per_page):
            for lst in per_page:
                if i < len(lst) and lst[i] not in picks and len(picks) < want:
                    picks.append(lst[i])
            i += 1
        if len(picks) < want:
            picks += _evenly([u for u in fresh(sm_by["product"]) if u not in picks], want - len(picks))
        for u in picks:
            fetch(u, "product")

        found = {k: have(r) for k, r in (("categories", "collection"), ("posts", "post"), ("products", "product"))}
        if enabled and found["products"] == 0:
            notes.append("No product pages recognised. Set discover.product_regex for this site in config/competitors.json.")
        if found["posts"] == 0:
            notes.append("No blog posts found (no blog link in the menu and none in the sitemap).")
        if found["categories"] == 0:
            notes.append("No category pages found.")

    # legacy: simple regex follow rule from the homepage
    follow = site.get("follow")
    if follow and fetched_ok:
        frx = re.compile(follow["include"])
        taken = 0
        for link in [u for s in side.values() for u in s["links"]]:
            if taken >= int(follow.get("max", 5)):
                break
            clean = _clean(link)
            if _norm_url(clean) in seen or not frx.search(clean):
                continue
            if fetch(clean, "followed"):
                taken += 1

    ok = [p for p in pages if p["status"] == 200]
    blocked = [p for p in pages if p["status"] == -1 or p["status"] in BLOCK_CODES]
    if not pages:
        status = "error"
    elif len(ok) == len(pages):
        status = "ok"
    elif ok:
        status = "partial"
    elif len(blocked) == len(pages):
        status = "blocked"
    else:
        status = "error"

    errors = []
    for p in pages:
        if p["status"] != 200:
            reason = p.get("note") or ("blocked by robots.txt" if p["status"] == -1 else f"HTTP {p['status']}")
            errors.append(f"{p['url']}: {reason}")

    roles: dict[str, int] = {}
    for p in ok:
        roles[p["role"]] = roles.get(p["role"], 0) + 1
    rec = {"status": status, "fetched_at": _now(), "pages": pages, "errors": errors,
           "discovery": {"platform": platform, "sitemap_urls": len(sm_info.get("urls", [])),
                         "sitemap_ok": sm_info.get("ok", False), "roles": roles, "notes": notes}}
    if is_self:
        rec["sitemap"] = _sitemap_stats(sm_info, platform, rx)
    return rec


# --------------------------------------------------------------------------- all sites
def run(root: Path, only: list[str] | None = None, log=print) -> dict:
    settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
    comp = json.loads((root / "config" / "competitors.json").read_text(encoding="utf-8"))
    cc = settings["crawl"]
    ua = os.environ.get("TROVELLE_SEO_UA") or cc["user_agent"]     # CI sets this from the repo URL
    cc = dict(cc, user_agent=ua)
    out_path = root / "data" / "live" / "snapshot.json"
    prev = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8")).get("sites", {})
        except Exception:
            prev = {}

    lock = threading.Lock()
    say = lambda *a: (lock.acquire(), log(*a), lock.release())  # noqa: E731

    def work(item):
        site, is_self = item
        key = site["key"]
        # one fetcher per site: each host gets its own politeness delay and robots cache
        fetcher = Fetcher(ua, delay=cc["delay_seconds"], timeout=cc["timeout_seconds"],
                          respect_robots=cc["respect_robots"])
        try:
            rec = crawl_site(site, fetcher, cc, is_self)
        except Exception as e:  # a bug or odd site must never stop the other sites
            rec = {"status": "error", "fetched_at": _now(), "pages": [], "errors": [f"{type(e).__name__}: {e}"]}
        n_ok = sum(1 for p in rec["pages"] if p["status"] == 200)
        say(f"crawl {key}: {rec['status']} ({n_ok}/{len(rec['pages'])} pages)")
        return key, rec

    targets = [(comp["self"], True)] + [(c, False) for c in comp["competitors"]]
    todo = [t for t in targets if not only or t[0]["key"] in only]
    with ThreadPoolExecutor(max_workers=int(cc.get("workers", 4))) as ex:
        results = dict(ex.map(work, todo))

    sites: dict[str, dict] = {}
    for site, _ in targets:
        key = site["key"]
        if key not in results:
            if key in prev:
                sites[key] = prev[key]       # keep last good data for sites we skipped
            continue
        rec = results[key]
        # a failed run must not erase the last good data for that site
        if rec["status"] in ("blocked", "error") and prev.get(key, {}).get("status") in ("ok", "partial"):
            old = dict(prev[key])
            old["last_attempt"] = {"status": rec["status"], "at": rec["fetched_at"], "errors": rec["errors"][:5]}
            rec = old
            log("  keeping previous good snapshot for", key)
        sites[key] = rec

    snap = {"generated_at": _now(), "user_agent": ua, "sites": sites}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"wrote {out_path}")
    return snap


if __name__ == "__main__":  # pragma: no cover
    run(Path(__file__).resolve().parent.parent, only=sys.argv[1:] or None)
