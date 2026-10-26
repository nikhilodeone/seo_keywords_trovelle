"""Turn an HTML document into the SEO signals the dashboard needs. stdlib only."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "svg"}
HEADING_TAGS = {"h1", "h2", "h3"}


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.lang = ""
        self.headings: dict[str, list[str]] = {"h1": [], "h2": [], "h3": []}
        self.anchors: list[tuple[str, str]] = []  # (href, text)
        self.nav_anchor_texts: list[str] = []
        self.jsonld_raw: list[str] = []
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self._cur_heading: str | None = None
        self._cur_heading_buf: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []
        self._cur_a_href: str | None = None
        self._cur_a_buf: list[str] = []
        self._nav_name: str | None = None     # tag that opened the current nav region
        self._nav_count = 0
        self.nav_hrefs: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self._stack.append(tag)
        if self._nav_name is None:
            if tag == "nav" or a.get("role") == "navigation":
                self._nav_name, self._nav_count = tag, 1
        elif tag == self._nav_name:
            self._nav_count += 1
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
            self._title_buf = []
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link":
            if "canonical" in a.get("rel", "").lower().split():
                self.canonical = a.get("href", "")
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
            else:
                self._skip_depth += 1
        elif tag in SKIP_TEXT_TAGS:
            self._skip_depth += 1
        elif tag in HEADING_TAGS:
            self._cur_heading = tag
            self._cur_heading_buf = []
        elif tag == "a":
            self._cur_a_href = urljoin(self.base_url, a.get("href", ""))
            self._cur_a_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            self.title = _clean(" ".join(self._title_buf))
        elif tag == "script":
            if self._in_jsonld:
                self._in_jsonld = False
                self.jsonld_raw.append("".join(self._jsonld_buf))
            elif self._skip_depth:
                self._skip_depth -= 1
        elif tag in SKIP_TEXT_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in HEADING_TAGS and self._cur_heading == tag:
            txt = _clean(" ".join(self._cur_heading_buf))
            if txt:
                self.headings[tag].append(txt)
            self._cur_heading = None
        elif tag == "a" and self._cur_a_href is not None:
            txt = _clean(" ".join(self._cur_a_buf))
            if txt:
                self.anchors.append((self._cur_a_href, txt))
                if self._nav_name is not None:
                    self.nav_hrefs.append(self._cur_a_href)
                    if len(txt.split()) <= 6:
                        self.nav_anchor_texts.append(txt)
            self._cur_a_href = None
        if self._nav_name is not None and tag == self._nav_name:
            self._nav_count -= 1
            if self._nav_count <= 0:
                self._nav_name = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._skip_depth:
            return
        if self._cur_heading:
            self._cur_heading_buf.append(data)
        if self._cur_a_href is not None:
            self._cur_a_buf.append(data)
        self.text_parts.append(data)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _walk_jsonld(node, out_types: list[str], faq: list[str]):
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out_types, faq)
        return
    if not isinstance(node, dict):
        return
    t = node.get("@type")
    types = t if isinstance(t, list) else ([t] if t else [])
    for x in types:
        if isinstance(x, str):
            out_types.append(x)
    if "FAQPage" in types:
        ents = node.get("mainEntity", [])
        ents = ents if isinstance(ents, list) else [ents]
        for e in ents:
            if isinstance(e, dict) and e.get("name"):
                faq.append(_clean(str(e["name"])))
    for key in ("@graph", "mainEntity", "itemListElement", "hasPart"):
        if key in node:
            _walk_jsonld(node[key], out_types, faq)


def extract_page(html: str, url: str, max_text_chars: int = 12000) -> dict:
    p = _Parser(url)
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed markup: keep what we parsed
        pass

    types: list[str] = []
    faq: list[str] = []
    same_as = False
    for raw in p.jsonld_raw:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        _walk_jsonld(data, types, faq)
        if '"sameAs"' in raw:
            same_as = True

    host = urlparse(url).netloc
    product_titles: list[str] = []
    seen = set()
    for href, txt in p.anchors:
        if "/products/" in href and urlparse(href).netloc == host:
            key = href.split("?")[0]
            if key not in seen:
                seen.add(key)
                product_titles.append(txt)

    text = _clean(" ".join(p.text_parts))
    og_title = p.meta.get("og:title", "")
    return {
        "url": url,
        "title": p.title,
        "meta_description": p.meta.get("description", ""),
        "meta_keywords": p.meta.get("keywords", ""),
        "og_title": og_title,
        "canonical": p.canonical,
        "lang": p.lang,
        "h1": p.headings["h1"][:10],
        "h2": p.headings["h2"][:30],
        "h3": p.headings["h3"][:40],
        "nav": list(dict.fromkeys(p.nav_anchor_texts))[:120],
        "product_titles": product_titles[:80],
        "jsonld_types": sorted(set(types)),
        "jsonld_same_as": same_as,
        "faq_questions": faq[:40],
        "word_count": len(text.split()),
        "text": text[:max_text_chars],
        "links": [h for h, _ in p.anchors][:600],
        "nav_links": list(dict.fromkeys(p.nav_hrefs))[:400],
    }
