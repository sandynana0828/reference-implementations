# Reference Implementations

一组**可直接运行**的后端参考实现。每个示例都能在本地复现，并附带命令的原始输出，用来说明「代码 → 测试 → 证据」这条链路是怎么做的。

- 无第三方 Python 依赖：核心服务只用标准库（`sqlite3`、`http.server`、`unittest`）
- 第二个示例使用本机 PostgreSQL 16，验证真实 SQL 行为
- 所有数据均为虚构示例，不含任何客户、企业或个人信息，也不含凭据

## 示例

| 示例 | 内容 | 验证结果 |
|---|---|---|
| [`taxonomy-service/`](taxonomy-service/) | 字典与标签中心：层级、版本历史、标签合并与引用迁移、停用、提案复核、批量导入、树缓存、公开只读接口 | 21 项单测全绿、19 条 HTTP 请求状态码符合预期 |
| [`postgres-ledger/`](postgres-ledger/) | PostgreSQL 押金台账模型：金额按分、jsonb、软删可复用业务键的部分唯一索引、CHECK 约束 | 真机 psql 执行输出 |

## 快速复现

```bash
cd taxonomy-service
python -m unittest test_taxonomy -v     # 21 tests, OK
python demo.py                          # 端到端演示，打印真实统计数字
python smoke_test.py                    # 起本地 HTTP 服务，逐条打印状态码
```

`taxonomy-service/VERIFICATION.txt` 是上述三条命令的真实输出汇总；`postgres-ledger/VERIFICATION.txt` 是 psql 的真实执行输出。两者都是运行后直接保存，未手工改写。

## 验证方式

- 单元测试覆盖状态流转、权限、冲突、回滚、幂等等边界，而不是只测正常路径
- HTTP 层用真实请求打本地服务，断言状态码、ETag / 缓存头、错误码
- 数据库行为用真实 PostgreSQL 实例执行 DDL / DML，检查约束与索引是否生效
- 故障注入（例如合并过程中写表失败）用于证明事务回滚，而不是口头声明

## 边界说明

这些是**参考实现**，刻意保持小而可读：

- 认证用请求头模拟角色，未接入真实身份系统
- 单进程 sqlite，未做多实例并发与水平扩展
- 事件写入 outbox 表，未实现投递器
- 不包含前端、部署脚本与监控接入

这些限制在每个示例的 README 里都会单独列出，避免把参考实现误当成生产系统。
