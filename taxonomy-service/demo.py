# -*- coding: utf-8 -*-
"""End-to-end walkthrough of the taxonomy service. Prints real numbers, no mocks inside."""
import json

from taxonomy import TaxError, Taxonomy


def line(tag, *args):
    print(tag, *args, flush=True)


t = Taxonomy()
did = t.create_dict("org-1", "industry", "行业分类", max_depth=3)
t.register_ref_table("enterprise", "crm_enterprise_tags")
t.register_ref_table("demand", "crm_demand_tags")
t.register_ref_table("capability", "crm_capability_tags")

# ---- hierarchy, draft vs active ----
I = t.create_tag(did, "I", "信息技术", direct=True, definition="国标门类 I")
I01 = t.create_tag(did, "I-01", "软件", parent_id=I, direct=True, definition="国标大类 65")
I02 = t.create_tag(did, "I-02", "互联网", parent_id=I, direct=True, definition="国标大类 64")
saas = t.create_tag(did, "I-0101", "SaaS", parent_id=I01, direct=True, definition="软件即服务")
paas = t.create_tag(did, "I-0102", "PaaS", parent_id=I01, direct=True, definition="平台即服务")
cloud = t.create_tag(did, "I-0201", "云服务", parent_id=I02, direct=True, definition="公有云/私有云")
draft = t.create_tag(did, "I-0103", "低代码", parent_id=I01)  # stays draft on purpose
def flat(nodes):
    for n in nodes:
        yield n["code"]
        yield from flat(n["children"])


tree_codes = sorted(flat(json.loads(t.tree(did)["body"])["tags"]))
line("[1] active tags in tree:", tree_codes, "| draft tag", "I-0103", "hidden:", "I-0103" not in json.dumps(t.tree(did)["body"]))

# ---- refs ----
for oid in ("E-1001", "E-1002", "E-1003"):
    t.add_ref("enterprise", oid, saas)
t.add_ref("demand", "D-2001", saas)
t.add_ref("capability", "C-3001", saas)
t.add_ref("enterprise", "E-1001", paas)   # E-1001 already carries the merge target -> dedup case
line("[2] refcount SaaS:", t.refcount(saas), "| PaaS:", t.refcount(paas)["total"])

# ---- merge ----
res = t.merge(saas, paas)
line("[3] merge SaaS->PaaS:", json.dumps({k: res[k] for k in ("moved", "dedup", "per_type", "target_version")}, ensure_ascii=False))
line("    source ->", dict(t.db.execute("SELECT status FROM taxonomy_tag WHERE id=?", (saas,)).fetchone()),
     "merged_into set:", bool(t.db.execute("SELECT merged_into_id FROM taxonomy_tag WHERE id=?", (saas,)).fetchone()[0]),
     "| target aliases:", res["target_aliases"], "| events:", [r[0] for r in t.db.execute("SELECT event_type FROM taxonomy_outbox")])
line("    refs after merge  src:", t.refcount(saas)["total"], " tgt:", t.refcount(paas)["total"])

# ---- rollback under injected fault ----
t2 = Taxonomy()
d2 = t2.create_dict("org-1", "industry", "行业分类")
t2.register_ref_table("enterprise", "crm_enterprise_tags")
t2.register_ref_table("demand", "crm_demand_tags")
a = t2.create_tag(d2, "A", "A", direct=True)
b = t2.create_tag(d2, "B", "B", direct=True)
t2.add_ref("enterprise", "E-1", a)
t2.add_ref("demand", "D-1", a)
t2.fail_on_table = "crm_demand_tags"     # 2nd registry table raises mid-transaction
try:
    t2.merge(a, b)
    line("[4] rollback: UNEXPECTED success")
except RuntimeError as e:
    line("[4] fault injected:", e)
    line("    source status:", t2.db.execute("SELECT status FROM taxonomy_tag WHERE id=?", (a,)).fetchone()[0],
         "| refs src/tgt:", t2.refcount(a)["total"], "/", t2.refcount(b)["total"],
         "| outbox rows:", t2.db.execute("SELECT COUNT(*) FROM taxonomy_outbox").fetchone()[0])
t2.fail_on_table = None
line("    retry after fault cleared: moved", t2.merge(a, b)["moved"])

# ---- deprecate ----
t.add_ref("enterprise", "E-2001", cloud)
t.add_ref("demand", "D-2002", cloud)
line("[5] deprecate 云服务 -> PaaS:",
     json.dumps(t.deprecate(cloud, "合并口径统一", replacement_id=paas, migrate=True), ensure_ascii=False))
try:
    t.deprecate(I01, "测试子标签拦截")
except TaxError as e:
    line("    children block:", e.code, [c["code"] for c in e.extra["children"]])
line("    draft tag refcount:", t.deprecate(draft, "未启用")["refcount_before"])

# ---- proposals ----
p_ai = t.propose(did, "create", {"code": "I-03", "name": "人工智能"}, "ai", "agent-7",
                 evidence="会员 E-1002 经营范围为「人工智能应用软件开发」")
p_user = t.propose(did, "create", {"code": "I-04", "name": "区块链"}, "user", "u-5")
p_dup = t.propose(did, "create", {"code": "I-01", "name": "软件"}, "user", "u-5")
line("[6] queue total:", t.list_proposals()["total"], "| filtered(ai,pending):",
     t.list_proposals(source="ai", status="pending")["total"])
try:
    t.propose(did, "create", {"code": "I-05", "name": "无证据"}, "ai", "agent-7")
except TaxError as e:
    line("    ai w/o evidence:", e.code, e.msg)
line("    batch-approve:", json.dumps(t.batch_approve([p_ai, p_user, p_dup], "u-9"), ensure_ascii=False))
p_self = t.propose(did, "create", {"code": "I-06", "name": "量子计算"}, "user", "u-9")
try:
    t.approve(p_self, "u-9")
except TaxError as e:
    line("    self-review:", e.code, e.msg)

# ---- version + history ----
t.update_tag(paas, definition="平台即服务(PaaS), 含容器与中间件")
h = t.history(paas)
line("[7] history(PaaS):", [(x["version"], x["action"]) for x in h],
     "| v1 name:", t.snapshot_at(paas, 1)["name"], "| v2 aliases:", t.snapshot_at(paas, 2)["aliases"])

# ---- tree + ETag ----
r1 = t.tree(did)
r2 = t.tree(did)
shallow = json.loads(t.tree(did, depth=1)["body"])
line("[8] tree:", r1["count"], "nodes, etag", r1["etag"], "| stable:", r1["etag"] == r2["etag"],
     "| depth=1 roots:", len(shallow["tags"]), "children cut:", all(not n["children"] for n in shallow["tags"]))
line("    dicts:", t.list_dicts("org-1"))

# ---- import on a fresh dict ----
imp = t.create_dict("org-1", "demand_type", "需求类型", max_depth=3)
t.create_tag(imp, "P", "采购", direct=True, definition="采购需求")
rows = [
    {"code": "P", "name": "采购", "definition": "采购需求（含工程与货物）", "aliases": ["采买"]},
    {"code": "P-01", "name": "设备采购", "parent_code": "P", "definition": "硬件设备"},
    {"code": "P-02", "name": "服务采购", "parent_code": "P", "definition": "外包服务"},
    {"code": "P-03", "name": "", "parent_code": "P"},
    {"code": "P-04", "name": "空父", "parent_code": "NONE"},
    {"code": "P-05", "name": "重复码"},
    {"code": "P-05", "name": "重复码2"},
]
plan = t.import_dry_run(imp, rows)
line("[9] dry-run: rows=%d added=%d updated=%d conflicts=%d errors=%d"
     % (plan["rows"], len(plan["added"]), len(plan["updated"]), len(plan["conflicts"]), len(plan["errors"])))
line("    errors:", [(e["row"], e["field"], e["reason"]) for e in plan["errors"]])
line("    added:", [x["code"] for x in plan["added"]])
good, seen_codes = [], set()
for r in rows:
    if not r["name"] or r.get("parent_code") == "NONE" or r["code"] in seen_codes:
        continue
    seen_codes.add(r["code"])
    good.append(r)
line("    apply#1:", t.import_apply(imp, good, init_mode=True, role="system_admin"))
line("    apply#2 (same file):", t.import_apply(imp, good, init_mode=True, role="system_admin"))
line("    tags now:", sorted(r[0] for r in t.db.execute("SELECT code FROM taxonomy_tag WHERE dict_id=? AND deleted_at IS NULL", (imp,))))
