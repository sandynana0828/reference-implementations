# -*- coding: utf-8 -*-
"""Acceptance tests for the dictionary tree query interface.

Covers the three acceptance criteria plus the dicts listing endpoint:
  1. disabled (deprecated) and merged-away tags are NOT returned by default
  2. depth=1 returns only the first level
  3. If-None-Match returns 304 when the dictionary has not changed
"""
import json
import unittest
import urllib.error
import urllib.request

import tree_api
from taxonomy import Taxonomy


class TreeServiceTests(unittest.TestCase):
    """Service-level checks against a freshly seeded instance."""

    def setUp(self):
        self.svc = Taxonomy()
        self.did = self.svc.create_dict("org-1", "industry", "Industry", max_depth=3)
        root = self.svc.create_tag(self.did, "I", "Infotech", direct=True)
        mid = self.svc.create_tag(self.did, "I-01", "Software", parent_id=root, direct=True)
        self.svc.create_tag(self.did, "I-0101", "SaaS", parent_id=mid, direct=True)
        dep = self.svc.create_tag(self.did, "I-0102", "Legacy ASP", parent_id=mid, direct=True)
        self.svc.deprecate(dep, "replaced")
        merged = self.svc.create_tag(self.did, "I-0103", "Middleware", parent_id=mid, direct=True)
        self.svc.merge(merged, mid)

    def codes(self, body):
        def walk(nodes, acc):
            for n in nodes:
                acc.append(n["code"])
                walk(n["children"], acc)
            return acc
        return walk(body["tags"], [])

    def test_default_excludes_deprecated_and_merged(self):
        body = json.loads(self.svc.tree(self.did)["body"])
        codes = self.codes(body)
        self.assertIn("I-0101", codes)
        self.assertNotIn("I-0102", codes, "deprecated tag must be hidden by default")
        self.assertNotIn("I-0103", codes, "merged-away tag must be hidden by default")

    def test_include_deprecated_brings_disabled_back(self):
        body = json.loads(self.svc.tree(self.did, include_deprecated=True)["body"])
        codes = self.codes(body)
        self.assertIn("I-0102", codes)
        self.assertNotIn("I-0103", codes)  # merged stays hidden even with the flag

    def test_node_shape_code_name_level_children_aliases(self):
        body = json.loads(self.svc.tree(self.did)["body"])
        node = body["tags"][0]
        for field in ("code", "name", "level", "children", "aliases"):
            self.assertIn(field, node)
        leaf = node["children"][0]["children"][0]
        self.assertEqual(leaf["code"], "I-0101")
        self.assertEqual(leaf["level"], 3)

    def test_depth_one_returns_only_first_level(self):
        body = json.loads(self.svc.tree(self.did, depth=1)["body"])
        self.assertEqual([t["code"] for t in body["tags"]], ["I"])
        self.assertEqual(body["tags"][0]["children"], [])

    def test_depth_two_returns_two_levels(self):
        body = json.loads(self.svc.tree(self.did, depth=2)["body"])
        self.assertEqual([t["code"] for t in body["tags"]], ["I"])
        self.assertEqual([c["code"] for c in body["tags"][0]["children"]], ["I-01"])
        self.assertEqual(body["tags"][0]["children"][0]["children"], [])

    def test_etag_stable_and_changes_on_update(self):
        e1 = self.svc.tree(self.did)["etag"]
        e2 = self.svc.tree(self.did)["etag"]
        self.assertEqual(e1, e2)
        root_id = self.svc.db.execute("SELECT id FROM taxonomy_tag WHERE code = 'I'").fetchone()["id"]
        self.svc.update_tag(root_id, name="Infotech & Media")
        e3 = self.svc.tree(self.did)["etag"]
        self.assertNotEqual(e1, e3)

    def test_list_dicts_counts_only_live_tags(self):
        dicts = self.svc.list_dicts("org-1")
        self.assertEqual(dicts[0]["code"], "industry")
        # 5 tags created, 1 deprecated, 1 merged -> 3 live
        self.assertEqual(dicts[0]["tag_count"], 3)


class HttpLayerTests(unittest.TestCase):
    """End-to-end against the real stdlib HTTP server (acceptance criteria 1-3)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), tree_api.Handler)  # ephemeral port
        t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        t.start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_dicts_endpoint(self):
        status, headers, body = self.get("/api/taxonomy/dicts")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data[0]["code"], "industry")
        # seeded: I, I-01, I-0101, I-0102 live; deprecated I-0103 + merged I-0104 excluded
        self.assertEqual(data[0]["tag_count"], 4)

    def test_tree_default_hides_disabled_and_merged(self):
        status, headers, body = self.get("/api/taxonomy/dicts/industry/tree")
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertNotIn("Legacy ASP", text)
        self.assertNotIn("I-0104", text, "merged-away tag code must not appear")
        self.assertIn("SaaS", text)

    def test_tree_depth_one(self):
        status, headers, body = self.get("/api/taxonomy/dicts/industry/tree?depth=1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data["tags"]), 1)
        self.assertEqual(data["tags"][0]["children"], [])

    def test_tree_depth_two(self):
        status, headers, body = self.get("/api/taxonomy/dicts/industry/tree?depth=2")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([c["code"] for c in data["tags"][0]["children"]], ["I-01"])
        self.assertEqual(data["tags"][0]["children"][0]["children"], [])

    def test_if_none_match_returns_304_when_unchanged(self):
        status, headers, body = self.get("/api/taxonomy/dicts/industry/tree")
        etag = headers["ETag"]
        status2, headers2, body2 = self.get("/api/taxonomy/dicts/industry/tree",
                                            headers={"If-None-Match": etag})
        self.assertEqual(status2, 304)
        self.assertEqual(body2, b"")
        self.assertEqual(headers2["ETag"], etag)

    def test_200_when_dict_changed_after_etag(self):
        status, headers, _ = self.get("/api/taxonomy/dicts/industry/tree")
        etag = headers["ETag"]
        # mutate through a second service instance sharing the same in-memory db
        tree_api.SERVICE.update_tag(
            tree_api.SERVICE.db.execute("SELECT id FROM taxonomy_tag WHERE code='I'").fetchone()["id"],
            name="Infotech & Media")
        status2, _, _ = self.get("/api/taxonomy/dicts/industry/tree",
                                 headers={"If-None-Match": etag})
        self.assertEqual(status2, 200)

    def test_unknown_dict_404(self):
        status, _, _ = self.get("/api/taxonomy/dicts/nope/tree")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
