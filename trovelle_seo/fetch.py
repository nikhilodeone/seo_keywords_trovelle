"""Polite HTTP fetching: identifiable user agent, robots.txt, per-host delay, one retry."""
from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from urllib.parse import urlparse


class Fetcher:
    def __init__(self, user_agent: str, delay: float = 2.0, timeout: float = 20.0,
                 respect_robots: bool = True):
        self.ua = user_agent
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_why: dict[str, str] = {}

    # -- robots ---------------------------------------------------------------
    def _robots_for(self, url: str):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        status, body, _ = self._get(origin + "/robots.txt", is_robots=True)
        if status == 200:
            rp.parse(body.splitlines())
        elif 400 <= status < 500:
            rp = None          # RFC 9309: robots.txt unavailable (4xx) means no restrictions
        else:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(["User-agent: *", "Disallow: /"])   # 5xx / network failure: be cautious
            self._robots_why[origin] = (f"robots.txt unreachable ({body[:80]})" if status == 0
                                        else f"robots.txt returned HTTP {status}; skipped to be safe")
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self.ua, url)

    # -- http -----------------------------------------------------------------
    def _wait(self, host: str):
        gap = time.monotonic() - self._last.get(host, 0.0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    def _get(self, url: str, is_robots: bool = False):
        host = urlparse(url).netloc
        for attempt in (1, 2):
            self._wait(host)
            req = urllib.request.Request(url, headers={
                "User-Agent": self.ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en-IN,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read(5_000_000)
                    enc = (r.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip":
                        raw = gzip.decompress(raw)
                    elif enc == "deflate":
                        raw = zlib.decompress(raw)
                    charset = r.headers.get_content_charset() or "utf-8"
                    return r.status, raw.decode(charset, errors="replace"), r.geturl()
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt == 1:
                    time.sleep(5)
                    continue
                return e.code, "", url
            except Exception as e:  # DNS, TLS, timeout
                if attempt == 1:
                    time.sleep(2)
                    continue
                return 0, f"{type(e).__name__}: {e}", url
        return 0, "unreachable", url

    def get(self, url: str):
        """Return (status, body, final_url, note). status 0 = network error, -1 = blocked by robots."""
        if not self.allowed(url):
            p = urlparse(url)
            why = self._robots_why.get(f"{p.scheme}://{p.netloc}", "disallowed by robots.txt")
            return -1, "", url, why
        status, body, final = self._get(url)
        note = body if status == 0 else ""
        return status, ("" if status == 0 else body), final, note
