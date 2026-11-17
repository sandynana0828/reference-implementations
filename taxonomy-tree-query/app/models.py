# -*- coding: utf-8 -*-
"""SQLAlchemy 2 models for the dictionary & tag center (taxonomy).

Business tables follow the project-wide convention: every table carries
id (UUID), org_id, site_id, created_at, created_by, updated_at, deleted_at
(soft delete).

Semantics used by the tree endpoint:

* ``status = 'deprecated'``  -> disabled tag, hidden unless include_deprecated.
* ``merged_into_id`` set     -> the tag has disappeared by being merged into
  another tag; it is NEVER returned by the tree (the source tag's name was
  recorded in the target tag's ``aliases`` so no semantics are lost).
* ``deleted_at`` set         -> soft deleted, never returned.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TAG_STATUS_ACTIVE = "active"
TAG_STATUS_DEPRECATED = "deprecated"


class Base(DeclarativeBase):
    pass


class TaxonomyDict(Base):
    __tablename__ = "taxonomy_dicts"
    __table_args__ = (UniqueConstraint("org_id", "site_id", "code", name="uq_taxonomy_dicts_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Bumped whenever any tag of this dict changes; feeds the tree ETag.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tags: Mapped[list["TaxonomyTag"]] = relationship(back_populates="dict_", passive_deletes=True)


class TaxonomyTag(Base):
    __tablename__ = "taxonomy_tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dict_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_dicts.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_tags.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TAG_STATUS_ACTIVE)
    # Set when this tag was merged into another one; the tag never shows again.
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_tags.id", ondelete="SET NULL"), nullable=True
    )
    # Historical names (e.g. names of tags merged into this one).
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dict_: Mapped[TaxonomyDict] = relationship(back_populates="tags", remote_side=[TaxonomyDict.id])
