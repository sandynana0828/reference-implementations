# -*- coding: utf-8 -*-
"""Reference consumers for dictionary change events: matching engine + search index.

Both consumers are idempotent (safe under the dispatcher's at-least-once semantics)
and both are in-memory views built purely from outbox events — demonstrating that
the payload carries everything needed to stay in sync.
"""
from collections import defaultdict


class MatchingEngine:
    """Business-opportunity matching view: object_type -> set(tag codes)."""

    def __init__(self):
        self.by_object = defaultdict(set)

    def __call__(self, event):
        p = event["payload"]
        et = event["event_type"]
        key = ("tag", p["dict"], p["code"])
        if et == "taxonomy.tag_activated":
            self.by_object[key[2]].add(p["dict"])
        elif et == "taxonomy.tag_updated":
            # rename/alias changes do not affect membership; nothing to rebuild
            pass
        elif et == "taxonomy.tag_deprecated":
            self.by_object.pop(p["code"], None)
        elif et == "taxonomy.tag_merged":
            src, into = p["code"], p["into_code"]
            self.by_object.setdefault(into, set())
            self.by_object[into] |= self.by_object.pop(src, set())


class SearchIndex:
    """Search view: dict -> code -> name/aliases lookup."""

    def __init__(self):
        self.terms = defaultdict(dict)

    def __call__(self, event):
        p = event["payload"]
        if event["event_type"] == "taxonomy.tag_deprecated":
            self.terms[p["dict"]].pop(p["code"], None)
        else:
            self.terms[p["dict"]][p["code"]] = {
                "version": p["version"],
                "merged_into": p.get("into_code"),
            }

    def search(self, dict_code: str, prefix: str):
        return sorted(c for c in self.terms.get(dict_code, {}) if c.startswith(prefix))


if __name__ == "__main__":
    from outbox_ext import OutboxDispatcher, OutboxTaxonomy

    svc = OutboxTaxonomy()
    did = svc.create_dict("org-1", "industry", "Industry", max_depth=3)
    root = svc.create_tag(did, "I", "Infotech", direct=True)
    mid = svc.create_tag(did, "I-01", "Software", parent_id=root, direct=True)
    draft = svc.create_tag(did, "I-02", "Hardware")
    svc.activate_tag(draft)                                # tag_activated
    svc.update_tag(draft, name="Hardware & Devices")       # tag_updated
    svc.deprecate(draft, "renamed")                        # tag_deprecated
    other = svc.create_tag(did, "I-03", "Middleware", parent_id=root, direct=True)
    svc.merge(other, mid)                                  # tag_merged (+ tag_deprecated)

    engine, index = MatchingEngine(), SearchIndex()
    n = OutboxDispatcher(svc).drain([engine, index])
    types = [r["event_type"] for r in svc.db.execute(
        "SELECT event_type FROM taxonomy_outbox ORDER BY rowid").fetchall()]
    print(f"outbox rows: {n}")
    for t in types:
        print(" -", t)
    print("matching engine keys:", sorted(engine.by_object))
    print("search index I-*:", index.search("industry", "I-"))
    pending = svc.db.execute("SELECT COUNT(*) c FROM taxonomy_outbox WHERE delivered_at IS NULL").fetchone()["c"]
    print("undelivered:", pending)
