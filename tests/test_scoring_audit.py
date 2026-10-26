import unittest

from trovelle_seo.audit import run_audit
from trovelle_seo.scoring import KeywordMatcher, PageIndex, build_rows

SETTINGS = {
    "scoring": {"body_mentions_for_weak": 2, "competitor_bonus_per_site": 0.08,
                "competitor_bonus_cap_sites": 5, "quick_win_min_score": 25},
    "brand": {"name": "B", "domain": "b.test", "tokens": ["brand"], "confusable_domains": []},
    "audit": {"title_min_chars": 30, "meta_min_chars": 70, "meta_max_chars": 160, "blog_posts_min": 10,
              "foreign_brand_terms": ["sverve"], "positioning_terms": ["independent designer"],
              "price_claim_ceiling": 20000},
}
COMP = {"self": {"key": "trovelle", "name": "Brand", "pages": []},
        "competitors": [{"key": "a", "name": "A", "seeded": True, "pages": []},
                        {"key": "b", "name": "B", "seeded": False, "pages": []}]}


def kw(keyword, **kw_):
    row = {"keyword": keyword, "match": "", "cluster": "C", "intent": "x", "demand": 4, "difficulty": 2, "fit": 5,
           "aeo": 3, "action_type": "Blog / guide", "where": "", "evidence": "", "trovelle": "0", "a": "0", "b": "0"}
    row.update(kw_)
    return row


def page(**k):
    base = {"status": 200, "role": "home", "url": "https://x.test/", "title": "", "meta_description": "", "og_title": "",
            "h1": [], "h2": [], "h3": [], "nav": [], "product_titles": [], "faq_questions": [], "text": "",
            "jsonld_types": [], "jsonld_same_as": False}
    base.update(k)
    return base


class Matching(unittest.TestCase):
    def test_title_is_strong_h2_is_weak_body_needs_two(self):
        m = KeywordMatcher(kw("co-ord sets for women"))
        self.assertEqual(m.score(PageIndex(page(title="Designer Co-ord Set | X")), 2)[0], 2)
        self.assertEqual(m.score(PageIndex(page(h2=["Our co-ord sets"])), 2)[0], 1)
        self.assertEqual(m.score(PageIndex(page(text="one co-ord set here")), 2)[0], 0)
        self.assertEqual(m.score(PageIndex(page(text="co-ord set. another co-ord set.")), 2)[0], 1)

    def test_match_override_is_strict(self):
        m = KeywordMatcher(kw("independent designers india", match="independent designer"))
        self.assertEqual(m.score(PageIndex(page(h1=["Independent Designers"])), 2)[0], 2)
        self.assertEqual(m.score(PageIndex(page(h1=["Designers"])), 2)[0], 0)


class Merge(unittest.TestCase):
    def snap(self, **sites):
        return {"sites": sites}

    def test_seed_used_when_nothing_crawled(self):
        rows, _ = build_rows([kw("x y", a="2", trovelle="1")], self.snap(), COMP, SETTINGS, {})
        r = rows[0]
        self.assertEqual(r["comp"]["a"]["score"], 2)
        self.assertEqual(r["comp"]["a"]["src"], "seed")
        self.assertIsNone(r["comp"]["b"]["score"])      # unseeded, uncrawled competitor has no data
        self.assertEqual(r["trovelle"], 1)

    def test_crawl_raises_competitor_but_never_lowers(self):
        s = self.snap(a={"status": "ok", "pages": [page(title="Zebra stripes")]})
        rows, _ = build_rows([kw("x y", a="2")], s, COMP, SETTINGS, {})
        self.assertEqual(rows[0]["comp"]["a"]["score"], 2)    # crawl saw nothing, seed kept
        s = self.snap(a={"status": "ok", "pages": [page(title="X Y here")]})
        rows, _ = build_rows([kw("x y", a="0")], s, COMP, SETTINGS, {})
        self.assertEqual(rows[0]["comp"]["a"]["score"], 2)
        self.assertEqual(rows[0]["comp"]["a"]["src"], "crawl")

    def test_blocked_site_keeps_seed(self):
        s = self.snap(a={"status": "blocked", "pages": []})
        rows, meta = build_rows([kw("x y", a="2")], s, COMP, SETTINGS, {})
        self.assertEqual(rows[0]["comp"]["a"]["score"], 2)
        self.assertEqual(meta["a"]["status"], "blocked")

    def test_live_trovelle_overrides_seed(self):
        s = self.snap(trovelle={"status": "ok", "pages": [page(title="Something else")]})
        rows, _ = build_rows([kw("x y", trovelle="2")], s, COMP, SETTINGS, {})
        self.assertEqual(rows[0]["trovelle"], 0)

    def test_gap_scores_above_owned(self):
        rows, _ = build_rows([kw("aa bb", a="2", trovelle="0"), kw("cc dd", a="2", trovelle="2")],
                             self.snap(), COMP, SETTINGS, {})
        by = {r["keyword"]: r for r in rows}
        self.assertGreater(by["aa bb"]["score"], by["cc dd"]["score"])
        self.assertEqual(by["cc dd"]["action"], "Defend")

    def test_metrics_override_estimates(self):
        rows, _ = build_rows([kw("x y", demand=1)], self.snap(), COMP, SETTINGS, {"x y": {"volume": 20000.0, "kd": 10.0}})
        self.assertEqual(rows[0]["demand"], 5)
        self.assertEqual(rows[0]["demand_src"], "metrics")
        self.assertEqual(rows[0]["difficulty"], 1)


class Audit(unittest.TestCase):
    def audit(self, pages):
        return {c["id"]: c for c in run_audit({"sites": {"trovelle": {"status": "ok", "pages": pages}}}, SETTINGS)["checks"]}

    def test_bare_brand_title_fails(self):
        c = self.audit([page(title="Brand")])
        self.assertEqual(c["home-title"]["status"], "fail")

    def test_good_title_and_meta_pass(self):
        c = self.audit([page(title="Independent Designer Clothing, Co-ords & Dresses | Brand",
                             meta_description="Shop co-ord sets, dresses and shirts from independent designer labels across India. Custom fit, express delivery.",
                             h1=["Independent designer clothing"])])
        self.assertEqual(c["home-title"]["status"], "pass")
        self.assertEqual(c["home-meta"]["status"], "pass")
        self.assertEqual(c["home-h1"]["status"], "pass")
        self.assertEqual(c["positioning"]["status"], "pass")

    def test_foreign_brand_and_typo_detected(self):
        c = self.audit([page(title="Brand", nav=["Cord-Sets"]),
                        page(role="about", url="https://x.test/pages/about", meta_description="Sverve is so much more")])
        self.assertEqual(c["foreign-brand"]["status"], "fail")
        self.assertEqual(c["typo-cord"]["status"], "fail")

    def test_unobserved_jsonld_is_not_a_failure(self):
        c = self.audit([page(title="Brand", jsonld_observed=False)])
        self.assertNotIn("schema-org", c)
        c = self.audit([page(title="Brand")])
        self.assertEqual(c["schema-org"]["status"], "fail")

    def test_price_claim_not_confused_with_price(self):
        c = self.audit([page(title="Brand", text="Priced under ₹20,000. Dress Rs. 9,500 Set Rs. 28,000")])
        self.assertEqual(c["price-claim"]["status"], "warn")
        c = self.audit([page(title="Brand", text="Priced under ₹20,000. Dress Rs. 9,500")])
        self.assertEqual(c["price-claim"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
