# -*- coding: utf-8 -*-
"""taxonomy dicts & tags

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID_KW = {"server_default": sa.text("gen_random_uuid()")}


def upgrade() -> None:
    op.create_table(
        "taxonomy_dicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, **UUID_KW),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "site_id", "code", name="uq_taxonomy_dicts_code"),
    )

    op.create_table(
        "taxonomy_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, **UUID_KW),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dict_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dict_id"], ["taxonomy_dicts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["taxonomy_tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["merged_into_id"], ["taxonomy_tags.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_taxonomy_tags_dict_id", "taxonomy_tags", ["dict_id"])
    op.create_index("ix_taxonomy_tags_parent_id", "taxonomy_tags", ["parent_id"])
    op.create_index("ix_taxonomy_tags_status", "taxonomy_tags", ["dict_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_taxonomy_tags_status", table_name="taxonomy_tags")
    op.drop_index("ix_taxonomy_tags_parent_id", table_name="taxonomy_tags")
    op.drop_index("ix_taxonomy_tags_dict_id", table_name="taxonomy_tags")
    op.drop_table("taxonomy_tags")
    op.drop_table("taxonomy_dicts")
