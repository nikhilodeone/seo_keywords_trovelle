"""Keyword matching against crawled pages, coverage merging and the opportunity score."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urlparse

from . import textnorm as tn

STATUS = {0: "gap", 1: "weak", 2: "using"}
QUICK_TYPES = {"On-site fix", "Collection page", "Designer page", "Brand / schema"}
# Only a real crawl overrides seed scores; the hand-captured 4-page baseline is used by the audit only.
OBSERVED_OK = {"ok", "partial"}


# --------------------------------------------------------------------------- loading
def load_keywords(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, start=2):
        if None in r:
            raise ValueError(f"{path}:{i}: too many columns (unquoted comma?) in row '{r.get('keyword')}'")
        for col in ("demand", "difficulty", "fit", "aeo"):
            try:
                r[col] = int(r[col])
            except (TypeError, ValueError):
                raise ValueError(f"{path}:{i}: column '{col}' must be an integer 0-5 (keyword '{r.get('keyword')}')")
            if not 0 <= r[col] <= 5:
                raise ValueError(f"{path}:{i}: column '{col}' out of range 0-5")
    return rows


def load_metrics(path: Path) -> dict[str, dict]:
    """Optional Semrush / Ahrefs / Keyword Planner / Search Console export.
    Columns (case-insensitive): keyword, volume, kd (0-100), optionally clicks, impressions, position."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            kw = tn.strict(r.get("keyword", ""))
            if not kw:
                continue
            m = {}
            for col in ("volume", "kd", "clicks", "impressions", "position"):
                v = r.get(col, "").replace(",", "")
                if v:
                    try:
                        m[col] = float(v)
                    except ValueError:
                        pass
            if m:
                out[kw] = m
    return out


def volume_to_demand(v: float) -> int:
    return 5 if v >= 10000 else 4 if v >= 3000 else 3 if v >= 1000 else 2 if v >= 300 else 1


def kd_to_difficulty(kd: float) -> int:
    return 1 if kd < 20 else 2 if kd < 40 else 3 if kd < 60 else 4 if kd < 80 else 5


# --------------------------------------------------------------------------- matching
class PageIndex:
    """Normalised, labelled segments of one page."""

    def __init__(self, page: dict):
        prom = [("title", page.get("title", "")), ("meta", page.get("meta_description", "")),
                ("meta", page.get("meta_keywords", "")), ("title", page.get("og_title", ""))]
        prom += [("h1", h) for h in page.get("h1", [])]
        prom += [("nav", n) for n in page.get("nav", [])]
        sec = [("heading", h) for h in page.get("h2", []) + page.get("h3", [])]
        sec += [("faq", q) for q in page.get("faq_questions", [])]
        sec += [("product", p) for p in page.get("product_titles", [])]
        self.prom = [(l, tn.strict(t), tn.loose(t)) for l, t in prom if t]
        self.sec = [(l, tn.strict(t), tn.loose(t)) for l, t in sec if t]
        text = page.get("text", "")
        self.body_strict = tn.strict(text)
        self.body_loose = tn.loose(text)
        self.url = page.get("url", "")


class KeywordMatcher:
    def __init__(self, row: dict):
        match = (row.get("match") or "").strip()
        self.variants: list[tuple[str, str]] = []
        if match:
            for v in match.split("|"):
                s = tn.strict(v)
                if s:
                    self.variants.append((s, "strict"))
        if not self.variants:
            lo = tn.loose(row["keyword"])
            self.variants.append((lo, "loose") if lo else (tn.strict(row["keyword"]), "strict"))

    def score(self, idx: PageIndex, body_threshold: int) -> tuple[int, str]:
        best = (0, "")
        for phrase, mode in self.variants:
            col = 2 if mode == "loose" else 1
            for seg in idx.prom:
                if tn.contains(seg[col], phrase):
                    return 2, seg[0]
            for seg in idx.sec:
                if tn.contains(seg[col], phrase) and best[0] < 1:
                    best = (1, seg[0])
            body = idx.body_loose if mode == "loose" else idx.body_strict
            if tn.count(body, phrase) >= body_threshold and best[0] < 1:
                best = (1, "body")
        return best


def observe_site(pages: list[dict], matchers: list[KeywordMatcher], body_threshold: int):
    """For each keyword: (best score, where, url) across the site's pages."""
    indexes = [PageIndex(p) for p in pages if p.get("status", 200) == 200]
    out = []
    for m in matchers:
        best = (0, "", "")
        for idx in indexes:
            s, where = m.score(idx, body_threshold)
            if s > best[0]:
                best = (s, where, idx.url)
                if s == 2:
                    break
        out.append(best)
    return out


# --------------------------------------------------------------------------- merge + score
def _count_roles(pages: list[dict]) -> dict:
    out: dict[str, int] = {}
    for p in pages:
        out[p.get("role", "page")] = out.get(p.get("role", "page"), 0) + 1
    return out


def build_rows(keywords: list[dict], snapshot: dict, comp_cfg: dict, settings: dict,
               metrics: dict[str, dict]) -> tuple[list[dict], dict]:
    sc = settings["scoring"]
    body_threshold = sc["body_mentions_for_weak"]
    matchers = [KeywordMatcher(r) for r in keywords]
    sites_snap = snapshot.get("sites", {})

    sites_meta: dict[str, dict] = {}
    observed: dict[str, list] = {}
    all_sites = [dict(comp_cfg["self"], tier="Self", seeded=True, self=True)] + [
        dict(c, self=False) for c in comp_cfg["competitors"]]
    for s in all_sites:
        snap = sites_snap.get(s["key"], {})
        status = snap.get("status", "not-crawled")
        pages = [p for p in snap.get("pages", []) if p.get("status", 200) == 200]
        usable = status in OBSERVED_OK and bool(pages)
        sites_meta[s["key"]] = {
            "key": s["key"], "name": s["name"], "tier": s.get("tier", ""), "self": s["self"],
            "seeded": bool(s.get("seeded")), "status": status, "pages": len(pages),
            "words": sum(int(p.get("word_count", 0) or 0) for p in pages),
            "roles": snap.get("discovery", {}).get("roles") or _count_roles(pages),
            "notes": snap.get("discovery", {}).get("notes", []),
            "fetched_at": snap.get("fetched_at", ""), "note": s.get("note", ""),
            "errors": snap.get("errors", [])[:5],
        }
        words = sum(int(p.get("word_count", 0) or 0) for p in pages)
        # A competitor that was read properly is scored from the crawl alone; estimates only fill in
        # for sites that were blocked, not crawled, or returned too little text to judge.
        sites_meta[s["key"]]["measured_only"] = bool(
            usable and not s["self"] and sc.get("live_overrides_seed", True)
            and len(pages) >= sc.get("live_min_pages", 2) and words >= sc.get("live_min_words", 1500))
        if usable:
            observed[s["key"]] = observe_site(pages, matchers, body_threshold)
    comp_keys = [c["key"] for c in comp_cfg["competitors"]]

    rows: list[dict] = []
    for i, r in enumerate(keywords):
        seed_t = int(r.get("trovelle") or 0)
        obs_t = observed.get("trovelle", [None] * len(keywords))[i]
        t_score = obs_t[0] if obs_t else seed_t

        comp = {}
        for k in comp_keys:
            meta = sites_meta[k]
            seed = int(r.get(k) or 0) if meta["seeded"] else None
            o = observed.get(k, [None] * len(keywords))[i]
            if o is not None:
                if meta["measured_only"]:
                    val, src = o[0], "crawl"
                else:
                    val = max(o[0], seed or 0)
                    src = "crawl" if o[0] >= (seed or 0) else "seed"
                comp[k] = {"score": val, "src": src, "where": o[1], "url": o[2]}
            elif seed is not None:
                comp[k] = {"score": seed, "src": "seed", "where": "", "url": ""}
            else:
                comp[k] = {"score": None, "src": "n/a", "where": "", "url": ""}

        users = [k for k, v in comp.items() if v["score"]]
        strong = [k for k, v in comp.items() if v["score"] == 2]

        demand, diff = r["demand"], r["difficulty"]
        m = metrics.get(tn.strict(r["keyword"]), {})
        d_src = "estimate"
        if "volume" in m:
            demand, d_src = volume_to_demand(m["volume"]), "metrics"
        if "kd" in m:
            diff = kd_to_difficulty(m["kd"])

        gap_factor = {0: 1.0, 1: 0.6, 2: 0.15}[t_score]
        bonus = 1 + sc["competitor_bonus_per_site"] * min(len(users), sc["competitor_bonus_cap_sites"])
        raw = demand * r["fit"] * (6 - diff) * gap_factor * bonus
        denom = 5 * 5 * 4 * (1 + sc["competitor_bonus_per_site"] * sc["competitor_bonus_cap_sites"])
        score = min(100, round(raw / denom * 100))

        status = STATUS[t_score]
        if status == "using":
            action = "Defend"
        elif score >= sc["quick_win_min_score"]:
            action = "Quick win" if r["action_type"] in QUICK_TYPES else "Content bet"
        else:
            action = "Backlog"

        rows.append({
            "keyword": r["keyword"], "cluster": r["cluster"], "intent": r["intent"],
            "demand": demand, "difficulty": diff, "fit": r["fit"], "aeo": r["aeo"],
            "demand_src": d_src, "volume": m.get("volume"), "kd": m.get("kd"),
            "action_type": r["action_type"], "where": r["where"], "evidence": r["evidence"],
            "trovelle": t_score, "status": status,
            "trovelle_obs": ({"where": obs_t[1], "url": obs_t[2]} if obs_t else None),
            "comp": comp, "comp_users": len(users), "comp_strong": len(strong),
            "score": score, "action": action,
        })
    rows.sort(key=lambda x: (-x["score"], x["keyword"]))
    return rows, sites_meta


def summarise(rows: list[dict], sites_meta: dict, clusters: list[str]) -> dict:
    n = len(rows)
    by_status = {"using": 0, "weak": 0, "gap": 0}
    for r in rows:
        by_status[r["status"]] += 1
    validated_gaps = [r for r in rows if r["status"] != "using" and r["comp_users"] >= 1]

    site_cov = []
    for key, meta in sites_meta.items():
        vals = [r["trovelle"] if key == "trovelle" else r["comp"][key]["score"] for r in rows]
        known = [v for v in vals if v is not None]
        if not known:
            site_cov.append({**meta, "coverage": None, "strong": None, "n": 0})
            continue
        site_cov.append({**meta, "coverage": round(100 * sum(1 for v in known if v >= 1) / len(known)),
                         "strong": round(100 * sum(1 for v in known if v == 2) / len(known)), "n": len(known)})

    matrix = []
    for c in clusters:
        crow = [r for r in rows if r["cluster"] == c]
        cells = {}
        for key, meta in sites_meta.items():
            vals = [r["trovelle"] if key == "trovelle" else r["comp"][key]["score"] for r in crow]
            known = [v for v in vals if v is not None]
            cells[key] = None if not known else {
                "pct": round(100 * sum(1 for v in known if v >= 1) / len(known)),
                "used": sum(1 for v in known if v >= 1), "n": len(known)}
        matrix.append({"cluster": c, "n": len(crow), "cells": cells,
                       "gaps": sum(1 for r in crow if r["status"] != "using" and r["comp_users"] >= 1)})
    return {
        "n_keywords": n, "by_status": by_status,
        "coverage_pct": round(100 * (by_status["using"] + by_status["weak"]) / n) if n else 0,
        "strong_pct": round(100 * by_status["using"] / n) if n else 0,
        "validated_gaps": len(validated_gaps),
        "quick_wins": sum(1 for r in rows if r["action"] == "Quick win"),
        "content_bets": sum(1 for r in rows if r["action"] == "Content bet"),
        "site_coverage": site_cov, "matrix": matrix,
    }


# --------------------------------------------------------------------------- observed phrases
_EXTRA_STOP = {"page", "home", "official", "store", "site", "website", "login", "sign", "cart", "account",
               "menu", "search", "close", "view", "all", "more", "read", "click", "here", "free", "shipping",
               "return", "policy", "contact", "us", "about", "faq", "terms", "privacy", "cookie", "skip",
               "content", "wishlist", "bag", "rs", "inr"}


def _segments(page: dict) -> list[str]:
    segs = [page.get("title", ""), page.get("meta_description", ""), page.get("og_title", "")]
    segs += page.get("h1", []) + page.get("h2", []) + page.get("nav", [])
    return [s for s in segs if s]


def observed_phrases(snapshot: dict, comp_cfg: dict, top_n: int = 60) -> list[dict]:
    """Phrases that actually appear in competitors' titles, headings, meta and navigation.

    Counts distinct competitor sites per phrase, so one site repeating itself does not dominate.
    Brand words (competitor names/domains) are removed.
    """
    sites = snapshot.get("sites", {})
    comps = comp_cfg["competitors"]
    brand_tokens: set[str] = set()
    for c in [comp_cfg["self"]] + comps:
        brand_tokens.update(tn.base_tokens(c["name"]))
        host = urlparse(c["pages"][0]["url"]).netloc if c.get("pages") else ""
        for part in re.split(r"[.\-]", host):
            if part not in ("www", "com", "in", "co"):
                brand_tokens.update(tn.base_tokens(part))
    stop = tn.LOOSE_DROP | _EXTRA_STOP | brand_tokens

    def grams(seg: str):
        toks = [t for t in tn.base_tokens(seg)]
        run: list[str] = []
        out = set()
        for t in toks + ["|"]:                 # "|" flushes the run
            if t == "|" or t in stop or len(t) < 2 or t.isdigit() and len(t) > 5:
                for n in (2, 3):
                    for i in range(len(run) - n + 1):
                        out.add(" ".join(run[i:i + n]))
                run = []
            else:
                run.append(t)
        return out

    per_site: dict[str, dict[str, int]] = {}
    for c in comps:
        s = sites.get(c["key"], {})
        if s.get("status") not in OBSERVED_OK:
            continue
        counts: dict[str, int] = {}
        for pg in s.get("pages", []):
            if pg.get("status") != 200 or pg.get("role") == "product":
                continue            # product names are brand-specific noise for phrase mining
            for seg in _segments(pg):
                for g in grams(seg):
                    counts[g] = counts.get(g, 0) + 1
        per_site[c["key"]] = counts

    tro_pages = [p for p in sites.get("trovelle", {}).get("pages", []) if p.get("status") == 200]
    tro_idx = [PageIndex(p) for p in tro_pages]

    agg: dict[str, dict] = {}
    for key, counts in per_site.items():
        for g, n in counts.items():
            a = agg.setdefault(g, {"sites": [], "count": 0})
            a["sites"].append(key)
            a["count"] += n
    min_sites = 2 if len(per_site) >= 3 else 1
    rows = []
    for g, a in agg.items():
        if len(a["sites"]) < min_sites:
            continue
        if min_sites == 1 and a["count"] < 2:
            continue
        gs = tn.strict(g)
        used = None
        if tro_idx:
            used = any(tn.contains(seg[1], gs) for idx in tro_idx for seg in idx.prom + idx.sec)
        rows.append({"phrase": g, "n_sites": len(a["sites"]), "sites": sorted(a["sites"]),
                     "count": a["count"], "trovelle": used})
    rows.sort(key=lambda r: (-r["n_sites"], -r["count"], r["phrase"]))
    return rows[:top_n]
