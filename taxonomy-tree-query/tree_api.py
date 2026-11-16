# -*- coding: utf-8 -*-
"""Dictionary tree query interface (stdlib HTTP layer, zero third-party deps).

Routes (production target FastAPI — the service class drops into routers unchanged):

    GET /api/taxonomy/dicts                      -> list dicts with live tag counts
    GET /api/taxonomy/dicts/{code}/tree          -> tag tree for one dict
                                                    ?depth=N        limit returned levels
                                                    ?include_deprecated=true  also return disabled tags
                                                    If-None-Match: <etag>     -> 304 when dict unchanged
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from taxonomy import TaxError, Taxonomy

SERVICE = Taxonomy()


def seed(svc: Taxonomy):
    """Small fictional dataset: one dict, 3 levels, plus a deprecated subtree."""
    did = svc.create_dict("org-1", "industry", "Industry", max_depth=3)
    root = svc.create_tag(did, "I", "Infotech", direct=True)
    mid = svc.create_tag(did, "I-01", "Software", parent_id=root, direct=True)
    svc.create_tag(did, "I-0101", "SaaS", parent_id=mid, direct=True, aliases=["cloud software"])
    svc.create_tag(did, "I-0102", "PaaS", parent_id=mid, direct=True)
    dep = svc.create_tag(did, "I-0103", "Legacy ASP", parent_id=mid, direct=True)
    svc.deprecate(dep, "replaced by SaaS/PaaS")
    merged = svc.create_tag(did, "I-0104", "Middleware", parent_id=mid, direct=True)
    svc.merge(merged, mid)  # merged-away tag must not appear in the default tree
    return did, root, mid


DICT, ROOT, MID = seed(SERVICE)

ROUTES = [
    ("GET", r"^/api/taxonomy/dicts$", lambda m, q: (200, SERVICE.list_dicts("org-1"))),
    ("GET", r"^/api/taxonomy/dicts/(?P<code>[^/]+)/tree$", lambda m, q: tree(m["code"], q)),
]


def tree(dict_code: str, q: dict):
    row = SERVICE.db.execute("SELECT id FROM taxonomy_dict WHERE code = ? AND org_id = 'org-1'",
                             (dict_code,)).fetchone()
    if not row:
        raise TaxError(404, "dict not found", {"dict": dict_code})
    depth = int(q["depth"]) if q.get("depth") else None
    res = SERVICE.tree(row["id"], depth=depth,
                       include_deprecated=q.get("include_deprecated") == "true")
    return 200, json.loads(res["body"]), {"ETag": res["etag"]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        # conditional request on the tree endpoint: dict unchanged -> 304
        if path.endswith("/tree") and self.headers.get("If-None-Match"):
            row = SERVICE.db.execute(
                "SELECT id FROM taxonomy_dict WHERE code = ? AND org_id = 'org-1'",
                (path.rsplit("/", 2)[1],)).fetchone()
            if row:
                etag = SERVICE.tree(row["id"])["etag"]
                if self.headers["If-None-Match"] == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
        for method, pattern, fn in ROUTES:
            if method != "GET":
                continue
            m = re.match(pattern, path)
            if not m:
                continue
            hdr = []
            try:
                status, payload, *hdr = fn(m, q)
            except TaxError as e:
                status, payload = e.code, {"error": e.msg, **e.extra}
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for h in hdr or []:
                for k, v in h.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(404)
        body = json.dumps({"error": "no route", "path": path}).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port=8101):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8101)
