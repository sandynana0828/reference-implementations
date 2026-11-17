# -*- coding: utf-8 -*-
"""Tree builder + weak ETag computation.

Pure functions on tag rows; no HTTP concerns here so the logic is directly
unit-testable.
"""
import hashlib
import json
from dataclasses import dataclass, field

from .models import TAG_STATUS_ACTIVE, TAG_STATUS_DEPRECATED, TaxonomyTag


@dataclass
class TagNode:
    code: str
    name: str
    level: int
    aliases: list[str]
    status: str
    children: list["TagNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "level": self.level,
            "aliases": self.aliases,
            "children": [c.to_dict() for c in self.children],
        }


def visible_tags(tags: list[TaxonomyTag], include_deprecated: bool) -> list[TaxonomyTag]:
    """A tag is visible when it is not soft deleted, not merged away, and
    (active, or deprecated when include_deprecated=True)."""
    out = []
    for t in tags:
        if t.deleted_at is not None:
            continue
        if t.merged_into_id is not None:
            continue
        if t.status == TAG_STATUS_DEPRECATED and not include_deprecated:
            continue
        out.append(t)
    return out


def build_tree(tags: list[TaxonomyTag], include_deprecated: bool, max_depth: int | None = None) -> list[dict]:
    """Build the tree of visible tags.

    ``max_depth`` (1-based): nodes at that level get ``children: []`` so the
    caller can tell truncation happened (children are never null).
    """
    rows = visible_tags(tags, include_deprecated)
    by_parent: dict[str | None, list[TaxonomyTag]] = {}
    for t in rows:
        key = str(t.parent_id) if t.parent_id is not None else None
        by_parent.setdefault(key, []).append(t)

    def walk(parent_id: str | None, level: int) -> list[TagNode]:
        nodes = []
        for t in sorted(by_parent.get(parent_id, []), key=lambda x: (x.code, str(x.id))):
            node = TagNode(code=t.code, name=t.name, level=level, aliases=list(t.aliases or []), status=t.status)
            if max_depth is None or level < max_depth:
                node.children = walk(str(t.id), level + 1)
            nodes.append(node)
        return nodes

    return [n.to_dict() for n in walk(None, 1)]


def tree_etag(dict_code: str, dict_version: int, tree: list[dict]) -> str:
    """Weak ETag over (dict code, dict version, canonical tree JSON)."""
    payload = json.dumps(
        {"dict": dict_code, "version": dict_version, "tree": tree},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f'W/"{digest}"'


def etag_matches(if_none_match_header: str | None, etag: str) -> bool:
    """RFC 7232 comparison against an If-None-Match header.

    Handles ``*``, lists of etags, and weak comparison (weak etags compare
    equal when their opaque values match).
    """
    if not if_none_match_header:
        return False
    opaque = etag.split('"')[1] if '"' in etag else etag
    for candidate in if_none_match_header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        c_opaque = candidate.split('"')[1] if '"' in candidate else candidate
        if c_opaque == opaque:
            return True
    return False


# Re-exported so callers (and the API layer) share one set of status names.
TAG_STATUSES = (TAG_STATUS_ACTIVE, TAG_STATUS_DEPRECATED)
