# -*- coding: utf-8 -*-
"""Minimal HTTP surface for the Dictionary & Tag Center (stdlib only, no framework needed).

Production target is FastAPI; these handlers are deliberately thin so the same service class
drops into FastAPI routers unchanged (see README "Porting to FastAPI").
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from taxonomy import TaxError, Taxonomy

SERVICE = Taxonomy()


def seed(svc: Taxonomy):
    did = svc.create_dict("org-1", "industry", "行业分类", max_depth=3)
    svc.register_ref_table("enterprise", "crm_enterprise_tags")
    svc.register_ref_table("demand", "crm_demand_tags")
    svc.register_ref_table("capability", "crm_capability_tags")
    root = svc.create_tag(did, "I", "信息技术", direct=True, definition="国标门类 I")
    mid = svc.create_tag(did, "I-01", "软件", parent_id=root, direct=True)
    a = svc.create_tag(did, "I-0101", "SaaS", parent_id=mid, direct=True, definition="软件即服务")
    b = svc.create_tag(did, "I-0102", "PaaS", parent_id=mid, direct=True, definition="平台即服务")
    svc.add_ref("enterprise", "E-1001", a)
    svc.add_ref("demand", "D-2001", a)
    svc.add_ref("enterprise", "E-1001", b)
    return did, mid, a, b


DICT, MID, SAAS, PAAS = seed(SERVICE)

ROUTES = [
    ("GET", r"^/api/taxonomy/dicts$", lambda m, q, b, role: (200, SERVICE.list_dicts("org-1"))),
    ("GET", r"^/api/taxonomy/dicts/(?P<code>[^/]+)/tree$", lambda m, q, b, role: tree(m, q)),
    ("POST", r"^/api/taxonomy/dicts/(?P<code>[^/]+)/tags$", lambda m, q, b, role: (
        201, {"id": SERVICE.create_tag(DICT, b["code"], b["name"], role=role, parent_id=b.get("parent_id"),
                                       definition=b.get("definition", ""), aliases=b.get("aliases") or [])})),
    ("PATCH", r"^/api/taxonomy/tags/(?P<tid>[^/]+)$", lambda m, q, b, role: (200, SERVICE.update_tag(m["tid"], role=role, **b))),
    ("GET", r"^/api/taxonomy/tags/(?P<tid>[^/]+)/history$", lambda m, q, b, role: (200, SERVICE.history(m["tid"]))),
    ("GET", r"^/api/taxonomy/tags/(?P<tid>[^/]+)/refcount$", lambda m, q, b, role: (200, SERVICE.refcount(m["tid"]))),
    ("POST", r"^/api/taxonomy/tags/(?P<tid>[^/]+)/merge$", lambda m, q, b, role: (200, SERVICE.merge(m["tid"], b["into_id"], role=role))),
    ("POST", r"^/api/taxonomy/tags/(?P<tid>[^/]+)/deprecate$", lambda m, q, b, role: (
        200, SERVICE.deprecate(m["tid"], b.get("reason", ""), b.get("replacement_id"), role=role,
                              migrate=bool(b.get("migrate"))))),
    ("GET", r"^/api/taxonomy/proposals$", lambda m, q, b, role: (200, SERVICE.list_proposals(
        dict_id=q.get("dict_id"), ptype=q.get("type"), source=q.get("source"), status=q.get("status"),
        page=int(q.get("page", 1)), page_size=int(q.get("page_size", 20))))),
    ("POST", r"^/api/taxonomy/proposals$", lambda m, q, b, role: (201, {"id": SERVICE.propose(
        DICT, b["type"], b["content"], b["source"], b.get("proposed_by", role), b.get("evidence"))})),
    ("POST", r"^/api/taxonomy/proposals/batch-approve$", lambda m, q, b, role: (
        200, SERVICE.batch_approve(b["ids"], b.get("reviewer", role)))),
    ("POST", r"^/api/taxonomy/proposals/(?P<pid>[^/]+)/approve$", lambda m, q, b, role: (
        200, SERVICE.approve(m["pid"], b.get("reviewer", role)))),
    ("POST", r"^/api/taxonomy/proposals/(?P<pid>[^/]+)/reject$", lambda m, q, b, role: (
        200, SERVICE.reject(m["pid"], b.get("reviewer", role), b.get("reason", "")))),
    ("GET", r"^/api/public/taxonomy/(?P<code>[^/]+)$", lambda m, q, b, role: public(m.get("logged_in"))),
]


def tree(m, q):
    res = SERVICE.tree(DICT, depth=int(q["depth"]) if q.get("depth") else None,
                       include_deprecated=q.get("include_deprecated") == "true")
    return 200, json.loads(res["body"]), {"ETag": res["etag"]}


def public(logged_in: bool):
    if not logged_in:
        raise TaxError(401, "member login required")
    tree_data = json.loads(SERVICE.tree(DICT)["body"])

    def strip(nodes):
        return [{"code": n["code"], "name": n["name"], "children": strip(n["children"])} for n in nodes]

    return 200, {"dict": tree_data["dict"], "tags": strip(tree_data["tags"])}, {"Cache-Control": "max-age=300"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _dispatch(self, method):
        path, _, query = self.path.partition("?")
        q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        role = self.headers.get("X-Role", "knowledge_admin")
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            mt = re.match(pattern, path)
            if not mt:
                continue
            args = mt.groupdict()
            if path.startswith("/api/public/"):
                args["logged_in"] = bool(self.headers.get("Authorization"))
            try:
                out = fn(args, q, body, role)
                status, payload = out[0], out[1]
                headers = out[2] if len(out) > 2 else {}
            except TaxError as e:
                status, payload, headers = e.code, {"error": e.msg, **e.extra}, {}
            break
        else:
            status, payload, headers = 404, {"error": "no route", "path": path}, {}

        if status == 304:
            self.send_response(304)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get("If-None-Match") and self.path.endswith("/tree"):
            res = SERVICE.tree(DICT)
            if self.headers["If-None-Match"] == res["etag"]:
                self.send_response(304)
                self.send_header("ETag", res["etag"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")


def serve(port=8099):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8099)
