# -*- coding: utf-8 -*-
"""Pytest fixtures: a REAL PostgreSQL 16 database per test session.

The fixture creates a dedicated scratch database, runs ``alembic upgrade
head`` against it (so migrations themselves are exercised), and drops it at
the end. Connection settings come from env vars so nothing is hardcoded:

* TAXO_TEST_ADMIN_URL  - admin URL used to create/drop the scratch DB
                         (default: postgresql+psycopg://postgres@127.0.0.1:5432/postgres)
* TAXO_TEST_DB_URL     - the scratch DB URL handed to the app & alembic
                         (default: same admin host, db name taxonomy_test)
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT))

ADMIN_URL = os.environ.get("TAXO_TEST_ADMIN_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")
TEST_DB_URL = os.environ.get("TAXO_TEST_DB_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/taxonomy_test")
SCRATCH_DB = TEST_DB_URL.rsplit("/", 1)[-1]


@pytest.fixture(scope="session", autouse=True)
def pg_database():
    # The app reads DATABASE_URL at import time: set it before anything
    # imports app.*, so the FastAPI app points at the scratch database.
    os.environ["DATABASE_URL"] = TEST_DB_URL
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        cfg = Config(str(PKG_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
        command.upgrade(cfg, "head")
        yield TEST_DB_URL
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def client(pg_database):
    """Fresh engine + seeded dataset per test, served through FastAPI TestClient."""
    from fastapi.testclient import TestClient

    from app.database import engine, SessionLocal
    from app.main import app
    from app.models import Base

    engine.dispose()
    Base.metadata.create_all(engine)  # no-op safety; tables come from alembic
    session = SessionLocal()
    seed(session)
    session.close()

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(engine)


def seed(session):
    """Dataset covering every acceptance path.

    dict "industry" v1:
      manufacturing (active, root)
        auto (active)            <- alias carries the merged tag's name
          ev (active)
        elec (active)
      services (active, root)
        consulting (active)
      legacy (deprecated, root)  <- hidden by default
      old_auto (merged into auto) <- never shown
      ghost (soft deleted)        <- never shown
    dict "region" v1: cn (active, root), eu (active, root)
    """
    from app.models import TAG_STATUS_ACTIVE, TAG_STATUS_DEPRECATED, TaxonomyDict, TaxonomyTag

    org, site = uuid.uuid4(), uuid.uuid4()

    d_ind = TaxonomyDict(org_id=org, site_id=site, code="industry", name="Industry Tags", version=1)
    d_reg = TaxonomyDict(org_id=org, site_id=site, code="region", name="Region Tags", version=1)
    session.add_all([d_ind, d_reg])
    session.flush()

    def tag(d, code, name, parent=None, status=TAG_STATUS_ACTIVE, merged_into=None, aliases=None, deleted=False):
        if parent is not None:
            session.flush()  # ensure parent.id is assigned before referencing it
        t = TaxonomyTag(
            org_id=org,
            site_id=site,
            dict_id=d.id,
            code=code,
            name=name,
            parent_id=parent.id if parent else None,
            status=status,
            merged_into_id=merged_into.id if merged_into else None,
            aliases=aliases or [],
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
        session.add(t)
        return t

    manufacturing = tag(d_ind, "manufacturing", "Manufacturing")
    auto = tag(d_ind, "auto", "Automotive", parent=manufacturing, aliases=["Old Automotive"])
    ev = tag(d_ind, "ev", "New Energy Vehicles", parent=auto)
    elec = tag(d_ind, "elec", "Electronics Manufacturing", parent=manufacturing)
    services = tag(d_ind, "services", "Modern Services")
    consulting = tag(d_ind, "consulting", "Consulting", parent=services)
    legacy = tag(d_ind, "legacy", "Legacy Sector", status=TAG_STATUS_DEPRECATED)
    old_auto = tag(d_ind, "old_auto", "Old Automotive", status=TAG_STATUS_DEPRECATED, merged_into=auto)
    ghost = tag(d_ind, "ghost", "Ghost Tag", deleted=True)
    session.add_all([manufacturing, auto, ev, elec, services, consulting, legacy, old_auto, ghost])

    cn = tag(d_reg, "cn", "China")
    eu = tag(d_reg, "eu", "Europe")
    session.add_all([cn, eu])

    session.commit()
