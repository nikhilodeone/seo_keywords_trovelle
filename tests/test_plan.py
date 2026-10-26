import unittest
from datetime import date

from trovelle_seo.plan import detect, load_seasons, season_cards, suggest, summarise_plan, track

TODAY = date(2026, 10, 26)
SEASONS = {
    "diwali": {"id": "diwali", "name": "Diwali", "date_obj": date(2026, 11, 8), "ends_obj": date(2026, 11, 8),
               "lead_days": "21", "verified": "yes", "note": ""},
    "wedding": {"id": "wedding", "name": "Wedding season", "date_obj": date(2026, 11, 15), "ends_obj": date(2027, 2, 28),
                "lead_days": "30", "verified": "approx", "note": ""},
    "past": {"id": "past", "name": "Past", "date_obj": date(2026, 10, 1), "ends_obj": date(2026, 10, 20),
             "lead_days": "7", "verified": "yes", "note": ""},
}
KW = {"co ord set under 20000": {"keyword": "co-ord sets under 20000", "match": ""},
      "diwali outfit idea for woman": {"keyword": "diwali outfit ideas for women", "match": ""}}


def item(**k):
    base = {"id": "c", "title": "T", "keyword": "co-ord sets under 20000", "format": "Blog guide", "priority": "P1",
            "publish_by": "2026-12-01", "season": "", "status": "", "url": ""}
    base.update(k)
    return base


def page(role, url, title="", h1=(), nav=()):
    return {"status": 200, "role": role, "url": url, "title": title, "meta_description": "", "og_title": "",
            "h1": list(h1), "h2": [], "h3": [], "nav": list(nav), "product_titles": [], "faq_questions": [], "text": ""}


def run(items, pages=(), sm=(), live=True):
    from trovelle_seo import textnorm as tn
    kw = {tn.strict(v["keyword"]): v for v in KW.values()}
    return track(items, kw, list(pages), list(sm), SEASONS, TODAY, live)


class Dates(unittest.TestCase):
    def test_states_follow_the_calendar(self):
        out = run([item(id="a", publish_by="2026-10-20"), item(id="b", publish_by="2026-11-05"),
                   item(id="c", publish_by="2026-12-20")], live=False)
        self.assertEqual([i["state"] for i in out], ["overdue", "due_soon", "planned"])
        self.assertEqual(out[1]["days_left"], 10)

    def test_festival_item_expires_instead_of_going_overdue(self):
        out = run([item(season="past", publish_by="2026-10-10")], live=False)
        self.assertEqual(out[0]["state"], "expired")

    def test_manual_status_wins(self):
        out = run([item(status="Published", url="https://x.in/blogs/news/a", publish_by="2026-10-01"),
                   item(status="skipped"), item(status="in progress")], live=False)
        self.assertEqual([i["state"] for i in out], ["live", "skipped", "in_progress"])
        self.assertEqual(out[0]["live_url"], "https://x.in/blogs/news/a")
        self.assertEqual(out[0]["detected_by"], "manual")


class Detection(unittest.TestCase):
    def test_blog_post_with_keyword_in_title_makes_it_live(self):
        pg = [page("post", "https://x.in/blogs/news/co-ord-guide", title="Co-ord Sets Under ₹20,000: 15 Picks")]
        out = run([item()], pg)
        self.assertEqual(out[0]["state"], "live")
        self.assertEqual(out[0]["detected_by"], "crawl")

    def test_menu_label_does_not_count(self):
        pg = [page("collection", "https://x.in/collections/a", title="New In", nav=["Co-ord sets under 20000"])]
        self.assertEqual(run([item()], pg)[0]["state"], "planned")

    def test_home_and_products_never_count(self):
        pg = [page("home", "https://x.in/", title="Co-ord sets under 20000"),
              page("product", "https://x.in/products/p", title="Co-ord sets under 20000")]
        self.assertEqual(run([item()], pg)[0]["state"], "planned")

    def test_collection_plus_guide_is_partial_until_both_exist(self):
        it = lambda: item(format="Collection page + guide")  # noqa: E731
        coll = page("collection", "https://x.in/collections/co-ord-under", title="Co-ord sets under 20000 | Trovélle")
        post = page("post", "https://x.in/blogs/news/g", title="Co-ord sets under 20000: how to choose")
        self.assertEqual(run([it()], [coll])[0]["state"], "partial")
        self.assertEqual(run([it()], [coll, post])[0]["state"], "live")

    def test_sitemap_slug_match(self):
        out = run([item()], [], ["https://x.in/blogs/news/best-coord-sets-under-20000-for-women"])
        self.assertEqual(out[0]["state"], "live")
        self.assertEqual(out[0]["detected_by"], "sitemap")
        out = run([item()], [], ["https://x.in/blogs/news/something-else-entirely"])
        self.assertEqual(out[0]["state"], "planned")

    def test_no_detection_without_a_live_crawl(self):
        pg = [page("post", "https://x.in/blogs/news/g", title="Co-ord Sets Under ₹20,000")]
        self.assertEqual(run([item()], pg, live=False)[0]["state"], "planned")


class Summaries(unittest.TestCase):
    def test_summary_counts(self):
        out = run([item(status="done"), item(publish_by="2026-10-01"), item()], live=False)
        s = summarise_plan(out)
        self.assertEqual(s["counts"]["live"], 1)
        self.assertEqual(s["counts"]["overdue"], 1)
        self.assertEqual(s["open"], 2)

    def test_seasons_hide_finished_and_flag_ranges(self):
        cards = season_cards(SEASONS, run([item(season="diwali")], live=False), TODAY)
        names = [c["name"] for c in cards]
        self.assertNotIn("Past", names)
        self.assertEqual(names[0], "Diwali")
        self.assertEqual(cards[0]["days_until"], 13)
        self.assertEqual(cards[0]["days_to_publish"], -8)      # 8 Nov minus 21 days is already behind us
        self.assertTrue([c for c in cards if c["name"] == "Wedding season"][0]["is_range"])

    def test_suggestions_skip_keywords_already_planned(self):
        rows = [{"keyword": "co-ord sets under 20000", "status": "gap", "action": "Quick win", "cluster": "c",
                 "score": 50, "action_type": "x", "where": "w", "comp_users": 1},
                {"keyword": "new idea", "status": "gap", "action": "Content bet", "cluster": "c",
                 "score": 40, "action_type": "x", "where": "w", "comp_users": 2},
                {"keyword": "owned", "status": "using", "action": "Defend", "cluster": "c",
                 "score": 3, "action_type": "x", "where": "w", "comp_users": 0}]
        out = suggest(rows, [item()])
        self.assertEqual([r["keyword"] for r in out], ["new idea"])

    def test_seed_seasons_file_parses(self):
        from pathlib import Path
        s = load_seasons(Path(__file__).resolve().parent.parent / "data" / "seed" / "seasons.csv")
        self.assertIn("diwali", s)
        self.assertEqual(s["diwali"]["date_obj"], date(2026, 11, 8))


if __name__ == "__main__":
    unittest.main()
