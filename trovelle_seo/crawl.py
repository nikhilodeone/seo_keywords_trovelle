"""Crawl Trovelle and its competitors and write data/live/snapshot.json.

Design rules:
  * never fail the run because one site is down or blocks us; record the status and move on
  * a blocked or errored competitor keeps its seed scores (handled in scoring.py)
  * only fetch the pages listed in config/competitors.json plus a few followed links
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .extract import extract_page
from .fetch import Fetcher

COMPETITOR_TEXT_CAP = 8000
BLOCK_CODES = {401, 403, 429}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_url(u: str) -> str:
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/") or u


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


def _sitemap_stats(fetcher: Fetcher, url: str) -> dict:
    """Count URLs by type from a Shopify-style sitemap index. Best effort."""
    out: dict = {"url": url, "ok": False, "products": 0, "collections": 0, "pages": 0,
                 "blog_urls": []}
    status, body, _, _ = fetcher.get(url)
    if status != 200:
        out["status"] = status
        return out
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
    children = [u for u in locs if u.endswith(".xml") or ".xml?" in u]
    urls = [] if children else locs
    for child in children[:8]:
        name = child.lower()
        if not any(k in name for k in ("product", "collection", "blog", "page")):
            continue
        s, b, _, _ = fetcher.get(child)
        if s != 200:
            continue
        child_locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", b)
        if "product" in name:
            out["products"] += len(child_locs)
        elif "collection" in name:
            out["collections"] += len(child_locs)
        elif "blog" in name:
            out["blog_urls"] += child_locs
        elif "page" in name:
            out["pages"] += len(child_locs)
    for u in urls:
        path = urlparse(u).path
        if "/products/" in path:
            out["products"] += 1
        elif "/collections/" in path:
            out["collections"] += 1
        elif "/blogs/" in path and path.count("/") >= 3:
            out["blog_urls"].append(u)
        elif "/pages/" in path:
            out["pages"] += 1
    # a blog index is /blogs/<handle>; posts are /blogs/<handle>/<slug>
    out["blog_urls"] = [u for u in out["blog_urls"] if urlparse(u).path.strip("/").count("/") >= 2]
    out["blog_posts"] = len(out["blog_urls"])
    out["blog_urls"] = out["blog_urls"][:30]
    out["ok"] = True
    return out


def crawl_site(site: dict, fetcher: Fetcher, crawl_cfg: dict, is_self: bool) -> dict:
    cap = crawl_cfg["max_text_chars"] if is_self else min(crawl_cfg["max_text_chars"], COMPETITOR_TEXT_CAP)
    max_pages = int(crawl_cfg.get("max_pages_per_site", 25))
    pages: list[dict] = []
    seen: set[str] = set()
    link_pool: list[str] = []

    for p in site.get("pages", []):
        if len(pages) >= max_pages:
            break
        key = _norm_url(p["url"])
        if key in seen:
            continue
        seen.add(key)
        raw = _fetch_page(fetcher, p["url"], p.get("role", "page"), cap)
        link_pool.extend(raw.pop("links", []))
        pages.append(raw)

    follow = site.get("follow")
    if follow:
        rx = re.compile(follow["include"])
        taken = 0
        for link in link_pool:
            if taken >= int(follow.get("max", 5)) or len(pages) >= max_pages:
                break
            clean = link.split("#")[0].split("?")[0]
            key = _norm_url(clean)
            if key in seen or not rx.search(clean):
                continue
            seen.add(key)
            raw = _fetch_page(fetcher, clean, "followed", cap)
            raw.pop("links", None)
            pages.append(raw)
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

    rec = {"status": status, "fetched_at": _now(), "pages": pages, "errors": errors}
    if is_self and site.get("sitemap"):
        rec["sitemap"] = _sitemap_stats(fetcher, site["sitemap"])
    return rec


def run(root: Path, only: list[str] | None = None, log=print) -> dict:
    settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
    comp = json.loads((root / "config" / "competitors.json").read_text(encoding="utf-8"))
    cc = settings["crawl"]
    ua = os.environ.get("TROVELLE_SEO_UA") or cc["user_agent"]     # CI sets this from the repo URL
    cc = dict(cc, user_agent=ua)
    fetcher = Fetcher(ua, delay=cc["delay_seconds"], timeout=cc["timeout_seconds"],
                      respect_robots=cc["respect_robots"])
    out_path = root / "data" / "live" / "snapshot.json"
    prev = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8")).get("sites", {})
        except Exception:
            prev = {}

    sites: dict[str, dict] = {}
    targets = [(comp["self"], True)] + [(c, False) for c in comp["competitors"]]
    for site, is_self in targets:
        key = site["key"]
        if only and key not in only:
            if key in prev:
                sites[key] = prev[key]       # keep last good data for sites we skipped
            continue
        log(f"crawl {key} ...")
        rec = crawl_site(site, fetcher, cc, is_self)
        n_ok = sum(1 for p in rec["pages"] if p["status"] == 200)
        log(f"  {rec['status']}: {n_ok}/{len(rec['pages'])} pages")
        # a failed run must not erase the last good data for that site
        if rec["status"] in ("blocked", "error") and prev.get(key, {}).get("status") in ("ok", "partial"):
            old = dict(prev[key])
            old["last_attempt"] = {"status": rec["status"], "at": rec["fetched_at"], "errors": rec["errors"][:5]}
            rec = old
            log("  keeping previous good snapshot for", key)
        sites[key] = rec

    snap = {"generated_at": _now(), "user_agent": cc["user_agent"], "sites": sites}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"wrote {out_path}")
    return snap


if __name__ == "__main__":  # pragma: no cover
    run(Path(__file__).resolve().parent.parent, only=sys.argv[1:] or None)
