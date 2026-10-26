# Trovélle SEO & AEO dashboard

A live dashboard that shows which search keywords Trovélle's competitors use, which ones trovelle.in is missing, and what to publish (blogs, collection pages, FAQ answers) to be found in Google and quoted by AI answer engines.

It rebuilds itself every morning (08:00 IST) with GitHub Actions and is hosted on Vercel. No server, no database, no paid API, no dependencies beyond Python 3.10+.

## What you get

| Tab | What it answers |
|---|---|
| Overview | How much of the keyword set each site targets, a keyword-group heatmap, top opportunities, trend over time |
| Competitor keywords | Phrases competitors actually put in titles, headings, meta and menus (mined live), plus crawl status per site |
| Opportunities | 90 tracked keywords with an opportunity score, which competitors use each, where to use it, filter/sort/CSV export |
| Content plan | 22 posts and pages with publish-by dates (Diwali-sensitive ones first) and an AEO structure for each |
| AEO answers | 22 answer-first drafts for FAQ and blog use, with the schema type and what to verify before publishing |
| Site audit | Computed checks on trovelle.in: titles, meta, H1, "cord-sets" typo, leftover "Sverve" copy, schema, blog depth |

## Put it on GitHub (about five minutes)

1. Create an empty repository on GitHub (private or public).
2. From this folder:
   ```bash
   git init -b main
   git add .
   git commit -m "Trovélle SEO dashboard"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
3. Host the dashboard on Vercel (see "Hosting on Vercel" below).
4. Go to **Actions → Refresh dashboard → Run workflow**. The run crawls, saves the data in the repo and asks Vercel to redeploy.

After that it refreshes daily. Each refresh commits `data/live/snapshot.json` and `data/live/history.jsonl`, which is how the trend line builds up.

## Hosting on Vercel

`vercel.json` tells Vercel to run `python3 -m trovelle_seo build` and serve the `site/` folder. The page is generated at build time, so there is no `index.html` in the repo itself; without `vercel.json` Vercel returns 404.

- Import the GitHub repo in Vercel (or run `vercel --prod` from this folder). No framework preset or output directory needs to be set by hand.
- The Vercel build now **crawls and then builds** (`python -m trovelle_seo all`), so every deploy shows fresh data. A blocked competitor never fails the build; it keeps its estimates. The header chip "Competitors measured live N of 11" tells you how many were actually read.
- To refresh daily without touching Vercel: in Vercel, **Settings → Git → Deploy Hooks**, create a hook, and save its URL as the GitHub secret `VERCEL_DEPLOY_HOOK`. The daily GitHub Action then triggers a redeploy after it crawls.
- History (the trend line) is only kept when the GitHub Action commits `data/live/` to the repo. A Vercel-only setup shows the latest crawl but no trend.

## Run it locally

```bash
python -m trovelle_seo build        # build from seed + last crawl, no network
python -m trovelle_seo crawl        # fetch live pages (add site keys, e.g. "aza pernia", to crawl only those)
python -m trovelle_seo all          # crawl, then build
python -m trovelle_seo serve        # http://127.0.0.1:8000
python -m unittest discover -s tests -t .
```

## How "live" works, and what is not live

- **Live:** page titles, meta descriptions, headings, menus, FAQ markup, JSON-LD types, body text and sitemap counts, from the homepage, category pages, blog posts and a sample of 10 product pages per site. The audit, the coverage scores, the phrase list and the history are all recomputed from these on every run.
- **Not live:** *demand* and *difficulty* for each keyword (1–5) are analyst estimates, and competitors' starting coverage scores were set by hand. A competitor the crawl read properly (at least 2 pages and 1,500 words) is scored from the crawl alone, so "not used" means not found on the pages read. Sites that were blocked, not crawled or returned too little text keep the hand-set scores. Trovélle's own score always comes from the crawl once it has run. To go back to "estimate or crawl, whichever is higher", set `scoring.live_overrides_seed` to `false` in `config/settings.json`. If a site blocks the crawler, its hand-set scores stay and its card says "Blocked".
- **Not rank tracking.** The dashboard shows who *targets* a phrase on their pages, not who ranks for it. Connect Google Search Console for that.

To replace estimates with real numbers, export from Semrush, Ahrefs, Keyword Planner or Search Console and save as `data/inputs/metrics.csv` with columns `keyword,volume,kd`. Matching keywords then use real volume and difficulty.

## Configure

- `config/competitors.json` — which sites to crawl. Each site lists a few pinned pages (the homepage at least). The crawler then finds category pages from the site menu and sitemap, blog posts from the blog index and sitemap, and product pages from the category pages. Change how many in `config/settings.json` under `crawl.discover` (`categories`, `posts`, `products`), or per site with `discover.limits`. For a site whose product links are not recognised, add `discover.product_regex` (also `category_regex`, `post_regex`, `sitemap`, `platform`). Add a competitor by copying an entry; `seeded: false` means it has no hand-set scores and fills in after its first crawl. Check IKKIVI's domain: it was not re-verified.
- `data/seed/keywords.csv` — the tracked keywords. `match` (optional) lists exact phrases separated by `|` to avoid loose matching; the `trovelle` and competitor columns are 0/1/2 (none / weak / strong).
- `data/seed/aeo_questions.csv`, `data/seed/content_plan.csv` — the AEO drafts and the content calendar.
- `config/settings.json` — crawl politeness, scoring weights, audit thresholds. The crawler's User-Agent is set from your repo URL in CI (`TROVELLE_SEO_UA`); edit `crawl.user_agent` for local runs.

## Crawling etiquette

The crawler identifies itself, obeys `robots.txt`, waits 2 seconds between requests to the same site, reads about 35 pages per site per run (homepage, up to 12 category pages, the blog index, up to 8 posts and up to 10 product pages), and stores only SEO fields and a short text excerpt. Respect each site's terms of use; if a competitor asks you to stop, remove it from `config/competitors.json`. If `robots.txt` cannot be fetched, the crawler skips that site rather than guessing.

## Before you publish the AEO drafts

Drafts contain yellow `[Add: …]` slots for facts only the Trovélle team knows (return window, delivery times, how designers are selected), and each has a "Check before publishing" note. Do not publish with a slot unfilled.

## Layout

```
trovelle_seo/   crawler (fetch, extract, crawl), matcher and scoring, audit, build, dashboard template
config/         competitors and settings
data/seed/      keywords, AEO questions, content plan, Trovélle baseline snapshot
data/inputs/    optional metrics.csv
data/live/      crawl snapshot and history (written by CI)
tests/          unit tests, including a crawl against a local test server
.github/        daily crawl, data commit and Vercel redeploy
```
