# -*- coding: utf-8 -*-
"""Environment-driven configuration.

统一技术约定: 配置与密钥只从环境变量读取, 不硬编码。
"""
import os


def _default_database_url() -> str:
    db = os.environ.get("TAXO_PG_DB", "taxonomy")
    host = os.environ.get("TAXO_PG_HOST", "127.0.0.1")
    port = os.environ.get("TAXO_PG_PORT", "5432")
    user = os.environ.get("TAXO_PG_USER", "postgres")
    password = os.environ.get("TAXO_PG_PASSWORD", "")
    if password:
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
    return f"postgresql+psycopg://{user}@{host}:{port}/{db}"


DATABASE_URL: str = os.environ.get("DATABASE_URL") or _default_database_url()

# Tree query hard limits (protect the service from pathological payloads).
MAX_DEPTH: int = int(os.environ.get("TAXO_MAX_DEPTH", "16"))
