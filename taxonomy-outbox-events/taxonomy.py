# -*- coding: utf-8 -*-
"""Dictionary & Tag Center core (ticket series ..025) - dependency-free reference service.

Runs on Python 3.11 stdlib only (sqlite3). Every write goes through one transaction;
business-object references are migrated through a registry so merging never touches
hard-coded table names.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS taxonomy_dict (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, site_id TEXT,
  code TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
  allow_multi INTEGER NOT NULL DEFAULT 0, max_depth INTEGER NOT NULL DEFAULT 3,
  owner_role TEXT NOT NULL DEFAULT 'knowledge_admin',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  deleted_at TEXT,
  UNIQUE (org_id, code)
);

CREATE TABLE IF NOT EXISTS taxonomy_tag (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, site_id TEXT,
  dict_id TEXT NOT NULL REFERENCES taxonomy_dict(id),
  code TEXT NOT NULL, name TEXT NOT NULL, parent_id TEXT,
  path TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
  aliases TEXT NOT NULL DEFAULT '[]', definition TEXT,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','pending_review','active','deprecated','merged')),
  merged_into_id TEXT, sort_order INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  created_by TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by TEXT,
  deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_code ON taxonomy_tag(dict_id, code) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS taxonomy_tag_history (
  id TEXT PRIMARY KEY, tag_id TEXT NOT NULL REFERENCES taxonomy_tag(id),
  version INTEGER NOT NULL, action TEXT NOT NULL,
  before_json TEXT, after_json TEXT, operator TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_proposal (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, dict_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('create','rename','merge')),
  content_json TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('user','ai')),
  evidence_snippet TEXT, status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','approved','rejected')),
  proposed_by TEXT, reviewed_by TEXT, reject_reason TEXT,
  created_at TEXT NOT NULL, reviewed_at TEXT
);

-- registry of business tables that reference tags
CREATE TABLE IF NOT EXISTS taxonomy_ref_registry (
  object_type TEXT PRIMARY KEY, table_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_tag_ref (
  object_type TEXT NOT NULL, object_id TEXT NOT NULL, tag_id TEXT NOT NULL,
  org_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (object_type, object_id, tag_id)
);

CREATE TABLE IF NOT EXISTS taxonomy_outbox (
  event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS taxonomy_tag_stats (
  tag_id TEXT NOT NULL, stat_date TEXT NOT NULL, ref_total INTEGER NOT NULL DEFAULT 0,
  ref_30d INTEGER NOT NULL DEFAULT 0, breakdown_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (tag_id, stat_date)
);
"""

WRITE_ROLES = {"knowledge_admin", "ops_supervisor"}


class TaxError(Exception):
    def __init__(self, code: int, msg: str, extra=None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.extra = extra or {}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def nid() -> str:
    return str(uuid.uuid4())


class Taxonomy:
    def __init__(self, path: str = ":memory:", fail_on_table: str | None = None):
        self.db = sqlite3.connect(path, check_same_thread=False)  # ThreadingHTTPServer reuses one service instance
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fail_on_table = fail_on_table  # fault injection for rollback tests
        self._defer = False

    def _commit(self):
        if not self._defer:
            self.db.commit()

    # ---------- helpers ----------
    def _tx(self):
        return self.db

    def _audit(self, role: str):
        if role not in WRITE_ROLES:
            raise TaxError(403, "role not allowed to write taxonomy", {"role": role})

    def _bump_dict(self, dict_id: str):
        self.db.execute("UPDATE taxonomy_dict SET version = version + 1, updated_at = ? WHERE id = ?", (now(), dict_id))

    def _emit(self, event_type: str, payload: dict):
        self.db.execute(
            "INSERT INTO taxonomy_outbox (event_id, event_type, payload_json, created_at) VALUES (?,?,?,?)",
            (nid(), event_type, json.dumps(payload, ensure_ascii=False), now()),
        )

    def _history(self, tag_id: str, action: str, before, after, operator: str, version: int):
        self.db.execute(
            "INSERT INTO taxonomy_tag_history (id, tag_id, version, action, before_json, after_json, operator, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (nid(), tag_id, version, action,
             json.dumps(before, ensure_ascii=False) if before else None,
             json.dumps(after, ensure_ascii=False) if after else None, operator, now()),
        )

    def _tag(self, tag_id: str) -> sqlite3.Row:
        r = self.db.execute("SELECT * FROM taxonomy_tag WHERE id = ? AND deleted_at IS NULL", (tag_id,)).fetchone()
        if not r:
            raise TaxError(404, "tag not found", {"id": tag_id})
        return r

    def _snapshot(self, r) -> dict:
        return {k: r[k] for k in ("name", "definition", "parent_id", "path", "level", "aliases",
                                  "status", "sort_order", "version")}

    def _recount_dict(self, dict_id: str):
        self._bump_dict(dict_id)

    # ----------  ----------
    def create_dict(self, org_id: str, code: str, name: str, max_depth: int = 3, allow_multi: bool = False) -> str:
        did = nid()
        try:
            self.db.execute(
                "INSERT INTO taxonomy_dict (id, org_id, code, name, max_depth, allow_multi, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)", (did, org_id, code, name, max_depth, int(allow_multi), now(), now()))
        except sqlite3.IntegrityError:
            raise TaxError(409, "dict code already exists in org", {"code": code})
        self._commit()
        return did

    # ----------  ----------
    def create_tag(self, dict_id: str, code: str, name: str, role: str = "knowledge_admin",
                   parent_id: str | None = None, definition: str = "", aliases=None, sort_order: int = 0,
                   direct: bool = False) -> str:
        self._audit(role)
        d = self.db.execute("SELECT * FROM taxonomy_dict WHERE id = ?", (dict_id,)).fetchone()
        if not d:
            raise TaxError(404, "dict not found")
        level, path = 1, code
        if parent_id:
            p = self._tag(parent_id)
            if p["dict_id"] != dict_id:
                raise TaxError(400, "parent tag belongs to another dict")
            level = p["level"] + 1
            if level > d["max_depth"]:
                raise TaxError(400, "max_depth exceeded", {"max_depth": d["max_depth"], "level": level})
            path = p["path"] + "/" + code
        tid = nid()
        status = "active" if direct else "draft"
        try:
            self.db.execute(
                "INSERT INTO taxonomy_tag (id, org_id, dict_id, code, name, parent_id, path, level, aliases,"
                " definition, status, sort_order, version, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, d["org_id"], dict_id, code, name, parent_id, path, level,
                 json.dumps(aliases or [], ensure_ascii=False), definition, status, sort_order, 1, now(), now()))
        except sqlite3.IntegrityError:
            raise TaxError(409, "tag code duplicated in dict", {"code": code})
        self._history(tid, "create", None, {"name": name, "status": status}, role, 1)
        self._bump_dict(dict_id)
        self._commit()
        return tid

    def update_tag(self, tag_id: str, role: str = "knowledge_admin", **fields) -> dict:
        self._audit(role)
        r = self._tag(tag_id)
        before = self._snapshot(r)
        allowed = {"name", "definition", "aliases", "sort_order", "parent_id"}
        bad = set(fields) - allowed
        if bad:
            raise TaxError(400, "unsupported field", {"fields": sorted(bad)})
        path, level = r["path"], r["level"]
        if "parent_id" in fields and fields["parent_id"] != r["parent_id"]:
            new_parent = fields["parent_id"]
            if new_parent:
                p = self._tag(new_parent)
                if p["dict_id"] != r["dict_id"]:
                    raise TaxError(400, "parent tag belongs to another dict")
                if p["path"].startswith(r["path"] + "/") or p["id"] == r["id"]:
                    raise TaxError(400, "cycle detected")
                level = p["level"] + 1
                path = p["path"] + "/" + r["code"]
            else:
                level, path = 1, r["code"]
        version = r["version"] + (1 if r["status"] == "active" else 0)
        sets, vals = [], []
        for k, v in fields.items():
            if k == "aliases":
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ?")
            vals.append(v)
        sets += ["path = ?", "level = ?", "version = ?", "updated_at = ?", "updated_by = ?"]
        vals += [path, level, version, now(), role, tag_id]
        self.db.execute(f"UPDATE taxonomy_tag SET {', '.join(sets)} WHERE id = ?", vals)
        r2 = self._tag(tag_id)
        self._history(tag_id, "update", before, self._snapshot(r2), role, version)
        self._emit("taxonomy.tag_updated", {"dict_id": r2["dict_id"], "code": r2["code"], "version": version,
                                            "change": sorted(fields)})
        self._bump_dict(r2["dict_id"])
        self._commit()
        return self._snapshot(r2)

    def delete_tag(self, tag_id: str, role: str = "knowledge_admin"):
        self._audit(role)
        r = self._tag(tag_id)
        if r["status"] != "draft" or self._refcount(tag_id) > 0:
            raise TaxError(400, "only never-activated draft tags can be deleted", {"status": r["status"]})
        self.db.execute("UPDATE taxonomy_tag SET deleted_at = ? WHERE id = ?", (now(), tag_id))
        self._history(tag_id, "delete", self._snapshot(r), None, role, r["version"])
        self._commit()

    # ----------  ----------
    def history(self, tag_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM taxonomy_tag_history WHERE tag_id = ? ORDER BY created_at DESC, version DESC", (tag_id,)).fetchall()
        return [{"version": r["version"], "action": r["action"], "operator": r["operator"],
                 "at": r["created_at"], "before": json.loads(r["before_json"] or "null"),
                 "after": json.loads(r["after_json"] or "null")} for r in rows]

    def snapshot_at(self, tag_id: str, version: int) -> dict:
        rows = self.db.execute(
            "SELECT * FROM taxonomy_tag_history WHERE tag_id = ? AND version <= ? ORDER BY version DESC, created_at DESC",
            (tag_id, version)).fetchall()
        if not rows:
            raise TaxError(404, "no history at version", {"version": version})
        snap = json.loads(rows[0]["after_json"] or "null") or json.loads(rows[0]["before_json"] or "{}")
        cur = self._tag(tag_id)
        snap["merged_into_id"] = cur["merged_into_id"]
        return snap

    # ----------  ----------
    def merge(self, src_id: str, into_id: str, role: str = "knowledge_admin") -> dict:
        self._audit(role)
        src, tgt = self._tag(src_id), self._tag(into_id)
        if src["dict_id"] != tgt["dict_id"]:
            raise TaxError(400, "merge only allowed within the same dict")
        if src["id"] == tgt["id"]:
            raise TaxError(400, "cannot merge into itself")
        if src["status"] == "merged":
            raise TaxError(409, "tag already merged", {"into_id": src["merged_into_id"]})
        try:
            aliases = json.loads(tgt["aliases"])
            if src["name"] not in aliases:
                aliases.append(src["name"])
            regs = self.db.execute("SELECT * FROM taxonomy_ref_registry").fetchall()
            moved = dedup = 0
            per_type = {}
            for reg in regs:
                table = reg["table_name"]
                if self.fail_on_table == table:  # fault injection -> must roll back everything
                    raise RuntimeError(f"simulated failure on {table}")
                refs = self.db.execute(
                    "SELECT object_id FROM taxonomy_tag_ref WHERE object_type = ? AND tag_id = ?",
                    (reg["object_type"], src["id"])).fetchall()
                for ref in refs:
                    exists = self.db.execute(
                        "SELECT 1 FROM taxonomy_tag_ref WHERE object_type = ? AND object_id = ? AND tag_id = ?",
                        (reg["object_type"], ref["object_id"], tgt["id"])).fetchone()
                    if exists:
                        dedup += 1
                    self.db.execute(
                        "INSERT OR IGNORE INTO taxonomy_tag_ref (object_type, object_id, tag_id, org_id, created_at)"
                        " VALUES (?,?,?,?,?)",
                        (reg["object_type"], ref["object_id"], tgt["id"], tgt["org_id"], now()))
                    self.db.execute(
                        "DELETE FROM taxonomy_tag_ref WHERE object_type = ? AND object_id = ? AND tag_id = ?",
                        (reg["object_type"], ref["object_id"], src["id"]))
                    moved += 1
                if refs:
                    per_type[reg["object_type"]] = len(refs)
            v = tgt["version"] + 1
            self.db.execute("UPDATE taxonomy_tag SET aliases = ?, version = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(aliases, ensure_ascii=False), v, now(), tgt["id"]))
            self.db.execute("UPDATE taxonomy_tag SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id = ?",
                            (tgt["id"], now(), src["id"]))
            self._history(src["id"], "merge", self._snapshot(src), {"merged_into_id": tgt["id"]}, role, src["version"])
            self._history(tgt["id"], "merge_target", None, {"aliases": aliases, "version": v}, role, v)
            self._emit("taxonomy.tag_merged", {"dict_id": src["dict_id"], "code": src["code"],
                                               "into_code": tgt["code"], "version": v,
                                               "moved": moved, "dedup": dedup})
            self._bump_dict(src["dict_id"])
            self._commit()
        except Exception:
            self.db.rollback()
            raise
        return {"moved": moved, "dedup": dedup, "per_type": per_type, "target_version": v,
                "target_aliases": aliases}

    def register_ref_table(self, object_type: str, table_name: str):
        self.db.execute("INSERT OR REPLACE INTO taxonomy_ref_registry VALUES (?,?)", (object_type, table_name))
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ("
                        "object_id TEXT, tag_id TEXT, PRIMARY KEY (object_id, tag_id))")
        self._commit()

    def add_ref(self, object_type: str, object_id: str, tag_id: str):
        reg = self.db.execute("SELECT 1 FROM taxonomy_ref_registry WHERE object_type = ?", (object_type,)).fetchone()
        if not reg:
            raise TaxError(400, "object type not registered in ref registry", {"object_type": object_type})
        r = self._tag(tag_id)
        if r["status"] not in ("active",):
            raise TaxError(400, "only active tags can be attached to new objects", {"status": r["status"]})
        self.db.execute("INSERT OR IGNORE INTO taxonomy_tag_ref (object_type, object_id, tag_id, org_id, created_at)"
                        " VALUES (?,?,?,?,?)", (object_type, object_id, tag_id, r["org_id"], now()))
        self._commit()

    def _refcount(self, tag_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM taxonomy_tag_ref WHERE tag_id = ?", (tag_id,)).fetchone()[0]

    def refcount(self, tag_id: str) -> dict:
        rows = self.db.execute("SELECT object_type, COUNT(*) n FROM taxonomy_tag_ref WHERE tag_id = ?"
                               " GROUP BY object_type", (tag_id,)).fetchall()
        return {"total": sum(r["n"] for r in rows), "by_type": {r["object_type"]: r["n"] for r in rows}}

    # ----------  ----------
    def deprecate(self, tag_id: str, reason: str, replacement_id: str | None = None,
                  role: str = "knowledge_admin", migrate: bool = False) -> dict:
        self._audit(role)
        if not reason or not reason.strip():
            raise TaxError(400, "reason is required")
        r = self._tag(tag_id)
        kids = self.db.execute("SELECT id, code FROM taxonomy_tag WHERE parent_id = ? AND deleted_at IS NULL",
                               (tag_id,)).fetchall()
        if kids:
            raise TaxError(409, "deactivate child tags first",
                           {"children": [{"id": k["id"], "code": k["code"]} for k in kids]})
        rc = self.refcount(tag_id)
        v = r["version"] + 1
        self.db.execute("UPDATE taxonomy_tag SET status = 'deprecated', version = ?, updated_at = ? WHERE id = ?",
                        (v, now(), tag_id))
        migrated = 0
        if replacement_id and migrate:
            rep = self._tag(replacement_id)
            if rep["status"] != "active" or rep["dict_id"] != r["dict_id"]:
                raise TaxError(400, "replacement must be an active tag in the same dict")
            for ref in self.db.execute("SELECT object_type, object_id FROM taxonomy_tag_ref WHERE tag_id = ?",
                                       (tag_id,)).fetchall():
                self.db.execute("INSERT OR IGNORE INTO taxonomy_tag_ref VALUES (?,?,?,?,?)",
                                (ref["object_type"], ref["object_id"], rep["id"], rep["org_id"], now()))
                migrated += 1
            self.db.execute("DELETE FROM taxonomy_tag_ref WHERE tag_id = ?", (tag_id,))
        self._history(tag_id, "deprecate", self._snapshot(r),
                      {"status": "deprecated", "reason": reason, "replacement_id": replacement_id}, role, v)
        self._emit("taxonomy.tag_deprecated", {"dict_id": r["dict_id"], "code": r["code"], "version": v,
                                               "reason": reason, "replacement_id": replacement_id})
        self._bump_dict(r["dict_id"])
        self._commit()
        return {"refcount_before": rc["total"], "by_type": rc["by_type"], "migrated": migrated, "version": v}

    # ----------  ----------
    def propose(self, dict_id: str, ptype: str, content: dict, source: str, by: str,
                evidence: str | None = None) -> str:
        if source == "ai" and not (evidence or "").strip():
            raise TaxError(422, "ai-sourced proposal must carry an evidence snippet")
        pid = nid()
        self.db.execute(
            "INSERT INTO taxonomy_proposal (id, org_id, dict_id, type, content_json, source, evidence_snippet,"
            " status, proposed_by, created_at) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
            (pid, "org-1", dict_id, ptype, json.dumps(content, ensure_ascii=False), source,
             evidence, by, now()))
        self._commit()
        return pid

    def approve(self, proposal_id: str, reviewer: str) -> dict:
        p = self.db.execute("SELECT * FROM taxonomy_proposal WHERE id = ?", (proposal_id,)).fetchone()
        if not p:
            raise TaxError(404, "proposal not found")
        if p["status"] != "pending":
            raise TaxError(409, "proposal already reviewed", {"status": p["status"]})
        if p["proposed_by"] == reviewer:
            raise TaxError(403, "proposer cannot review their own proposal")
        content = json.loads(p["content_json"])
        tag_id = None
        if p["type"] == "create":
            tag_id = self.create_tag(p["dict_id"], content["code"], content["name"],
                                     role="knowledge_admin", direct=True,
                                     definition=content.get("definition", ""))
        elif p["type"] == "rename":
            tag_id = content["tag_id"]
            self.update_tag(tag_id, role="knowledge_admin", name=content["name"])
        self.db.execute("UPDATE taxonomy_proposal SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                        (reviewer, now(), proposal_id))
        self._commit()
        return {"status": "approved", "tag_id": tag_id}

    def reject(self, proposal_id: str, reviewer: str, reason: str) -> dict:
        if not (reason or "").strip():
            raise TaxError(400, "reject reason is required")
        p = self.db.execute("SELECT * FROM taxonomy_proposal WHERE id = ?", (proposal_id,)).fetchone()
        if not p:
            raise TaxError(404, "proposal not found")
        if p["status"] != "pending":
            raise TaxError(409, "proposal already reviewed", {"status": p["status"]})
        self.db.execute("UPDATE taxonomy_proposal SET status='rejected', reviewed_by=?, reviewed_at=?,"
                        " reject_reason=? WHERE id=?", (reviewer, now(), reason, proposal_id))
        self._commit()
        return {"status": "rejected"}

    def list_proposals(self, dict_id=None, ptype=None, source=None, status=None, page=1, page_size=20):
        q = "SELECT * FROM taxonomy_proposal WHERE 1=1"
        args = []
        for cond, val in (("dict_id", dict_id), ("type", ptype), ("source", source), ("status", status)):
            if val:
                q += f" AND {cond} = ?"
                args.append(val)
        total = self.db.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [page_size, (page - 1) * page_size]
        rows = self.db.execute(q, args).fetchall()
        return {"total": total, "page": page, "page_size": page_size,
                "items": [{"id": r["id"], "type": r["type"], "source": r["source"], "status": r["status"],
                           "evidence": r["evidence_snippet"], "by": r["proposed_by"]} for r in rows]}

    def batch_approve(self, ids: list[str], reviewer: str) -> dict:
        if len(ids) > 50:
            raise TaxError(400, "at most 50 items per batch", {"count": len(ids)})
        out = []
        self._defer = True          # inner helpers must not commit; the savepoint owns atomicity
        try:
            for n, pid in enumerate(ids):
                sp = f"sp{n}"
                self.db.execute(f"SAVEPOINT {sp}")
                try:
                    self.approve(pid, reviewer)
                    self.db.execute(f"RELEASE {sp}")
                    out.append({"id": pid, "ok": True})
                except TaxError as e:
                    self.db.execute(f"ROLLBACK TO {sp}")
                    self.db.execute(f"RELEASE {sp}")
                    out.append({"id": pid, "ok": False, "error": e.msg})
                except Exception as e:  # noqa: BLE001
                    self.db.execute(f"ROLLBACK TO {sp}")
                    self.db.execute(f"RELEASE {sp}")
                    out.append({"id": pid, "ok": False, "error": str(e)})
        finally:
            self._defer = False
        self._commit()
        return {"ok": sum(1 for o in out if o["ok"]), "failed": sum(1 for o in out if not o["ok"]), "results": out}

    # ----------  ----------
    def tree(self, dict_id: str, depth: int | None = None, include_deprecated: bool = False):
        d = self.db.execute("SELECT * FROM taxonomy_dict WHERE id = ?", (dict_id,)).fetchone()
        if not d:
            raise TaxError(404, "dict not found")
        statuses = ("active", "deprecated") if include_deprecated else ("active",)
        rows = self.db.execute(
            f"SELECT * FROM taxonomy_tag WHERE dict_id = ? AND deleted_at IS NULL AND status IN ({','.join('?' * len(statuses))})"
            " ORDER BY level, sort_order, name", (dict_id, *statuses)).fetchall()
        nodes = {r["id"]: {"code": r["code"], "name": r["name"], "level": r["level"],
                           "status": r["status"], "aliases": json.loads(r["aliases"]), "children": []}
                 for r in rows}
        roots = []
        for r in rows:
            if r["level"] > 1 and r["parent_id"] in nodes:
                nodes[r["parent_id"]]["children"].append(nodes[r["id"]])
            elif r["level"] == 1:
                roots.append(nodes[r["id"]])

        def cut(items, d_left):
            if d_left <= 0:
                return items
            for it in items:
                it["children"] = cut(it["children"], d_left - 1) if d_left > 1 else []
            return items

        if depth:
            roots = cut(roots, depth)
        body = json.dumps({"dict": d["code"], "version": d["version"], "tags": roots}, ensure_ascii=False, sort_keys=True)
        etag = 'W/"' + hashlib.sha1(body.encode()).hexdigest()[:16] + '"'
        return {"body": body, "etag": etag, "count": len(rows)}

    def list_dicts(self, org_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT d.code, d.name, d.max_depth, COUNT(t.id) AS n FROM taxonomy_dict d"
            " LEFT JOIN taxonomy_tag t ON t.dict_id = d.id AND t.deleted_at IS NULL"
            " WHERE d.org_id = ? GROUP BY d.id ORDER BY d.code", (org_id,)).fetchall()
        return [{"code": r["code"], "name": r["name"], "max_depth": r["max_depth"], "tag_count": r["n"]} for r in rows]

    # ----------  ----------
    def import_dry_run(self, dict_id: str, rows: list[dict]) -> dict:
        existing = {r["code"]: r for r in self.db.execute(
            "SELECT * FROM taxonomy_tag WHERE dict_id = ? AND deleted_at IS NULL", (dict_id,)).fetchall()}
        added, updated, conflicts, errors = [], [], [], []
        seen = set()
        for i, row in enumerate(rows, start=2):  # line 1 = header
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            if not code:
                errors.append({"row": i, "field": "code", "reason": "code is empty"})
                continue
            if not name:
                errors.append({"row": i, "field": "name", "reason": "name is empty"})
                continue
            if code in seen:
                errors.append({"row": i, "field": "code", "reason": "duplicated code inside file"})
                continue
            seen.add(code)
            parent = (row.get("parent_code") or "").strip()
            if parent and parent not in existing and parent not in seen:
                errors.append({"row": i, "field": "parent_code", "reason": f"unknown parent {parent}"})
                continue
            cur = existing.get(code)
            if cur is None:
                added.append({"row": i, "code": code, "name": name})
            else:
                fields = []
                if cur["name"] != name:
                    conflicts.append({"row": i, "code": code, "field": "name",
                                      "existing": cur["name"], "incoming": name})
                    fields.append("name")
                if (row.get("definition") or "") != (cur["definition"] or ""):
                    fields.append("definition")
                if set(row.get("aliases") or []) - set(json.loads(cur["aliases"])):
                    fields.append("aliases")
                if fields:
                    updated.append({"row": i, "code": code, "fields": fields})
        return {"rows": len(rows), "added": added, "updated": updated, "conflicts": conflicts, "errors": errors}

    def import_apply(self, dict_id: str, rows: list[dict], role="knowledge_admin", init_mode=False):
        if init_mode and role != "system_admin":
            raise TaxError(403, "initialization mode requires system_admin")
        plan = self.import_dry_run(dict_id, rows)
        if plan["errors"]:
            raise TaxError(400, "import aborted: fix error rows first", {"errors": plan["errors"]})
        created = changed = 0
        for item in plan["added"]:
            row = next(r for r in rows if (r.get("code") or "").strip() == item["code"])
            parent = (row.get("parent_code") or "").strip()
            parent_id = None
            if parent:
                p = self.db.execute("SELECT id FROM taxonomy_tag WHERE dict_id = ? AND code = ? AND deleted_at IS NULL",
                                    (dict_id, parent)).fetchone()
                parent_id = p["id"] if p else None
            self.create_tag(dict_id, item["code"], item["name"], role="knowledge_admin",
                            parent_id=parent_id, definition=row.get("definition") or "",
                            aliases=row.get("aliases") or [], direct=init_mode)
            created += 1
        for item in plan["updated"]:
            row = next(r for r in rows if (r.get("code") or "").strip() == item["code"])
            cur = self.db.execute("SELECT * FROM taxonomy_tag WHERE dict_id = ? AND code = ? AND deleted_at IS NULL",
                                  (dict_id, item["code"])).fetchone()
            new_aliases = sorted(set(json.loads(cur["aliases"])) | set(row.get("aliases") or []))
            if new_aliases != json.loads(cur["aliases"]) or cur["definition"] != (row.get("definition") or ""):
                self.db.execute("UPDATE taxonomy_tag SET aliases = ?, definition = ?, updated_at = ? WHERE id = ?",
                                (json.dumps(new_aliases, ensure_ascii=False), row.get("definition") or cur["definition"],
                                 now(), cur["id"]))
                changed += 1
        self._commit()
        return {"created": created, "updated": changed, "rows": plan["rows"]}
