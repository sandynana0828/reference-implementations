# -*- coding: utf-8 -*-
"""Acceptance tests: all four dictionary change events flow through the outbox,
a rolled-back business transaction leaves no events, and the event structure
documentation covers every field."""
import json
import os
import re
import unittest

from outbox_ext import OutboxDispatcher, OutboxTaxonomy
from consumer_example import MatchingEngine, SearchIndex


def build(svc):
    did = svc.create_dict("org-1", "industry", "Industry", max_depth=3)
    root = svc.create_tag(did, "I", "Infotech", direct=True)          # -> tag_activated
    mid = svc.create_tag(did, "I-01", "Software", parent_id=root, direct=True)
    return did, root, mid


class EventsInOutbox(unittest.TestCase):
    def setUp(self):
        self.svc = OutboxTaxonomy()
        self.did, self.root, self.mid = build(self.svc)

    def events(self):
        return [r["event_type"] for r in self.svc.db.execute(
            "SELECT event_type FROM taxonomy_outbox ORDER BY rowid").fetchall()]

    def test_activated_emitted_on_direct_create(self):
        self.assertIn("taxonomy.tag_activated", self.events())

    def test_activated_emitted_on_explicit_activate(self):
        draft = self.svc.create_tag(self.did, "I-02", "Hardware")     # draft, no event
        self.assertEqual(self.events().count("taxonomy.tag_activated"), 2)
        self.svc.activate_tag(draft)
        self.assertEqual(self.events().count("taxonomy.tag_activated"), 3)

    def test_updated_emitted(self):
        self.svc.update_tag(self.mid, name="Software & Cloud")
        self.assertIn("taxonomy.tag_updated", self.events())

    def test_deprecated_emitted(self):
        self.svc.deprecate(self.mid, "superseded by AI tags")
        self.assertIn("taxonomy.tag_deprecated", self.events())

    def test_merged_emitted(self):
        src = self.svc.create_tag(self.did, "I-03", "Middleware", parent_id=self.root, direct=True)
        self.svc.merge(src, self.mid)
        types = self.events()
        self.assertIn("taxonomy.tag_merged", types)  # merge is one event; source status becomes 'merged'

    def test_all_four_types_present_after_full_lifecycle(self):
        draft = self.svc.create_tag(self.did, "I-04", "Data")          # activated
        self.svc.activate_tag(draft)                                   # activated
        self.svc.update_tag(draft, name="Data Platform")               # updated
        self.svc.deprecate(draft, "renamed")                           # deprecated
        other = self.svc.create_tag(self.did, "I-05", "Storage", parent_id=self.root, direct=True)
        self.svc.merge(other, draft)                                   # merged
        self.assertEqual(self.events().count("taxonomy.tag_activated"), 4)
        self.assertIn("taxonomy.tag_updated", self.events())
        self.assertIn("taxonomy.tag_deprecated", self.events())
        self.assertIn("taxonomy.tag_merged", self.events())

    def test_payload_carries_dict_code_version_summary(self):
        self.svc.update_tag(self.mid, name="Software & Cloud")
        row = self.svc.db.execute(
            "SELECT * FROM taxonomy_outbox WHERE event_type = 'taxonomy.tag_updated'").fetchone()
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["dict"], "industry")
        self.assertEqual(payload["dict_id"], self.did)
        self.assertEqual(payload["code"], "I-01")
        self.assertGreaterEqual(payload["version"], 1)
        self.assertTrue(payload["occurred_at"])
        self.assertIn("name", payload["change"])


class RollbackSafety(unittest.TestCase):
    def setUp(self):
        self.svc = OutboxTaxonomy()
        self.did, self.root, self.mid = build(self.svc)

    def test_rollback_leaves_no_events(self):
        n_before = self.svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"]
        self.svc._defer = True                       # business change runs uncommitted
        self.svc.update_tag(self.mid, name="should never land")
        self.svc.deprecate(self.mid, "should never land")
        pending = self.svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"]
        self.assertGreater(pending, n_before)        # written in the same tx...
        self.svc.db.rollback()                       # ...and gone with the rollback
        n_after = self.svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"]
        self.assertEqual(n_after, n_before)
        tag = self.svc.db.execute("SELECT name, status FROM taxonomy_tag WHERE id = ?",
                                  (self.mid,)).fetchone()
        self.assertEqual(tag["name"], "Software")    # business change also reverted
        self.assertEqual(tag["status"], "active")

    def test_fail_injected_commit_discards_events(self):
        svc = OutboxTaxonomy(fail_on_table="crm_enterprise_tags")
        did = svc.create_dict("org-1", "industry", "Industry")
        root = svc.create_tag(did, "I", "Infotech", direct=True)
        mid = svc.create_tag(did, "I-01", "Software", parent_id=root, direct=True)
        svc.register_ref_table("enterprise", "crm_enterprise_tags")
        svc.add_ref("enterprise", "E-1001", mid)
        n_before = svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"]
        with self.assertRaises(RuntimeError):
            svc.merge(mid, root)                     # fault injected mid-merge -> rollback
        n_after = svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"]
        self.assertEqual(n_after, n_before)          # no tag_merged event survived
        src = svc.db.execute("SELECT status FROM taxonomy_tag WHERE code = 'I-01'").fetchone()
        self.assertEqual(src["status"], "active")    # business change also reverted


class Consumers(unittest.TestCase):
    def test_drain_fans_out_and_marks_delivered(self):
        svc = OutboxTaxonomy()
        did, root, mid = build(svc)
        draft = svc.create_tag(did, "I-02", "Hardware")
        svc.activate_tag(draft)
        svc.update_tag(draft, name="Hardware & Devices")
        svc.deprecate(draft, "renamed")
        other = svc.create_tag(did, "I-03", "Middleware", parent_id=root, direct=True)
        svc.merge(other, mid)

        engine, index = MatchingEngine(), SearchIndex()
        d = OutboxDispatcher(svc)
        n = d.drain([engine, index])
        self.assertEqual(n, svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox").fetchone()["c"])
        self.assertEqual(sorted(engine.by_object), ["I", "I-01"])  # I-02 deprecated, I-03 merged into I-01
        self.assertIn("I-01", index.search("industry", "I-"))
        remaining = svc.db.execute(
            "SELECT COUNT(*) c FROM taxonomy_outbox WHERE delivered_at IS NULL").fetchone()["c"]
        self.assertEqual(remaining, 0)
        # replaying drains nothing new (cursor)
        self.assertEqual(d.drain([engine, index]), 0)

    def test_event_doc_covers_every_field(self):
        doc = open(os.path.join(os.path.dirname(__file__), "EVENTS.md"), encoding="utf-8").read()
        for field in ("event_id", "event_type", "created_at", "delivered_at", "payload",
                      "dict", "dict_id", "code", "version", "occurred_at",
                      "taxonomy.tag_activated", "taxonomy.tag_updated",
                      "taxonomy.tag_deprecated", "taxonomy.tag_merged"):
            self.assertIn(field, doc, f"EVENTS.md missing field {field}")
        for et in re.findall(r"taxonomy\.tag_[a-z]+", doc):
            self.assertIn(et, ("taxonomy.tag_activated", "taxonomy.tag_updated",
                               "taxonomy.tag_deprecated", "taxonomy.tag_merged"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
