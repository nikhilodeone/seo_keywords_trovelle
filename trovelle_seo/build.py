"""Assemble all data, append history, and render site/index.html + site/data.json."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from . import textnorm as tn
from .audit import run_audit
from .plan import load_seasons, season_cards, suggest, summarise_plan, track
from .scoring import (OBSERVED_OK, build_rows, load_keywords, load_metrics, observed_phrases, summarise)

IST = timezone(timedelta(hours=5, minutes=30))
PLACEHOLDER = "/*__DATA__*/null"


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, start=2):
        if None in r:
            raise ValueError(f"{path}:{i}: too many columns (unquoted comma?)")
    return rows


def effective_snapshot(live: dict | None, seed: dict | None) -> tuple[dict, dict]:
    """Live crawl where usable; the hand-captured Trovelle baseline where the live one is missing or blocked."""
    snap = {"generated_at": (live or {}).get("generated_at", ""), "sites": dict((live or {}).get("sites", {}))}
    prov = {"trovelle_source": "live crawl"}
    t = snap["sites"].get("trovelle")
    usable = bool(t) and t.get("status") in OBSERVED_OK and any(p.get("status") == 200 for p in t.get("pages", []))
    if not usable and seed and seed.get("sites", {}).get("trovelle"):
        snap["sites"]["trovelle"] = seed["sites"]["trovelle"]
        prov["trovelle_source"] = "seed baseline (26 Oct 2026)"
        if t:
            prov["trovelle_live_status"] = t.get("status")
    return snap, prov


def _kw_index(rows: list[dict]) -> dict[str, dict]:
    return {tn.strict(r["keyword"]): r for r in rows}


def _attach(items: list[dict], idx: dict[str, dict]) -> list[dict]:
    for it in items:
        r = idx.get(tn.strict(it.get("keyword", "")))
        it["kw"] = ({"status": r["status"], "score": r["score"], "demand": r["demand"],
                     "difficulty": r["difficulty"], "comp_users": r["comp_users"]} if r else None)
    return items


def append_history(path: Path, entry: dict) -> list[dict]:
    """One line per calendar day (IST); a second build the same day replaces that day's line."""
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    rows = [r for r in rows if r.get("date") != entry["date"]] + [entry]
    rows.sort(key=lambda r: r["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return rows


def assemble(root: Path, now: datetime | None = None, write_history: bool = True) -> dict:
    now = now or datetime.now(IST)
    cfg = root / "config"
    settings = _read_json(cfg / "settings.json")
    comp_cfg = _read_json(cfg / "competitors.json")
    keywords = load_keywords(root / "data" / "seed" / "keywords.csv")
    metrics = load_metrics(root / "data" / "inputs" / "metrics.csv")
    live = _read_json(root / "data" / "live" / "snapshot.json")
    seed_snap = _read_json(root / "data" / "seed" / "trovelle_snapshot.json")
    snapshot, prov = effective_snapshot(live, seed_snap)

    rows, sites_meta = build_rows(keywords, snapshot, comp_cfg, settings, metrics)
    clusters = list(dict.fromkeys(r["cluster"] for r in keywords))
    summary = summarise(rows, sites_meta, clusters)
    audit = run_audit(snapshot, settings)
    phrases = observed_phrases(snapshot, comp_cfg)
    idx = _kw_index(rows)

    aeo = _attach(_read_csv(root / "data" / "seed" / "aeo_questions.csv"), idx)
    plan = _attach(_read_csv(root / "data" / "seed" / "content_plan.csv"), idx)
    today = now.date()
    seasons = load_seasons(root / "data" / "seed" / "seasons.csv")
    tro = snapshot["sites"].get("trovelle", {})
    tro_live = prov["trovelle_source"].startswith("live")
    kw_by_key = {tn.strict(k["keyword"]): k for k in keywords}
    sm_urls = (tro.get("sitemap") or {}).get("other_urls", []) if tro_live else []
    plan = track(plan, kw_by_key, tro.get("pages", []) if tro_live else [], sm_urls, seasons, today, tro_live)
    plan_summary = summarise_plan(plan)
    plan_summary["detection"] = "crawl of trovelle.in" if tro_live else "off (no live crawl of trovelle.in yet)"
    upcoming = season_cards(seasons, plan, today)
    suggested = suggest(rows, plan)

    live_sites = [k for k, m in sites_meta.items() if m["status"] in OBSERVED_OK and k != "trovelle"
                  and snapshot["sites"].get(k, {}).get("fetched_at", "") and
                  snapshot["sites"][k].get("status") != "seed-observation"]
    blocked = [m["name"] for k, m in sites_meta.items() if not m["self"] and m["status"] in ("blocked", "error")]
    never = [m["name"] for k, m in sites_meta.items() if not m["self"] and m["status"] == "not-crawled"]

    entry = {
        "date": today.isoformat(),
        "coverage_pct": summary["coverage_pct"], "strong_pct": summary["strong_pct"],
        "validated_gaps": summary["validated_gaps"], "quick_wins": summary["quick_wins"],
        "n_keywords": summary["n_keywords"], "audit": audit["counts"],
        "trovelle_source": prov["trovelle_source"], "live_competitors": len(live_sites),
        "plan": {"live": plan_summary["counts"]["live"], "overdue": plan_summary["counts"]["overdue"],
                 "open": plan_summary["open"], "total": plan_summary["total"]},
        "site_coverage": {s["key"]: s["coverage"] for s in summary["site_coverage"]},
    }
    history_path = root / "data" / "live" / "history.jsonl"
    history = append_history(history_path, entry) if write_history else [entry]

    data = {
        "meta": {
            "version": __version__, "built_at": now.isoformat(timespec="seconds"),
            "built_label": now.strftime("%d %b %Y, %H:%M IST"),
            "crawl_at": (live or {}).get("generated_at", ""),
            "brand": settings["brand"], "provenance": prov,
            "live_competitors": len(live_sites), "total_competitors": len(comp_cfg["competitors"]),
            "blocked": blocked, "not_crawled": never,
            "has_live": bool(live), "metrics_rows": len(metrics),
        },
        "summary": summary, "sites": list(sites_meta.values()), "keywords": rows, "clusters": clusters,
        "phrases": phrases, "audit": audit, "aeo": aeo, "plan": plan, "plan_summary": plan_summary,
        "seasons": upcoming, "suggested": suggested, "history": history,
    }
    return data


def render(root: Path, data: dict) -> Path:
    tpl = (root / "trovelle_seo" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise RuntimeError("template is missing the data placeholder")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out_dir = root / "site"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "index.html").write_text(tpl.replace(PLACEHOLDER, blob), encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir / "index.html"


def run(root: Path, log=print) -> Path:
    data = assemble(root)
    out = render(root, data)
    s = data["summary"]
    log(f"built {out}  ({s['n_keywords']} keywords, {s['validated_gaps']} validated gaps, "
        f"{data['meta']['live_competitors']}/{data['meta']['total_competitors']} competitors live)")
    return out
