# 配对比较表（002）

## 背景
两组接到的单结构不同，直接比较整体超时率容易误判。新增“配对比较”：从两份**已复核报告**的冻结底稿中选样本，按同优先级、开单时间相隔不超过 30 分钟配对，每条样本只用一次，再分别计算两组在**配对样本**中的超时率。

## 新增表
`comparisons`（SQLAlchemy 启动自动创建）：

| 列 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 主键 |
| name | String(120) | 比较名称 |
| run_a_id / run_b_id | Integer | 引用两份已复核 `sla_runs` |
| status | String(20) | queued / running / completed / failed，与报告状态机一致 |
| generation | Integer | 代次，初始 1；每次改选 +1，旧代次计算结果不得覆盖新代次 |
| progress | JSON | 分批检查点：next_batch、carry_a/b、pairs（行号+时间差）、失效计数、finished |
| result | JSON | 配对统计、两组配对样本超时率、口径依据、warnings |
| error | Text | 失败原因 |
| published | Boolean | 发布闸门：配对完成且两组配对样本超时率齐备（配对数>0）才为 true |
| created | Float | 创建时间戳 |

## 升级方式
- 全新库：`Base.metadata.create_all` 自动建表，无需手动操作。
- 已有 PostgreSQL 库：手动执行
  ```sql
  CREATE TABLE comparisons (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120),
    run_a_id INTEGER,
    run_b_id INTEGER,
    status VARCHAR(20) DEFAULT 'queued',
    generation INTEGER DEFAULT 1,
    progress JSON,
    result JSON,
    error TEXT,
    published BOOLEAN DEFAULT FALSE,
    created DOUBLE PRECISION
  );
  ```
- 不改动既有表；正式报告（sla_runs）链路与配对比较完全隔离，报告照常提交、核算、复核与定时交付。

## 任务队列
新增 Redis 列表 `comparison-jobs`，消息体 `comparison_id:generation`，与原 `report-jobs` 分开。worker 启动时把中断在 running 的比较任务按当前代次重新入队，从 `progress` 检查点续跑。
