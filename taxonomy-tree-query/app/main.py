# -*- coding: utf-8 -*-
"""FastAPI application: taxonomy dictionary / tree query endpoints.

* GET /api/taxonomy/dicts                      -> list dicts with tag counts
* GET /api/taxonomy/dicts/{dict}/tree          -> tag tree, optional
    include_deprecated / depth, weak ETag + If-None-Match -> 304

Both endpoints are read-only GET routers (FastAPI). Conditional-request
handling uses Starlette's ``Response`` with an ``ETag`` header, per the
project's unified tech convention (FastAPI + SQLAlchemy 2 + PostgreSQL 16).
"""
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import MAX_DEPTH
from .database import get_session
from .models import TAG_STATUS_ACTIVE, TaxonomyDict, TaxonomyTag
from .tree import build_tree, etag_matches, tree_etag

app = FastAPI(
    title="Taxonomy Service (dict & tag center)",
    version="2.0.0",
    description="Dictionary list + tag tree query with ETag/304 support.",
)


@app.get("/api/taxonomy/dicts")
def list_dicts(
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    """List all non-deleted dictionaries with their active-tag counts."""
    active_count = (
        select(func.count())
        .select_from(TaxonomyTag)
        .where(
            TaxonomyTag.dict_id == TaxonomyDict.id,
            TaxonomyTag.deleted_at.is_(None),
            TaxonomyTag.status == TAG_STATUS_ACTIVE,
            TaxonomyTag.merged_into_id.is_(None),
        )
        .correlate(TaxonomyDict)
        .scalar_subquery()
    )
    rows = session.execute(
        select(TaxonomyDict, active_count.label("tag_count"))
        .where(TaxonomyDict.deleted_at.is_(None))
        .order_by(TaxonomyDict.code, TaxonomyDict.id)
    ).all()
    items = [
        {
            "code": d.code,
            "name": d.name,
            "version": d.version,
            "tag_count": int(count or 0),
        }
        for d, count in rows
    ]
    return JSONResponse(content={"items": items, "total": len(items)})


@app.get("/api/taxonomy/dicts/{dict_code}/tree")
def get_tree(
    dict_code: str,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    include_deprecated: bool = Query(False, description="Also return deprecated tags"),
    depth: int | None = Query(None, ge=1, le=MAX_DEPTH, description="Max levels to return (1-based)"),
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """Return the tag tree of one dictionary.

    ETag semantics: weak ETag over (dict code, dict version, tree JSON).
    ``If-None-Match`` matching the current ETag -> 304 with empty body and
    the ETag echoed back. Any tag change bumps the dict version, so the
    ETag changes and the next request is a 200 again.
    """
    d = session.execute(
        select(TaxonomyDict).where(
            TaxonomyDict.code == dict_code,
            TaxonomyDict.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail=f"dictionary '{dict_code}' not found")

    tags = session.execute(select(TaxonomyTag).where(TaxonomyTag.dict_id == d.id)).scalars().all()
    tree = build_tree(list(tags), include_deprecated=include_deprecated, max_depth=depth)
    etag = tree_etag(d.code, d.version, tree)

    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})

    return JSONResponse(
        content={
            "dict": d.code,
            "version": d.version,
            "include_deprecated": include_deprecated,
            "depth": depth,
            "tree": tree,
        },
        headers={"ETag": etag},
    )


# org_id / site_id scoping note: this reference implementation serves a
# single tenant from one database schema. When integrating into the shared
# backend, add ``TaxonomyDict.org_id == <request.org_id>`` (and site_id)
# filters to both queries, or rely on the session-level RLS used there.
def register_healthcheck(target_app: FastAPI) -> FastAPI:
    @target_app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return target_app


register_healthcheck(app)

# silence unused-import linters for uuid (kept for extension helpers)
_ = uuid
