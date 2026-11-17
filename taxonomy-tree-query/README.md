# taxonomy-tree-query (FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16)

字典与标签中心的树查询服务参考实现：Python 3.11+ / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL 16。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/taxonomy/dicts/{dict}/tree` | 标签树。默认只含 active 标签；`?include_deprecated=true` 带回已停用；`?depth=N` 限制层级；弱 ETag，未变化时 `If-None-Match` 返回 304 |
| GET | `/api/taxonomy/dicts` | 列出所有字典及各自 active 标签数 |
| GET | `/healthz` | 健康检查 |

### 语义要点

- **已停用（`status='deprecated'`）**：默认不返回，`include_deprecated=true` 时返回。
- **已合并（`merged_into_id` 非空）**：任何模式下都不返回；合并时源标签名写入目标标签的 `aliases`，语义不丢失。
- **软删除（`deleted_at` 非空）**：任何模式下不返回。
- **depth 截断**：`depth=1` 只返回第一层且 `children` 截断为 `[]`（空数组，不是 null/缺省）。
- **ETag**：弱 ETag `W/"<sha1 前 16 位>"`，输入为 `(字典 code, 字典 version, 树 JSON)` 的规范化哈希；任何标签变更使字典 `version` 自增，ETag 随之变化。304 响应空 body 并回显 ETag；`If-None-Match` 支持 etag 列表、`*` 与弱比较（RFC 7232）。

## 数据模型（Alembic 迁移 `0001`）

- `taxonomy_dicts`：id(UUID pk)、org_id、site_id、code（org+site 内唯一）、name、version、
  created_at / created_by / updated_at / deleted_at（软删除）。
- `taxonomy_tags`：同上公共列 + dict_id(FK, CASCADE)、code、name、parent_id(自引用 FK)、
  status(`active`/`deprecated`)、merged_into_id(自引用 FK, SET NULL)、aliases(JSONB)。
- 迁移可 `upgrade` 与 `downgrade`（真实执行记录见 `migration_output.txt`）。

## 运行

```bash
pip install -r requirements.txt

export DATABASE_URL="postgresql+psycopg://<user>:<password>@<host>:5432/<db>"

alembic upgrade head            # 建表
uvicorn app.main:app --port 8000
```

配置只从环境变量读取（`DATABASE_URL`，可选 `TAXO_PG_*` / `TAXO_MAX_DEPTH`）。

## 测试（真实 PostgreSQL 16）

`tests/conftest.py` 每次测试会话自动创建临时数据库（默认 `taxonomy_test`，可用
`TAXO_TEST_ADMIN_URL` / `TAXO_TEST_DB_URL` 覆盖），执行 `alembic upgrade head` 后
通过 FastAPI TestClient 跑全部用例，结束时删库。测试跑在真实 PG16 上，不使用 SQLite。

```bash
pytest tests/ -v
```

`unittest_output.txt`（11/11 通过）、`migration_output.txt`（upgrade→downgrade→upgrade）、
`smoke_output.txt`（uvicorn 真实 HTTP：200/304/depth/404）为真实执行记录。

## 对接点（集成提示）

- 本包只读。字典/标签 CRUD 侧每次变更标签（增/改名/停用/合并/软删）必须自增对应字典
  的 `version`，否则 ETag 不失效。
- 多租户：本参考实现单库单租户；接入多租户后端时在两处查询上追加 `org_id` / `site_id` 过滤。
- 可选 Redis 缓存：树 JSON 按 `dict:{code}:v{version}` 缓存，version 变更即失效。

## 核心用例

1. 默认不返回已停用与已合并标签 → `test_default_excludes_deprecated_and_merged`
2. `depth=1` 只返回第一层 → `test_depth_one_returns_only_first_level`
3. 字典未变化时 `If-None-Match` 返回 304 → `test_if_none_match_returns_304_and_revalidates_after_change`
