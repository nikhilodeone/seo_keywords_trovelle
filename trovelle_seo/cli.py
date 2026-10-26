"""Command line: python -m trovelle_seo crawl|build|all|serve"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trovelle_seo", description="Trovélle SEO & AEO dashboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="fetch Trovélle and competitor pages into data/live/snapshot.json")
    c.add_argument("sites", nargs="*", help="optional site keys, e.g. aza pernia (others keep their last data)")
    sub.add_parser("build", help="score keywords and write site/index.html from the latest data")
    a = sub.add_parser("all", help="crawl, then build")
    a.add_argument("sites", nargs="*")
    s = sub.add_parser("serve", help="serve site/ locally")
    s.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.cmd in ("crawl", "all"):
        from . import crawl
        crawl.run(ROOT, only=args.sites or None)
    if args.cmd in ("build", "all"):
        from . import build
        build.run(ROOT)
    if args.cmd == "serve":
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "site"))
        with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
            print(f"serving http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
