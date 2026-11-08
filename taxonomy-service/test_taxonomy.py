# -*- coding: utf-8 -*-
"""Acceptance tests for the Dictionary & Tag Center core, mapped 1:1 to ticket scope items."""
import json
import unittest

from taxonomy import TaxError, Taxonomy


def build(t=None, fail_on_table=None):
    t = t or Taxonomy(fail_on_table=fail_on_table)
    did = t.create_dict("org-1", "industry", "行业分类", max_depth=3)
    t.register_ref_table("enterprise", "crm_enterprise_tags")
    t.register_ref_table("demand", "crm_demand_tags")
    t.register_ref_table("capability", "crm_capability_tags")
    return t, did


def seed(t, did):
    """industry > software > saas, plus a sibling used as merge target."""
    l1 = t.create_tag(did, "I", "信息技术", direct=True, definition="国标门类 I")
    l2 = t.create_tag(did, "I-01", "软件", parent_id=l1, direct=True, definition="国标大类")
    saas = t.create_tag(did, "I-0101", "SaaS", parent_id=l2, direct=True, definition="软件即服务")
    cloud = t.create_tag(did, "I-0102", "云服务", parent_id=l2, direct=True, definition="云计算服务")
    return l1, l2, saas, cloud


class TestModel(unittest.TestCase):
    def test_dict_code_unique(self):
        t, did = build()
        with self.assertRaises(TaxError) as cm:
            t.create_dict("org-1", "industry", "dup")
        self.assertEqual(cm.exception.code, 409)

    def test_max_depth_enforced(self):
        t = Taxonomy()
        did = t.create_dict("org-1", "d", "浅字典", max_depth=2)
        a = t.create_tag(did, "A", "一级", direct=True)
        b = t.create_tag(did, "B", "二级", parent_id=a, direct=True)
        with self.assertRaises(TaxError) as cm:
            t.create_tag(did, "C", "三级", parent_id=b)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("max_depth", cm.exception.msg)

    def test_tag_code_unique_within_dict(self):
        t, did = build()
        t.create_tag(did, "I", "信息技术", direct=True)
        with self.assertRaises(TaxError) as cm:
            t.create_tag(did, "I", "另一个")
        self.assertEqual(cm.exception.code, 409)

    def test_soft_delete_frees_code(self):
        t, did = build()
        a = t.create_tag(did, "I", "信息技术")
        t.delete_tag(a)
        b = t.create_tag(did, "I", "信息技术v2")
        self.assertNotEqual(a, b)


class TestCrudAndHistory(unittest.TestCase):
    def test_write_permission(self):
        t, did = build()
        with self.assertRaises(TaxError) as cm:
            t.create_tag(did, "I", "信息技术", role="front_desk")
        self.assertEqual(cm.exception.code, 403)

    def test_delete_only_draft(self):
        t, did = build()
        a = t.create_tag(did, "I", "信息技术", direct=True)
        with self.assertRaises(TaxError) as cm:
            t.delete_tag(a)
        self.assertEqual(cm.exception.code, 400)

    def test_version_and_snapshot(self):
        t, did = build()
        a = t.create_tag(did, "I", "信息技术", direct=True)
        self.assertEqual(t.snapshot_at(a, 1)["name"], "信息技术")
        t.update_tag(a, name="信息技术服务")
        t.update_tag(a, definition="门类 I")
        h = t.history(a)
        self.assertEqual(h[0]["version"], 3)
        self.assertEqual(len(h), 3)  # create + 2 updates
        self.assertEqual(t.snapshot_at(a, 2)["name"], "信息技术服务")
        self.assertEqual(t.snapshot_at(a, 1)["name"], "信息技术")

    def test_parent_change_recomputes_path_and_blocks_cycle(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        did2 = t.create_dict("org-1", "d2", "另一个字典")
        other = t.create_tag(did2, "X", "外部", direct=True)
        with self.assertRaises(TaxError):
            t.update_tag(saas, parent_id=other)
        row = t.db.execute("SELECT path, level FROM taxonomy_tag WHERE id = ?", (saas,)).fetchone()
        self.assertEqual((row["path"], row["level"]), ("I/I-01/I-0101", 3))
        with self.assertRaises(TaxError) as cm:
            t.update_tag(l2, parent_id=saas)
        self.assertEqual(cm.exception.code, 400)


class TestMerge(unittest.TestCase):
    def test_merge_migrates_and_dedups(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        t.add_ref("enterprise", "e1", saas)
        t.add_ref("enterprise", "e2", saas)
        t.add_ref("demand", "d1", saas)
        t.add_ref("enterprise", "e1", cloud)          # already on target -> dedup on merge
        res = t.merge(saas, cloud)
        self.assertEqual(res["moved"], 3)
        self.assertEqual(res["dedup"], 1)
        self.assertEqual(res["per_type"], {"enterprise": 2, "demand": 1})
        self.assertEqual(t.refcount(saas)["total"], 0)
        self.assertEqual(t.refcount(cloud)["total"], 3)
        src = t.db.execute("SELECT status, merged_into_id FROM taxonomy_tag WHERE id = ?", (saas,)).fetchone()
        self.assertEqual(src["status"], "merged")
        self.assertEqual(src["merged_into_id"], cloud)
        aliases = json.loads(t.db.execute("SELECT aliases FROM taxonomy_tag WHERE id = ?", (cloud,)).fetchone()[0])
        self.assertIn("SaaS", aliases)
        ev = t.db.execute("SELECT event_type, payload_json FROM taxonomy_outbox WHERE event_type='taxonomy.tag_merged'").fetchone()
        self.assertIsNotNone(ev)
        self.assertEqual(json.loads(ev["payload_json"])["moved"], 3)

    def test_merge_cross_dict_rejected(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        did2 = t.create_dict("org-1", "d2", "另一个字典")
        other = t.create_tag(did2, "X", "外部", direct=True)
        with self.assertRaises(TaxError) as cm:
            t.merge(saas, other)
        self.assertEqual(cm.exception.code, 400)

    def test_merge_rollback_is_atomic(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        t.add_ref("enterprise", "e1", saas)
        t.add_ref("demand", "d1", saas)
        t.fail_on_table = "crm_demand_tags"           # second registry entry blows up
        with self.assertRaises(RuntimeError):
            t.merge(saas, cloud)
        src = t.db.execute("SELECT status, merged_into_id FROM taxonomy_tag WHERE id = ?", (saas,)).fetchone()
        self.assertEqual(src["status"], "active")
        self.assertIsNone(src["merged_into_id"])
        self.assertEqual(t.refcount(saas)["total"], 2)   # nothing lost
        self.assertEqual(t.refcount(cloud)["total"], 0)  # nothing leaked
        self.assertEqual(t.db.execute("SELECT COUNT(*) FROM taxonomy_outbox").fetchone()[0], 0)
        t.fail_on_table = None
        res = t.merge(saas, cloud)                        # retry succeeds after the fault clears
        self.assertEqual(res["moved"], 2)


class TestDeprecate(unittest.TestCase):
    def test_deprecate_with_children_blocked(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        with self.assertRaises(TaxError) as cm:
            t.deprecate(l2, "过期")
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(len(cm.exception.extra["children"]), 2)

    def test_deprecate_counts_and_migrates(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        t.add_ref("enterprise", "e1", saas)
        t.add_ref("capability", "c1", saas)
        res = t.deprecate(saas, "并入云服务", replacement_id=cloud, migrate=True)
        self.assertEqual(res["refcount_before"], 2)
        self.assertEqual(res["by_type"], {"enterprise": 1, "capability": 1})
        self.assertEqual(res["migrated"], 2)
        self.assertEqual(t.refcount(cloud)["total"], 2)
        with self.assertRaises(TaxError):   # deprecated tag cannot take new refs
            t.add_ref("enterprise", "e9", saas)

    def test_deprecate_requires_reason(self):
        t, did = build()
        l1, l2, saas, cloud = seed(t, did)
        with self.assertRaises(TaxError) as cm:
            t.deprecate(saas, "  ")
        self.assertEqual(cm.exception.code, 400)


class TestProposal(unittest.TestCase):
    def test_ai_needs_evidence(self):
        t, did = build()
        with self.assertRaises(TaxError) as cm:
            t.propose(did, "create", {"code": "N", "name": "新能源"}, "ai", "agent-7")
        self.assertEqual(cm.exception.code, 422)
        pid = t.propose(did, "create", {"code": "N", "name": "新能源"}, "ai", "agent-7",
                        evidence="会员 A 的经营范围含光伏组件")
        self.assertTrue(pid)

    def test_self_review_blocked(self):
        t, did = build()
        pid = t.propose(did, "create", {"code": "N", "name": "新能源"}, "user", "u-1", evidence=None)
        with self.assertRaises(TaxError) as cm:
            t.approve(pid, "u-1")
        self.assertEqual(cm.exception.code, 403)
        out = t.approve(pid, "u-2")
        self.assertEqual(out["status"], "approved")
        tag = t.db.execute("SELECT status FROM taxonomy_tag WHERE id = ?", (out["tag_id"],)).fetchone()
        self.assertEqual(tag["status"], "active")

    def test_reject_reason_required(self):
        t, did = build()
        pid = t.propose(did, "create", {"code": "N", "name": "新能源"}, "user", "u-1")
        with self.assertRaises(TaxError):
            t.reject(pid, "u-2", "")
        self.assertEqual(t.reject(pid, "u-2", "与已有标签重复")["status"], "rejected")

    def test_batch_approve_isolation(self):
        t, did = build()
        good1 = t.propose(did, "create", {"code": "N1", "name": "新能源"}, "user", "u-1")
        bad_self = t.propose(did, "create", {"code": "N2", "name": "储能"}, "user", "u-2")
        good2 = t.propose(did, "create", {"code": "N3", "name": "氢能"}, "user", "u-1")
        dup = t.propose(did, "create", {"code": "N1", "name": "重复码"}, "user", "u-1")
        res = t.batch_approve([good1, bad_self, good2, dup], "u-2")
        self.assertEqual(res["ok"], 2)
        self.assertEqual(res["failed"], 2)
        statuses = {r["id"]: r["ok"] for r in res["results"]}
        self.assertTrue(statuses[good1] and statuses[good2])
        self.assertFalse(statuses[bad_self] or statuses[dup])

    def test_batch_limit_50(self):
        t, did = build()
        with self.assertRaises(TaxError) as cm:
            t.batch_approve([str(i) for i in range(51)], "u-2")
        self.assertEqual(cm.exception.code, 400)


class TestTreeAndImport(unittest.TestCase):
    def test_tree_etag_and_depth(self):
        t, did = build()
        seed(t, did)
        r1 = t.tree(did)
        self.assertEqual(r1["count"], 4)
        self.assertTrue(r1["etag"].startswith('W/"'))
        self.assertEqual(t.tree(did)["etag"], r1["etag"])          # stable when nothing changed
        t.create_tag(did, "I-0103", "PaaS", parent_id=t.db.execute(
            "SELECT id FROM taxonomy_tag WHERE code='I-01'").fetchone()[0], direct=True)
        self.assertNotEqual(t.tree(did)["etag"], r1["etag"])       # changes after activation
        shallow = json.loads(t.tree(did, depth=1)["body"])
        self.assertTrue(all(not n["children"] for n in shallow["tags"]))
        self.assertEqual(len(t.list_dicts("org-1")), 1)
        self.assertEqual(t.list_dicts("org-1")[0]["tag_count"], 5)

    def test_import_dry_run_and_idempotency(self):
        t, did = build()
        t.create_tag(did, "I", "信息技术", direct=True)
        rows = [
            {"code": "I", "name": "信息技术服务", "parent_code": "", "definition": "改名"},
            {"code": "I-01", "name": "软件", "parent_code": "I", "aliases": ["软件业"]},
            {"code": "I-02", "name": "", "parent_code": "I"},
            {"code": "I-03", "name": "空父", "parent_code": "NOPE"},
        ]
        plan = t.import_dry_run(did, rows)
        self.assertEqual(len(plan["added"]), 1)
        self.assertEqual(len(plan["conflicts"]), 1)
        self.assertEqual(len(plan["errors"]), 2)
        fixed = [r for r in rows if r["name"] and r.get("parent_code") != "NOPE"]
        first = t.import_apply(did, fixed, init_mode=True, role="system_admin")
        self.assertEqual((first["created"], first["updated"]), (1, 1))  # rename = conflict, needs review
        second = t.import_apply(did, fixed, init_mode=True, role="system_admin")
        self.assertEqual((second["created"], second["updated"]), (0, 0))   # idempotent re-run
        with self.assertRaises(TaxError) as cm:
            t.import_apply(did, fixed, init_mode=True, role="knowledge_admin")
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
