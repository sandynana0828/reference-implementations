# 字典变更事件 · 结构说明（Outbox）

字典与标签中心的每次生命周期变更都会**在与业务变更相同的 SQL 事务内**向
`taxonomy_outbox` 表写入一条事件记录；下游（商机匹配引擎、搜索索引）通过
`OutboxDispatcher` 按游标拉取并消费。事件先落库、后投递：业务事务回滚时事件
随之消失，绝不会出现「业务没发生、事件却发出」的幽灵事件。

## 事件类型

| event_type | 触发操作 |
|---|---|
| `taxonomy.tag_activated` | 标签生效：直建（`direct=True`）、提案审批通过（create 类）、显式 `activate_tag()` |
| `taxonomy.tag_updated` | 生效标签的可变字段变更（name / definition / aliases / sort_order / parent_id） |
| `taxonomy.tag_deprecated` | 标签停用（可携带替代标签） |
| `taxonomy.tag_merged` | 标签合并进目标标签（单事件；源标签状态随之变为 `merged`，不再另发 `tag_deprecated`） |

## Outbox 行结构（表 `taxonomy_outbox`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `event_id` | TEXT (UUID) | 事件唯一 ID，主键；消费方据此幂等去重 |
| `event_type` | TEXT | 事件类型，取值见上表 |
| `payload_json` | TEXT | 事件负载，JSON 对象，结构见下节 |
| `created_at` | TEXT (ISO-8601, UTC) | 事件写入时间（即业务变更时间） |
| `delivered_at` | TEXT (ISO-8601, UTC)，可空 | 消费完成打点；NULL 表示待投递。崩溃恢复时未打点事件会重投，消费方必须幂等 |

## 负载结构（payload）

| 字段 | 类型 | 说明 | 出现于 |
|---|---|---|---|
| `dict` | TEXT | 字典 code（如 `industry`），消费方免查库 | 全部 |
| `dict_id` | TEXT (UUID) | 字典 ID | 全部 |
| `code` | TEXT | 标签 code（变更主体；merge 事件中为**源**标签 code） | 全部 |
| `version` | INTEGER | 变更后的标签版本号（单调递增） | 全部 |
| `occurred_at` | TEXT (ISO-8601, UTC) | 变更发生时间 | 全部 |
| `change` | TEXT[] | 变更字段列表（activated 固定 `["status=active"]`；updated 为被改字段名排序；merge/deprecate 亦填） | 全部 |
| `summary` | TEXT | 变更摘要（activated 事件） | activated |
| `operator` | TEXT | 操作人角色 / 审批人 | activated |
| `into_code` | TEXT | 合并目标标签 code | merged |
| `moved` | INTEGER | 合并时迁移的引用计数 | merged |
| `dedup` | INTEGER | 合并时去重的引用对象数 | merged |
| `reason` | TEXT | 停用原因 | deprecated |
| `replacement_id` | TEXT (UUID)，可空 | 替代标签 ID | deprecated |

## 消费语义

- **投递方式**：`OutboxDispatcher.drain(handlers)` 按 `rowid` 升序拉取
  `delivered_at IS NULL` 的事件，逐个调用 handler，全部成功后统一打
  `delivered_at`。
- **至少一次（at-least-once）**：打点发生在处理之后，崩溃重投可能重复；
  两个示例消费者（`consumer_example.py` 的 `MatchingEngine` / `SearchIndex`）
  均按 `(dict, code)` 覆盖式写入，天然幂等。
- **顺序**：同一业务变更只产生一条事件；行级游标保证首次消费按写入顺序。
- **回滚安全**：所有写入共享一个连接事务（`Taxonomy._defer` 模式下由外层
  `COMMIT/ROLLBACK` 控制），回滚后 outbox 与业务表同时回到变更前状态。

## 消费示例

```python
from outbox_ext import OutboxDispatcher, OutboxTaxonomy
from consumer_example import MatchingEngine, SearchIndex

svc = OutboxTaxonomy("taxonomy.db")
engine, index = MatchingEngine(), SearchIndex()
dispatched = OutboxDispatcher(svc).drain([engine, index])
print(f"dispatched {dispatched} events")
```

运行 `python consumer_example.py` 可看到完整端到端走查（建标签 → 生效 →
修改 → 停用 → 合并 → 两个消费者视图同步）。
