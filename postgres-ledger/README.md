# PostgreSQL Deposit Ledger — 数据模型示例

一个可直接在本机 PostgreSQL 上执行的数据模型示例，演示四类常见约束如何被真实 SQL 验证，而不是只写在设计文档里。

## 验证的能力

| 能力 | 做法 |
|---|---|
| 金额按分存储 | `amount_cents bigint CHECK (amount_cents >= 0)`，查询时才换算成元 |
| 结构化扩展字段 | `jsonb` + `->>` 查询 |
| 软删后业务键可复用 | 部分唯一索引 `WHERE deleted_at IS NULL` |
| 主键与审计字段 | `uuid` 主键、`created_at` / `deleted_at` 时间戳 |

## 文件

| 文件 | 作用 |
|---|---|
| `schema.sql` | 建表与两个索引（含部分唯一索引） |
| `verify.sql` | 插入虚构数据、断言约束、软删复用、查看索引定义 |
| `VERIFICATION.txt` | 在 PostgreSQL 16.15 上执行上述 SQL 的原始输出 |

## 复现

```bash
createdb -h 127.0.0.1 -U postgres sample_ledger
psql -h 127.0.0.1 -U postgres -d sample_ledger -v ON_ERROR_STOP=1 -f schema.sql
psql -h 127.0.0.1 -U postgres -d sample_ledger -v ON_ERROR_STOP=1 -f verify.sql
```

`VERIFICATION.txt` 中依次能看到：版本信息、两条金额按分/元与 jsonb 查询结果、CHECK 约束被触发的 NOTICE（说明负金额确实被拒绝）、软删后同一 `lease_code` 可再次插入且新旧两行共存、以及 `pg_indexes` 中带 `WHERE (deleted_at IS NULL)` 的部分唯一索引定义。

## 边界

- 单机示例，未配置行级安全、分区、审计触发器或备份策略
- `char(3)` 货币码只做格式演示，未接汇率与多币种结算
- 软删不做物理清理，未实现保留期任务
- 所有 UUID、租约号与来源标记均为虚构值
