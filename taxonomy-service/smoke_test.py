# -*- coding: utf-8 -*-
"""HTTP-level smoke test: prints method, path, status for every acceptance path of the ticket series."""
import json
import threading
import time
import urllib.error
import urllib.request

import api as A

PORT = 8107
threading.Thread(target=A.serve, args=(PORT,), daemon=True).start()
time.sleep(0.6)
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, role="knowledge_admin", etag=None, auth=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Role", role)
    if etag:
        req.add_header("If-None-Match", etag)
    if auth:
        req.add_header("Authorization", "Bearer demo")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload, dict(e.headers)


def show(label, method, path, res):
    st, body, hdr = res
    extra = ""
    if "ETag" in hdr:
        extra = " etag=" + hdr["ETag"]
    if "Cache-Control" in hdr:
        extra += " cache=" + hdr["Cache-Control"]
    print(f"{label:<18} {method:<5} {path:<44} -> {st}{extra}", flush=True)
    return st, body, hdr


# 1 dicts / tree / ETag
st, body, hdr = show("dicts", "GET", "/api/taxonomy/dicts", call("GET", "/api/taxonomy/dicts"))
show("tree", "GET", "/api/taxonomy/dicts/industry/tree", call("GET", "/api/taxonomy/dicts/industry/tree"))
_, tree_body, tree_hdr = call("GET", "/api/taxonomy/dicts/industry/tree")
show("tree/304", "GET", "/api/taxonomy/dicts/industry/tree",
     call("GET", "/api/taxonomy/dicts/industry/tree", etag=tree_hdr["ETag"]))

# 2 create tag (RBAC)
st, body, _ = show("tag create", "POST", "/api/taxonomy/dicts/industry/tags",
                   call("POST", "/api/taxonomy/dicts/industry/tags", {"code": "I-03", "name": "人工智能", "definition": "AI 相关"}))
tid = body.get("id")
show("tag create/403", "POST", "/api/taxonomy/dicts/industry/tags",
     call("POST", "/api/taxonomy/dicts/industry/tags", {"code": "I-04", "name": "x"}, role="front_desk"))
show("tag patch", "PATCH", f"/api/taxonomy/tags/{tid}", call("PATCH", f"/api/taxonomy/tags/{tid}", {"name": "人工智能应用"}))
show("tag history", "GET", f"/api/taxonomy/tags/{tid}/history", call("GET", f"/api/taxonomy/tags/{tid}/history"))
show("tag refcount", "GET", f"/api/taxonomy/tags/{A.SAAS}/refcount", call("GET", f"/api/taxonomy/tags/{A.SAAS}/refcount"))

# 3 proposals
show("proposal ai/no-ev", "POST", "/api/taxonomy/proposals",
     call("POST", "/api/taxonomy/proposals", {"type": "create", "source": "ai", "content": {"code": "N", "name": "无证据"}, "proposed_by": "agent-7"}))
st, p1, _ = show("proposal ai", "POST", "/api/taxonomy/proposals",
                 call("POST", "/api/taxonomy/proposals", {"type": "create", "source": "ai", "proposed_by": "agent-7",
                                                          "content": {"code": "I-05", "name": "量子计算"},
                                                          "evidence": "会员 E-1001 经营范围含量子"}))
st, p2, _ = call("POST", "/api/taxonomy/proposals", {"type": "create", "source": "user", "proposed_by": "u-5",
                                                    "content": {"code": "I-06", "name": "卫星互联网"}})
st, p3, _ = call("POST", "/api/taxonomy/proposals", {"type": "create", "source": "user", "proposed_by": "u-5",
                                                    "content": {"code": "I-05", "name": "重复码"}})
show("proposal self-ok", "POST", f"/api/taxonomy/proposals/{p2['id']}/approve",
     call("POST", f"/api/taxonomy/proposals/{p2['id']}/approve", {"reviewer": "u-5"}))
show("proposal batch", "POST", "/api/taxonomy/proposals/batch-approve",
     call("POST", "/api/taxonomy/proposals/batch-approve", {"ids": [p1["id"], p2["id"], p3["id"]], "reviewer": "u-9"}))
show("proposal list", "GET", "/api/taxonomy/proposals?status=pending", call("GET", "/api/taxonomy/proposals?status=pending"))

# 4 merge / deprecate
st, body, _ = show("merge", "POST", f"/api/taxonomy/tags/{A.SAAS}/merge", call("POST", f"/api/taxonomy/tags/{A.SAAS}/merge", {"into_id": A.PAAS}))
print("                   merge payload ->", json.dumps({k: body[k] for k in ("moved", "dedup", "per_type")}, ensure_ascii=False), flush=True)
show("deprecate/409", "POST", f"/api/taxonomy/tags/{A.MID}/deprecate",
     call("POST", f"/api/taxonomy/tags/{A.MID}/deprecate", {"reason": "测试子标签拦截"}))

# 5 public read-only
show("public/401", "GET", "/api/public/taxonomy/industry", call("GET", "/api/public/taxonomy/industry"))
st, body, hdr = show("public/200", "GET", "/api/public/taxonomy/industry", call("GET", "/api/public/taxonomy/industry", auth=True))
print("                   public payload keys ->", list(body["tags"][0].keys()), "(definition/statistics stripped)", flush=True)
