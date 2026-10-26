"""Keep the content plan current: dates, festival cut-offs, what Trovélle has already published,
and the next biggest gaps that are not in the plan yet."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from . import textnorm as tn
from .scoring import KeywordMatcher, PageIndex

DUE_SOON_DAYS = 14
MANUAL = {"published": "live", "live": "live", "done": "live", "skipped": "skipped", "skip": "skipped",
          "in progress": "in_progress", "in-progress": "in_progress", "drafting": "in_progress", "draft": "in_progress"}
LABELS = {"live": "Live", "partial": "Partly live", "in_progress": "In progress", "planned": "Planned",
          "due_soon": "Due soon", "overdue": "Overdue", "expired": "Season over", "skipped": "Skipped"}
TEXT_WORDS = ("blog", "guide", "listicle", "explainer", "comparison", "feature", "trust", "profile")


def _date(s: str) -> date | None:
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def load_seasons(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        out = {}
        for r in csv.DictReader(f):
            r["date_obj"], r["ends_obj"] = _date(r.get("date")), _date(r.get("ends") or r.get("date"))
            if r.get("id") and r["date_obj"]:
                out[r["id"]] = r
        return out


def _needs(fmt: str) -> tuple[bool, bool, bool]:
    f = (fmt or "").lower()
    coll = "collection" in f
    text = any(w in f for w in TEXT_WORDS)
    anyneed = not coll and not text      # e.g. "On-site fix"
    return coll, text, anyneed


def _slug_match(keyword: str, url: str) -> bool:
    kw = {t for t in tn.base_tokens(keyword) if t not in tn.LOOSE_DROP}
    slug = set(tn.base_tokens(urlparse(url).path.replace("-", " ").replace("/", " ")))
    if len(kw) < 2:
        return False
    common = len(kw & slug)
    return common >= 2 and common / len(kw) >= 0.6


def _kind(url: str, role: str = "") -> str:
    path = urlparse(url).path
    if role == "collection" or path.startswith("/collections/"):
        return "coll"
    return "text"      # blog posts and /pages/ pages


def detect(item: dict, kw_row: dict | None, pages: list[dict], sitemap_urls: list[str]) -> dict:
    """What has Trovélle already published for this item? Returns {'coll': url|None, 'text': url|None, 'by': str}."""
    found = {"coll": None, "text": None, "by": ""}
    keyword = item.get("keyword", "")
    if kw_row is not None:
        m = KeywordMatcher(kw_row)
        for p in pages:
            if p.get("status") != 200 or p.get("role") in ("home", "product", "blog"):
                continue
            idx = PageIndex(p)
            idx.prom = [s for s in idx.prom if s[0] != "nav"]      # a menu label is not a published page
            if m.score(idx, 99)[0] == 2:
                k = _kind(p["url"], p.get("role", ""))
                if not found[k]:
                    found[k], found["by"] = p["url"], "crawl"
    for u in sitemap_urls:
        if "/products/" in u:
            continue
        k = _kind(u)
        if not found[k] and _slug_match(keyword, u):
            found[k], found["by"] = u, found["by"] or "sitemap"
    return found


def track(plan: list[dict], kw_rows: dict[str, dict], pages: list[dict], sitemap_urls: list[str],
          seasons: dict[str, dict], today: date, detect_live: bool) -> list[dict]:
    for it in plan:
        due = _date(it.get("publish_by"))
        it["days_left"] = (due - today).days if due else None
        season = seasons.get(it.get("season", ""))
        it["season_name"] = season["name"] if season else ""
        expired = bool(season and season["ends_obj"] and season["ends_obj"] < today)
        manual = MANUAL.get((it.get("status") or "").strip().lower())
        found = detect(it, kw_rows.get(tn.strict(it.get("keyword", ""))), pages, sitemap_urls) if detect_live else \
            {"coll": None, "text": None, "by": ""}
        coll_need, text_need, any_need = _needs(it.get("format", ""))
        if coll_need and text_need:
            hit = "live" if found["coll"] and found["text"] else ("partial" if found["coll"] or found["text"] else "")
        elif coll_need:
            hit = "live" if found["coll"] else ""
        elif text_need:
            hit = "live" if found["text"] else ""
        else:
            hit = "live" if (found["coll"] or found["text"]) else ""

        if manual:
            status, by = manual, "manual"
        elif hit:
            status, by = hit, found["by"]
        elif expired:
            status, by = "expired", ""
        elif it["days_left"] is not None and it["days_left"] < 0:
            status, by = "overdue", ""
        elif it["days_left"] is not None and it["days_left"] <= DUE_SOON_DAYS:
            status, by = "due_soon", ""
        else:
            status, by = "planned", ""
        it["state"], it["state_label"], it["detected_by"] = status, LABELS[status], by
        it["live_url"] = (it.get("url") or "").strip() or found["text"] or found["coll"] or ""
    return plan


def summarise_plan(plan: list[dict]) -> dict:
    c = {k: 0 for k in LABELS}
    for it in plan:
        c[it["state"]] += 1
    open_ = [it for it in plan if it["state"] in ("planned", "due_soon", "overdue", "in_progress", "partial")]
    return {"total": len(plan), "counts": c, "open": len(open_)}


def season_cards(seasons: dict[str, dict], plan: list[dict], today: date) -> list[dict]:
    out = []
    for s in seasons.values():
        end = s["ends_obj"] or s["date_obj"]
        if end < today:
            continue
        lead = int(s.get("lead_days") or 21)
        start = s["date_obj"]
        publish_by = date.fromordinal(start.toordinal() - lead)
        items = [it for it in plan if it.get("season") == s["id"]]
        out.append({"id": s["id"], "name": s["name"], "date": start.isoformat(), "days_until": (start - today).days,
                    "ends": end.isoformat(), "is_range": end > start, "in_season": start <= today <= end,
                    "publish_by": publish_by.isoformat(), "days_to_publish": (publish_by - today).days,
                    "verified": s.get("verified", ""), "note": s.get("note", ""),
                    "open_items": sum(1 for it in items if it["state"] not in ("live", "skipped", "expired")),
                    "items": len(items)})
    out.sort(key=lambda x: x["days_until"])
    return out[:5]


def suggest(rows: list[dict], plan: list[dict], n: int = 8) -> list[dict]:
    planned = {tn.strict(it.get("keyword", "")) for it in plan}
    out = []
    for r in rows:      # rows are sorted by score already
        if r["status"] == "using" or r["action"] not in ("Quick win", "Content bet"):
            continue
        if tn.strict(r["keyword"]) in planned:
            continue
        out.append({"keyword": r["keyword"], "cluster": r["cluster"], "score": r["score"], "action": r["action"],
                    "action_type": r["action_type"], "where": r["where"], "comp_users": r["comp_users"],
                    "status": r["status"]})
        if len(out) >= n:
            break
    return out
