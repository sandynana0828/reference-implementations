# 字典树查询接口（条件请求 / 分层）

只读查询服务的参考实现：

- `GET /api/taxonomy/dicts` — 列出字典及各自 **active** 标签数量
- `GET /api/taxonomy/dicts/{dict}/tree` — 返回标签树：
  - 默认只含 active 标签；`?include_deprecated=true` 带回已停用标签
  - `?depth=N` 限制返回层级（`depth=1` 只返回第一层）
  - 节点字段：`code` / `name` / `level` / `children` / `aliases` / `status`
  - 响应带弱 `ETag`；携带 `If-None-Match` 且字典未变化时返回 **304** 空体

## 复现

```bash
python -m unittest test_tree -v     # Ran 14 tests ... OK（服务层 7 + 真实 HTTP 层 7）
python tree_api.py 8101             # 起服务后可复现 smoke_output.txt 里的每条请求
```

`unittest_output.txt` 与 `smoke_output.txt` 均为命令真实输出（含 200 / 304 / 404）。

## 说明

- 零第三方依赖：核心服务只用标准库（`sqlite3`、`http.server`、`unittest`）；
  HTTP 处理器刻意做薄，可原样迁入 FastAPI router。
- 数据集为虚构示例。
