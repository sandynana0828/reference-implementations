# -*- coding: utf-8 -*-
"""Acceptance & regression tests, all executed against a REAL PostgreSQL 16.

Acceptance criteria (from the ticket):
1. Deprecated & merged tags are excluded by default.
2. depth=1 returns only the first level.
3. Unchanged dict + If-None-Match -> 304 (empty body, ETag echoed).

All run through FastAPI's TestClient so routing, serialization, headers and
conditional-request handling are exercised end to end on PG16.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TREE = "/api/taxonomy/dicts/industry/tree"


def codes(nodes, level=None):
    out = []
    for n in nodes:
        if level is None or n["level"] == level:
            out.append(n["code"])
        out.extend(codes(n.get("children", []), level))
    return out


# ---------------------------------------------------------------- acceptance 1
def test_default_excludes_deprecated_and_merged(client):
    r = client.get(TREE)
    assert r.status_code == 200
    body = r.json()
    cs = codes(body["tree"])
    # active tags are present
    assert {"manufacturing", "auto", "ev", "elec", "services", "consulting"} <= set(cs)
    # deprecated, merged-away and soft-deleted tags are all absent
    assert "legacy" not in cs          # deprecated
    assert "old_auto" not in cs        # merged into 'auto' -> never returned
    assert "ghost" not in cs           # soft deleted
    # merged semantics preserved: the target tag keeps the old name as alias
    auto = next(n for n in body["tree"] if n["code"] == "manufacturing")
    auto = next(n for n in auto["children"] if n["code"] == "auto")
    assert "Old Automotive" in auto["aliases"]


def test_include_deprecated_returns_deprecated_but_not_merged(client):
    r = client.get(TREE, params={"include_deprecated": "true"})
    assert r.status_code == 200
    cs = codes(r.json()["tree"])
    assert "legacy" in cs              # deprecated now visible
    assert "old_auto" not in cs        # merged tags stay invisible in every mode
    assert "ghost" not in cs


# ---------------------------------------------------------------- acceptance 2
def test_depth_one_returns_only_first_level(client):
    r = client.get(TREE, params={"depth": 1})
    assert r.status_code == 200
    tree = r.json()["tree"]
    assert set(codes(tree)) == {"manufacturing", "services"}
    for node in tree:
        # truncation is explicit: children is an empty array, never null/absent
        assert node["children"] == []


def test_depth_two_keeps_second_level_and_truncates_third(client):
    r = client.get(TREE, params={"depth": 2})
    assert r.status_code == 200
    tree = r.json()["tree"]
    assert set(codes(tree)) == {"manufacturing", "auto", "elec", "services", "consulting"}
    manu = next(n for n in tree if n["code"] == "manufacturing")
    auto = next(n for n in manu["children"] if n["code"] == "auto")
    # third level (ev) truncated
    assert auto["children"] == []
    assert codes(manu["children"]) == ["auto", "elec"]


# ---------------------------------------------------------------- acceptance 3
def test_if_none_match_returns_304_and_revalidates_after_change(client):
    r1 = client.get(TREE)
    assert r1.status_code == 200
    etag = r1.headers["ETag"]
    assert etag.startswith('W/"')

    # unchanged dict + If-None-Match -> 304 with empty body, ETag echoed
    r2 = client.get(TREE, headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.content == b""
    assert r2.headers["ETag"] == etag

    # list-of-etags and wildcard also match (RFC 7232)
    assert client.get(TREE, headers={"If-None-Match": f'"xxx", {etag}'}).status_code == 304
    assert client.get(TREE, headers={"If-None-Match": "*"}).status_code == 304

    # a real change (dict version bump) invalidates the ETag -> 200 again
    import os
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(text("UPDATE taxonomy_dicts SET version = version + 1 WHERE code = 'industry'"))
    r3 = client.get(TREE, headers={"If-None-Match": etag})
    assert r3.status_code == 200
    assert r3.headers["ETag"] != etag
    engine.dispose()


def test_etag_stable_and_weak_comparison(client):
    e1 = client.get(TREE).headers["ETag"]
    e2 = client.get(TREE).headers["ETag"]
    assert e1 == e2  # stable across repeated identical requests
    # weak comparison: client sending strong form of same opaque value matches
    strong = e1.replace('W/', "")
    assert client.get(TREE, headers={"If-None-Match": strong}).status_code == 304


def test_etag_depends_on_depth_and_include_deprecated(client):
    full = client.get(TREE).headers["ETag"]
    d1 = client.get(TREE, params={"depth": 1}).headers["ETag"]
    inc = client.get(TREE, params={"include_deprecated": "true"}).headers["ETag"]
    assert len({full, d1, inc}) == 3


# ---------------------------------------------------------------- extra paths
def test_list_dicts_with_tag_counts(client):
    r = client.get("/api/taxonomy/dicts")
    assert r.status_code == 200
    items = {i["code"]: i for i in r.json()["items"]}
    assert items["industry"]["tag_count"] == 6   # active, not merged, not deleted
    assert items["region"]["tag_count"] == 2
    assert items["industry"]["version"] == 1


def test_unknown_dict_404(client):
    assert client.get("/api/taxonomy/dicts/nope/tree").status_code == 404


def test_invalid_depth_rejected(client):
    assert client.get(TREE, params={"depth": 0}).status_code == 422
    assert client.get(TREE, params={"depth": 999}).status_code == 422


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}
