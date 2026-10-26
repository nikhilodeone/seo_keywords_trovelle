import unittest

from trovelle_seo import textnorm as tn
from trovelle_seo.extract import extract_page

HTML = """<!doctype html><html lang="en-IN"><head><title>Co-ord Sets for Women | Brand</title>
<meta name="description" content="Shop designer co-ord sets."><link rel="canonical" href="https://x.test/c">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"B","sameAs":["https://instagram.com/b"]},
{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"What is a co-ord set?"}]}]}</script>
<script>var hidden = "do not index this";</script></head>
<body><nav><a href="/collections/dresses">Dresses</a><a href="/collections/cord">Co-ord Sets</a></nav>
<h1>Designer Co-ord Sets</h1><h2>Why shop here?</h2><p>Prices under ₹20,000.</p>
<a href="/products/linen-set">Linen Co-ord Set</a></body></html>"""


class TextNorm(unittest.TestCase):
    def test_coord_variants_match(self):
        a = tn.strict("Co-ord Sets")
        self.assertEqual(a, tn.strict("coord set"))
        self.assertEqual(a, tn.strict("CO ORDS SET"))

    def test_price_forms(self):
        self.assertEqual(tn.strict("Under ₹20,000"), tn.strict("under 20K"))

    def test_accents_and_plurals(self):
        self.assertEqual(tn.strict("Trovélle"), "trovelle")
        self.assertEqual(tn.strict("dresses"), tn.strict("dress"))

    def test_loose_drops_filler(self):
        self.assertEqual(tn.loose("co-ord sets for women"), "coord set")

    def test_contains_is_whole_token(self):
        self.assertTrue(tn.contains("designer coord set", "coord set"))
        self.assertFalse(tn.contains("designers", "design"))


class Extract(unittest.TestCase):
    def setUp(self):
        self.p = extract_page(HTML, "https://x.test/")

    def test_basics(self):
        self.assertEqual(self.p["title"], "Co-ord Sets for Women | Brand")
        self.assertEqual(self.p["meta_description"], "Shop designer co-ord sets.")
        self.assertEqual(self.p["lang"], "en-IN")
        self.assertEqual(self.p["canonical"], "https://x.test/c")
        self.assertEqual(self.p["h1"], ["Designer Co-ord Sets"])
        self.assertEqual(self.p["h2"], ["Why shop here?"])

    def test_nav_and_products(self):
        self.assertIn("Co-ord Sets", self.p["nav"])
        self.assertEqual(self.p["product_titles"], ["Linen Co-ord Set"])

    def test_jsonld(self):
        self.assertIn("FAQPage", self.p["jsonld_types"])
        self.assertTrue(self.p["jsonld_same_as"])
        self.assertEqual(self.p["faq_questions"], ["What is a co-ord set?"])

    def test_scripts_excluded_from_text(self):
        self.assertNotIn("do not index", self.p["text"])
        self.assertIn("under ₹20,000", self.p["text"])


if __name__ == "__main__":
    unittest.main()
