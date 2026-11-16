# -*- coding: utf-8 -*-
"""Dictionary & Tag Center — change event publishing via the transactional outbox.

Extends the core service so that every lifecycle transition writes an event row
into `taxonomy_outbox` **inside the same SQL transaction** as the business change:

    taxonomy.tag_activated   tag becomes active (direct create / approve / explicit activate)
    taxonomy.tag_updated     mutable fields changed on an active tag
    taxonomy.tag_deprecated  tag disabled (with optional replacement pointer)
    taxonomy.tag_merged      source tag merged into a target tag

Event payloads are enriched with the dictionary code so consumers do not need a
second lookup. Consumers (matching engine, search index) drain the outbox with a
rowid cursor and mark rows `delivered_at` — see consumer_example.py.
"""
import json

from taxonomy import TaxError, Taxonomy, now


class OutboxTaxonomy(Taxonomy):
    """Core taxonomy service + tag_activated publishing + payload enrichment."""

    # ---- event payload helpers -------------------------------------------

    def _dict_code(self, dict_id: str):
        row = self.db.execute("SELECT code FROM taxonomy_dict WHERE id = ?", (dict_id,)).fetchone()
        return row["code"] if row else None

    def _emit(self, event_type: str, payload: dict):
        """Enrich every payload with the dictionary code, then persist as-is."""
        enriched = dict(payload)
        if "dict_id" in enriched and "dict" not in enriched:
            enriched["dict"] = self._dict_code(enriched["dict_id"])
        enriched.setdefault("occurred_at", now())
        super()._emit(event_type, enriched)

    # ---- activation paths -------------------------------------------------

    def _emit_activated(self, tag_row, role: str):
        self._emit("taxonomy.tag_activated", {
            "dict_id": tag_row["dict_id"],
            "code": tag_row["code"],
            "version": tag_row["version"],
            "change": ["status=active"],
            "summary": "tag became active",
            "operator": role,
        })

    def create_tag(self, dict_id, code, name, role="knowledge_admin", parent_id=None,
                   definition="", aliases=None, sort_order=0, direct=False):
        tid = super().create_tag(dict_id, code, name, role=role, parent_id=parent_id,
                                 definition=definition, aliases=aliases,
                                 sort_order=sort_order, direct=direct)
        if direct:  # direct create skips the proposal workflow -> tag is born active
            self._emit_activated(self._tag(tid), role)
            self._commit()
        return tid

    def activate_tag(self, tag_id: str, role: str = "knowledge_admin") -> dict:
        """Explicit activation: draft -> active, emitting taxonomy.tag_activated."""
        self._audit(role)
        r = self._tag(tag_id)
        if r["status"] == "active":
            raise TaxError(409, "tag already active", {"code": r["code"]})
        if r["status"] == "deprecated":
            raise TaxError(409, "deprecated tag cannot be re-activated; create a new tag")
        version = r["version"] + 1
        self.db.execute("UPDATE taxonomy_tag SET status = 'active', version = ?, updated_at = ?,"
                        " updated_by = ? WHERE id = ?", (version, now(), role, tag_id))
        self._history(tag_id, "activate", {"status": r["status"]}, {"status": "active"}, role, version)
        self._emit_activated(self._tag(tag_id), role)
        self._bump_dict(r["dict_id"])
        self._commit()
        return {"id": tag_id, "code": r["code"], "status": "active", "version": version}

    def approve(self, proposal_id: str, reviewer: str) -> dict:
        """Approval of a create-proposal activates the tag -> emit tag_activated."""
        p = self.db.execute("SELECT * FROM taxonomy_proposal WHERE id = ?", (proposal_id,)).fetchone()
        result = super().approve(proposal_id, reviewer)
        if p["type"] == "create" and result.get("tag_id"):
            self._emit_activated(self._tag(result["tag_id"]), reviewer)
            self._commit()
        return result


# ---- consumer side (reference implementation) -----------------------------

class OutboxDispatcher:
    """Polls taxonomy_outbox with a rowid cursor and fans events out to handlers.

    At-least-once semantics: `delivered_at` is stamped only after every handler
    returns, so a crash between dispatch and stamp re-delivers the event —
    handlers must therefore be idempotent (both sample handlers are).
    """

    def __init__(self, svc: Taxonomy):
        self.svc = svc
        self.last_rowid = 0

    def drain(self, handlers) -> int:
        rows = self.svc.db.execute(
            "SELECT rowid, * FROM taxonomy_outbox"
            " WHERE rowid > ? AND delivered_at IS NULL ORDER BY rowid",
            (self.last_rowid,)).fetchall()
        for row in rows:
            event = {"event_id": row["event_id"], "event_type": row["event_type"],
                     "created_at": row["created_at"],
                     "payload": json.loads(row["payload_json"])}
            for h in handlers:
                h(event)
            self.svc.db.execute("UPDATE taxonomy_outbox SET delivered_at = ? WHERE event_id = ?",
                                (now(), row["event_id"]))
            self.last_rowid = row["rowid"]
        self.svc._commit()
        return len(rows)
