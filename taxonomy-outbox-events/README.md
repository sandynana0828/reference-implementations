# 字典变更事件（事务性 Outbox）

字典与标签中心的四类生命周期事件通过**事务性 outbox** 发布：标签生效 /
修改 / 停用 / 合并时，事件行与业务变更在**同一 SQL 事务**内写入
`taxonomy_outbox`，由 `OutboxDispatcher` 投递给订阅方。

| 事件 | 触发 |
|---|---|
| `taxonomy.tag_activated` | 标签生效（直建 / 审批通过 / 显式 activate） |
| `taxonomy.tag_updated` | 生效标签字段修改 |
| `taxonomy.tag_deprecated` | 标签停用 |
| `taxonomy.tag_merged` | 标签合并（源标签状态变 `merged`） |

事件负载统一含字典 code、标签 code、变更后版本号、发生时间、变更字段；
完整字段说明见 [`EVENTS.md`](EVENTS.md)。

## 复现

```bash
python -m unittest test_outbox -v    # Ran 11 tests ... OK
python consumer_example.py           # 端到端：7 条事件、双消费者同步、undelivered=0
```

`unittest_output.txt` 与 `demo_output.txt` 均为命令真实输出。

## 语义要点

- **回滚安全**：业务事务回滚时事件行随之消失（测试以 `_defer` 延迟提交 +
  `ROLLBACK`，以及合并路径故障注入两种方式验证）。
- **至少一次投递**：`delivered_at` 打点在处理之后，崩溃重投可能重复；
  示例消费者（MatchingEngine / SearchIndex）按 `(dict, code)` 覆盖式写入，幂等。
- 零第三方依赖；数据集为虚构示例。
