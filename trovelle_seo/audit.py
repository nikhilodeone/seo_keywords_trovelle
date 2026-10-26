"""Self-audit of trovelle.in, computed from the crawl snapshot.

Every check returns: id, area, title, status (pass | warn | fail | unknown), impact, detail, fix.
`unknown` means the snapshot did not contain the page needed, never a silent pass.
"""
from __future__ import annotations

import re

from . import textnorm as tn

CATEGORY_WORDS = {
    "dress", "kaftan", "coord", "set", "shirt", "top", "skirt", "saree", "kurta", "kurti", "jacket",
    "pant", "trouser", "jumpsuit", "gown", "lehenga", "bag", "jewellery", "jewelry", "earring",
    "necklace", "bracelet", "ring", "scarf", "stole", "blouse", "tunic", "cape", "shrug", "palazzo",
    "sharara", "anarkali", "dupatta", "belt", "clutch", "shorts", "short", "tee", "sunglass", "kimono",
}
BARE_BRAND_RX = re.compile(r"^\W*[a-z]+\W*$", re.I)
PRICE_RX = re.compile(r"(?:₹|rs\.?\s?)\s?([0-9][0-9,]{2,})", re.I)
CLAIM_RX = re.compile(r"under\s*(?:₹|rs\.?\s?)?\s?([0-9][0-9,]*)\s?(k)?", re.I)


def _chk(id_, area, title, status, impact, detail, fix=""):
    return {"id": id_, "area": area, "title": title, "status": status, "impact": impact,
            "detail": detail, "fix": fix}


def _by_role(pages: list[dict], role: str) -> list[dict]:
    return [p for p in pages if p.get("role") == role and p.get("status") == 200]


def _first(pages, role):
    r = _by_role(pages, role)
    return r[0] if r else None


def _is_bare(title: str, brand_tokens: list[str]) -> bool:
    t = tn.strict(title)
    toks = t.split()
    if not toks:
        return True
    return len(toks) <= 2 and any(b in toks for b in brand_tokens)


def run_audit(snapshot: dict, settings: dict) -> dict:
    a = settings["audit"]
    brand = settings["brand"]
    tokens = [tn.strict(t) for t in brand["tokens"]]
    site = snapshot.get("sites", {}).get("trovelle", {})
    pages = [p for p in site.get("pages", []) if p.get("status") == 200]
    checks: list[dict] = []

    if not pages:
        return {"checks": [], "counts": {"pass": 0, "warn": 0, "fail": 0, "unknown": 0},
                "available": False, "pages_checked": 0, "source": site.get("status", "not-crawled")}

    home = _first(pages, "home")
    collections = _by_role(pages, "collection")
    faq = _first(pages, "faq")

    # ---- titles -------------------------------------------------------------
    if home:
        t = home.get("title", "")
        bare = _is_bare(t, tokens) or len(t) < a["title_min_chars"]
        checks.append(_chk(
            "home-title", "Titles & meta", "Homepage title tells people what the store sells",
            "fail" if bare else "pass", "High",
            f"Title is “{t}” ({len(t)} characters)." if t else "No <title> found.",
            "Rewrite as: Brand | Independent Indian Designer Clothing, Co-ords & Dresses (50-60 characters)."))
        m = home.get("meta_description", "")
        if not m:
            st, d = "fail", "No meta description."
        elif len(m) < a["meta_min_chars"] or len(m) > a["meta_max_chars"]:
            st, d = "warn", f"Meta description is {len(m)} characters (aim {a['meta_min_chars']}-{a['meta_max_chars']}): “{m}”"
        elif _is_bare(m, tokens):
            st, d = "fail", f"Meta description only repeats the brand: “{m}”"
        else:
            st, d = "pass", f"{len(m)} characters."
        checks.append(_chk("home-meta", "Titles & meta", "Homepage meta description sells the click", st, "High", d,
                           "Write 120-155 characters naming the category, 'independent designers' and a reason to click."))
        h1 = home.get("h1", [])
        checks.append(_chk(
            "home-h1", "Titles & meta", "Homepage has exactly one H1",
            "pass" if len(h1) == 1 else ("fail" if not h1 else "warn"), "Medium",
            f"{len(h1)} H1 tag(s): " + "; ".join(f"“{h}”" for h in h1[:4]) if h1 else "No H1 found.",
            "Keep one H1 carrying the main phrase; demote the rest to H2."))
        if "lang" in home:
            checks.append(_chk("home-lang", "Technical", "Page language is declared",
                               "pass" if home.get("lang") else "warn", "Low",
                               f"lang=\"{home.get('lang', '')}\"" if home.get("lang") else "No lang attribute on <html>.",
                               "Set lang=\"en-IN\"."))
        if "canonical" in home:
            checks.append(_chk("home-canonical", "Technical", "Canonical URL is set",
                               "pass" if home.get("canonical") else "warn", "Low",
                               home.get("canonical") or "No canonical link on the homepage.",
                               "Add <link rel=\"canonical\"> (Shopify themes do this by default)."))

    # ---- collection titles ----------------------------------------------------
    if collections:
        bare = [p for p in collections if _is_bare(p.get("title", ""), tokens)
                or not p.get("meta_description") or _is_bare(p.get("meta_description", ""), tokens)]
        st = "pass" if not bare else ("fail" if len(bare) >= max(1, len(collections) // 2) else "warn")
        checks.append(_chk(
            "coll-meta", "Titles & meta", "Collection pages have their own title and meta",
            st, "High",
            f"{len(bare)} of {len(collections)} collection pages have a bare-brand title or meta: "
            + ", ".join(p["url"].rstrip("/").split("/")[-1] for p in bare[:8]) if bare
            else f"All {len(collections)} collection pages have specific titles and metas.",
            "Give each collection its own title ('Co-ord Sets for Women | Brand') and a 120-155 character meta."))

    # ---- typo ------------------------------------------------------------------
    typo_hits = []
    for p in pages:
        blob = " ".join([p.get("title", ""), p.get("url", ""), " ".join(p.get("nav", [])),
                         " ".join(p.get("h1", [])), p.get("meta_description", "")]).lower()
        if "cord-set" in blob or "cord set" in blob:
            typo_hits.append(p["url"])
    if any(p for p in pages if p.get("role") in ("home", "collection")):
        checks.append(_chk(
            "typo-cord", "Content", "Category is spelled 'co-ord', not 'cord'",
            "fail" if typo_hits else "pass", "High",
            f"“cord-sets” appears on {len(typo_hits)} page(s), including the collection URL {next((u for u in typo_hits if 'cord-set' in u), typo_hits[0])} and the main navigation" if typo_hits
            else "No 'cord set' spelling found in titles, URLs, nav or H1s.",
            "Rename the collection to co-ord-sets and 301-redirect /cord-sets. Shoppers search 'co-ord set'."))

    # ---- foreign brand text ------------------------------------------------------
    foreign = []
    for p in pages:
        blob = tn.strict(" ".join([p.get("title", ""), p.get("meta_description", ""), p.get("text", "")]))
        for term in a["foreign_brand_terms"]:
            if tn.contains(blob, tn.strict(term)):
                foreign.append((term, p["url"]))
    checks.append(_chk(
        "foreign-brand", "Content", "No copy left over from another brand",
        "fail" if foreign else "pass", "High",
        "; ".join(f"“{t}” on {u}" for t, u in foreign[:4]) if foreign else "No leftover brand names found in crawled pages.",
        "Replace the template text. A stray brand name in a meta description shows up in Google results."))

    # ---- positioning ---------------------------------------------------------------
    if home:
        blob = tn.strict(" ".join([home.get("title", ""), home.get("meta_description", ""),
                                   " ".join(home.get("h1", []) + home.get("h2", [])), home.get("text", "")]))
        found = [t for t in a["positioning_terms"] if tn.contains(blob, tn.strict(t))]
        checks.append(_chk(
            "positioning", "Content", "Homepage says 'independent designers' in words",
            "pass" if found else "fail", "High",
            f"Found: {', '.join(found)}." if found else "None of the positioning phrases appear on the homepage.",
            "Add the phrase to the H1/intro and the title. It is the brand's main differentiator and nobody else owns it."))

    # ---- price claim -----------------------------------------------------------------
    claim_hit = None
    max_price = 0
    for p in pages:
        # drop "under ₹20,000"-style claims first so a claim is never mistaken for a price
        for m in PRICE_RX.finditer(CLAIM_RX.sub(" ", p.get("text", ""))):
            try:
                max_price = max(max_price, int(m.group(1).replace(",", "")))
            except ValueError:
                pass
    for p in [home] + collections if home else collections:
        text = (p or {}).get("text", "") + " " + (p or {}).get("meta_description", "")
        for m in CLAIM_RX.finditer(text):
            v = int(m.group(1).replace(",", "") or 0) * (1000 if m.group(2) else 1)
            if v >= 5000:
                claim_hit = (v, p["url"])
                break
        if claim_hit:
            break
    if claim_hit and max_price:
        ok = max_price <= claim_hit[0]
        checks.append(_chk(
            "price-claim", "Content", "Price claims match the catalogue",
            "pass" if ok else "warn", "Medium",
            f"Page says 'under {claim_hit[0]:,}' but the highest price seen across crawled pages is ₹{max_price:,}."
            if not ok else f"Highest price seen (₹{max_price:,}) is within the claimed ₹{claim_hit[0]:,}.",
            "Change the claim to 'from ₹X' or 'starting at', or tag only in-range pieces."))
    elif max_price:
        checks.append(_chk("price-claim", "Content", "Price claims match the catalogue", "unknown", "Low",
                           "No 'under ₹X' claim found in crawled text.", ""))

    # ---- product titles ------------------------------------------------------------------
    titles = []
    for p in pages:
        titles.extend(p.get("product_titles", []))
    titles = list(dict.fromkeys(titles))
    if len(titles) >= 5:
        no_cat = [t for t in titles if not (set(tn.strict(t).split()) & CATEGORY_WORDS)]
        share = len(no_cat) / len(titles)
        checks.append(_chk(
            "product-titles", "Titles & meta", "Product names include what the item is",
            "pass" if share < 0.25 else ("warn" if share < 0.6 else "fail"), "High",
            f"{len(no_cat)} of {len(titles)} product titles have no category word, e.g. "
            + ", ".join(f"“{t}”" for t in no_cat[:4]),
            "Template: 'Name + Fabric/Print + Category' ('Daniyal Linen Co-ord Set'), or set the SEO title separately."))

    # ---- schema --------------------------------------------------------------------------------
    sp = [p for p in pages if p.get("jsonld_observed", True)]   # JSON-LD actually visible to the crawl
    s_home = _first(sp, "home")
    s_faq = _first(sp, "faq")
    s_prods = _by_role(sp, "product") + _by_role(sp, "followed")
    s_colls = _by_role(sp, "collection")

    if s_home:
        home_s = s_home
        t = set(home_s.get("jsonld_types", []))
        org = bool(t & {"Organization", "OnlineStore", "Store", "LocalBusiness"})
        st = "pass" if org and home_s.get("jsonld_same_as") else ("warn" if org else "fail")
        checks.append(_chk(
            "schema-org", "Structured data", "Organization schema with sameAs links",
            st, "High",
            f"Types on homepage: {', '.join(sorted(t)) or 'none'}. sameAs {'present' if home_s.get('jsonld_same_as') else 'missing'}.",
            "Add Organization JSON-LD with name, logo and sameAs (Instagram, LinkedIn). This is how AI engines tell you apart from trovelle.com."))
    if s_faq:
        has = "FAQPage" in set(s_faq.get("jsonld_types", []))
        checks.append(_chk(
            "schema-faq", "Structured data", "FAQ page has FAQPage schema", "pass" if has else "fail", "High",
            f"{len(s_faq.get('faq_questions', []))} marked-up questions." if has
            else "FAQ page has no FAQPage JSON-LD, so answers are invisible to rich results and answer engines.",
            "Add FAQPage JSON-LD for each question and answer on the page."))
    prods = s_prods
    if prods:
        ok = [p for p in prods if "Product" in p.get("jsonld_types", [])]
        checks.append(_chk("schema-product", "Structured data", "Product pages carry Product schema",
                           "pass" if ok else "fail", "Medium",
                           f"{len(ok)} of {len(prods)} sampled product pages have Product JSON-LD.",
                           "Enable Product schema with price, availability and brand in the theme."))
    if s_colls:
        ok = [p for p in s_colls if {"ItemList", "CollectionPage", "BreadcrumbList"} & set(p.get("jsonld_types", []))]
        checks.append(_chk("schema-coll", "Structured data", "Collection pages carry ItemList / breadcrumb schema",
                           "pass" if len(ok) == len(s_colls) else ("warn" if ok else "fail"), "Low",
                           f"{len(ok)} of {len(s_colls)} collection pages.",
                           "Add BreadcrumbList and ItemList markup."))

    # ---- blog --------------------------------------------------------------------------------------
    sm = site.get("sitemap") or {}
    blog_page = _first(pages, "blog")
    if sm.get("ok"):
        n = sm.get("blog_posts", 0)
        checks.append(_chk(
            "blog-depth", "Content", "Blog has enough posts to build topical authority",
            "pass" if n >= a["blog_posts_min"] else ("warn" if n >= 3 else "fail"), "High",
            f"{n} blog post URL(s) in the sitemap (target {a['blog_posts_min']}+). "
            f"Catalogue: {sm.get('products', 0)} products, {sm.get('collections', 0)} collections.",
            "Publish the P0/P1 posts in the Content Plan tab; start with Diwali and gifting."))
    elif blog_page is None:
        checks.append(_chk("blog-depth", "Content", "Blog has enough posts to build topical authority",
                           "unknown", "High", "Blog page and sitemap could not be read in this crawl.",
                           "Check /blogs/news and /sitemap.xml are public."))

    # ---- AEO --------------------------------------------------------------------------------------------
    if s_faq:
        faq = s_faq
        qs = len(faq.get("faq_questions", []))
        words = faq.get("word_count", 0)
        checks.append(_chk(
            "faq-depth", "AEO", "FAQ answers the questions shoppers ask",
            "pass" if qs >= 12 else ("warn" if qs >= 5 else "fail"), "High",
            f"{qs} questions in FAQPage markup; page has about {words} words.",
            "Add the questions in the AEO tab: who you are, how curation works, sizing, returns, gifting. Answer first, then detail."))
    if home:
        qh = [h for h in home.get("h2", []) + home.get("h3", []) if h.strip().endswith("?")]
        checks.append(_chk(
            "question-heads", "AEO", "Some headings are phrased as questions",
            "pass" if qh else "warn", "Medium",
            f"{len(qh)} question-style heading(s) on the homepage." if qh else "No question-style headings on the homepage.",
            "Use headings like 'What is Brand?' followed by a 40-60 word answer."))

    # ---- entity ----------------------------------------------------------------------------------------------
    conf = ", ".join(brand.get("confusable_domains", []))
    if conf:
        checks.append(_chk(
            "entity", "AEO", f"Brand name is not confused with {conf}", "unknown", "High",
            f"{conf} is a different business with a near-identical name. This script cannot see search rankings.",
            "Search the exact brand name monthly; keep Organization schema, sameAs links and 'Trovélle (trovelle.in)' wording consistent."))

    counts = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
    for c in checks:
        counts[c["status"]] += 1
    order = {"fail": 0, "warn": 1, "unknown": 2, "pass": 3}
    imp = {"High": 0, "Medium": 1, "Low": 2}
    checks.sort(key=lambda c: (order[c["status"]], imp[c["impact"]]))
    return {"checks": checks, "counts": counts, "available": True, "pages_checked": len(pages),
            "source": site.get("status", ""), "fetched_at": site.get("fetched_at", "")}
