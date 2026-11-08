# Dictionary & Tag Center — 参考实现

一个零第三方依赖的字典 / 标签中心服务，用 Python 标准库实现数据模型、业务规则、HTTP 接口与测试。目标是让每条设计决策都能被一条可运行的断言验证。

## 能力

| 能力 | 说明 |
|---|---|
| 字典与层级 | 字典、标签树、父子约束、环路与深度限制 |
| 草稿 / 启用 | 标签状态流转，停用后不可再被引用 |
| 版本与历史 | 字段级变更留痕，可查询单个标签的完整历史 |
| 合并 | 把源标签的引用迁移到目标标签，按 `(object_type, object_id, tag_id)` 去重，整批可回滚 |
| 停用 | 存在启用子标签时拒绝（409），强制走合并流程 |
| 提案与复核 | AI / 用户来源的提案要求证据，禁止自审自批，支持批量审批与逐条失败隔离 |
| 批量导入 | 先 dry-run 出 added / updated / conflicts / errors，再 apply；同文件二次导入幂等 |
| 树缓存 | 树接口带 ETag 与 `Cache-Control`，`If-None-Match` 命中返回 304 |
| 公开只读 | 未登录 401；登录后只返回 `code / name / children`，剥离定义与统计字段 |

## 文件

| 文件 | 作用 |
|---|---|
| `taxonomy.py` | 核心服务，约 560 行；8 张表、部分唯一索引、outbox、故障注入开关 |
| `api.py` | 标准库 `http.server` 的 14 条路由，支持角色头与 ETag |
| `test_taxonomy.py` | 21 个 unittest 用例 |
| `demo.py` | 端到端演示，打印真实统计数字（含故障注入回滚） |
| `smoke_test.py` | 起本地服务发真实 HTTP 请求，逐条打印 method / path / status |
| `VERIFICATION.txt` | 三条命令的真实输出汇总 |
| `unittest_output.txt` / `demo_run_output.txt` / `smoke_output.txt` | 原始输出文件 |

## 运行

需要 Python 3.9+，不需要安装任何包。

```bash
python -m unittest test_taxonomy -v     # Ran 21 tests ... OK
python demo.py                          # 端到端演示
python smoke_test.py                    # 19 条 HTTP 请求
```

单独起服务：

```bash
python api.py 8111
curl -H "X-Role: knowledge_admin" http://127.0.0.1:8111/api/taxonomy/dicts
```

## 几个关键取舍

**引用迁移不硬编码业务表。** 所有引用统一登记在 `ref_registry`，合并时按 `object_type → table_name` 解析。新增业务对象类型不需要改合并逻辑。

**唯一键建在引用侧。** `(object_type, object_id, tag_id)` 唯一，使「同一对象同时挂着源标签和目标标签」在合并时天然去重，不需要额外的补数逻辑。

**事件写 outbox，不在事务内投递。** 合并过程中若注入故障导致回滚，事件记录也随之消失，不会出现「数据没变但事件已经发出去」的不一致。

**批量操作内层不提交。** 批量审批用 SAVEPOINT 做逐条失败隔离，内层 helper 若自行 `commit()` 会提前释放 savepoint，因此用 `_defer` 标志统一到最后提交。

**导入必须幂等。** 创建标签时把 `definition` / `aliases` 一并写入，否则同一文件二次 dry-run 会误报「有更新」。

## 边界

- 角色由 `X-Role` 请求头模拟，不是真实认证；公开接口用 `Authorization` 是否存在判断是否登录
- sqlite 单连接、`check_same_thread=False` 以适配 `ThreadingHTTPServer`，未做连接池与并发压测
- outbox 只落库，没有投递器
- 无前端页面、无迁移工具、无监控接入
- 演示数据全部为虚构字符串与 UUID

## 验证结果

`VERIFICATION.txt` 记录了一次完整运行：21 项单测全绿；演示输出包含合并 `moved / dedup` 统计、导入幂等（第二次 `created=0, updated=0`）与故障回滚；HTTP 冒烟覆盖 200 / 304 / 401 / 403 / 409 等状态码与缓存头。
