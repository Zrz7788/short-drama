# 执行制片 — 短剧流水线（总编排）

## 何时使用

整条流水线的总入口。你负责推进阶段、守住门禁、盯住预算，**不亲自做任何一个
阶段的具体工作**——每个阶段有自己的导演技能。

## 前置

| 层 | 资源 | 用途 |
|---|---|---|
| 清单 | `pipeline_defs/short-drama.yaml` | 阶段定义、门禁开关、预算 |
| 协议 | `skills/meta/checkpoint-protocol.md` | 何时停、怎么写存档 |
| 预算 | `skills/meta/cost-governor.md` | 三档模式：observe / warn / cap |
| 一致性 | `skills/meta/consistency-guard.md` | 跨镜头实体一致性 |
| 数据库 | `data/drama.db` | 全部生产数据的真源 |
| 工具 | `pipeline/stage_*.py`、`frames.py`、`assemble.py` | 各阶段的执行体 |

## 流程

### 1. 开工前先看进度，不要从零开始

```sql
-- 这一集走到哪了
SELECT * FROM v_pipeline_status WHERE episode_seq = ?;
```

按 `checkpoint-protocol` 的续跑协议：如果发现已有进度，**先告诉人从哪接**，
再动手。绝不假设是全新的一集。

### 2. 按清单顺序推进，每阶段交给对应的导演技能

| 顺序 | 阶段 | 导演技能 | 人工门禁 |
|---|---|---|---|
| 0 | 定规格 | `spec-director` | ✅ |
| 1 | 剧本 | `script-director` | ✅ |
| 2 | 实体与分级 | `cast-director` | ✅ |
| 3 | 定妆照 | `art-director` | ✅ |
| 4 | 三视图 | `art-director` | ✅ |
| 5 | 分镜 | `storyboard-director` | ✅ |
| 6 | 提示词定稿 | `prompt-director` | ❌ 自动 |
| 7 | 首帧图 | `keyframe-director` | ✅ **最关键** |
| 8 | 视频生成 | `motion-director` | 样片要人挑 |
| 9 | 抽帧衔接 | `continuity-director` | ❌ 自动 |
| 10 | 拼接成集 | `assemble-director` | ✅ |

### 3. 花钱之前必须先算账

`motion` 是唯一会大额花钱的阶段。**在它之前，预算必须已经被确认过。**

```powershell
python pipeline\stage_video.py --episode-id N --mode draft --dry-run   # 先看金额
```
然后带上 `--max-cost` 真跑。`price_book` 缺对应价目时，**拒绝在未知花费下继续**。

判据（`v_episode_cost`）：

```sql
SELECT video_cost, image_cost, total_cost FROM v_episode_cost WHERE episode_seq = ?;
```

### 4. 守门禁：呈现之后结束回合

进入有人工门禁的阶段后，做完就停——**呈现摘要、结束回合、等人回话**。
在同一轮里继续推进下游是跳门禁。

摘要必须带三样：**产出摘要 + 审查发现 + 目前花费**。

### 5. 出问题时的处置

| 现象 | 处置 |
|---|---|
| 提示词里实体描述没逐字出现 | **停下**。第 6 阶段的机械校验会报错，说明模板被改坏了 |
| 某个 main 实体缺三视图 | 回 `art-director` 补，然后**重新冻结** |
| 分镜引用了不存在的实体名 | 回 `cast-director` 补实体，或回 `storyboard-director` 改名 |
| 首帧图里角色不像定妆照 | 回 `keyframe-director` 重出，别去跑视频 |
| 成片时长与素材之和对不上 | 回 `continuity-director` 查缺失镜头 |
| 预算超了 | 停。不要"先跑完再说"——`cost-governor` 的 cap 档就是为这个 |

## 常见陷阱

- **没算账就开跑 `motion`。** 这是唯一能一次烧掉几千块的阶段
- **首帧图阶段顺手跑视频。** 那是跳门禁，而且把成本结构搞反了（见下）
- **跳过实体分级。** 把路人都判成 `main`，出图成本翻几倍且毫无收益
- **改实体描述后不重跑第 6 阶段。** 提示词里还是旧描述，跨镜头一致性就断了
- **一口气跑完十个阶段再给人看。** 那样人只能在最贵的地方做审查

## 质量门禁

- 每道人工门禁都真的停过（回合结束过）
- `motion` 之前有明确的预算确认
- 每个 main 实体在三视图产出后被冻结（`approved=1`）
- 每个镜头都有 `selected_generation_id`
- 成片时长与素材之和偏差 ≤ 0.5 秒
- `cost_ledger` 的合计与 `generation.cost` 的合计一致

---

## 门禁提醒（绑定）

进入带人工门禁的阶段（清单里 `human_approval_default: true`）后，**呈现摘要并结束回合**。
不要在同一轮里开始下一阶段。批准是**逐道**的——之前说过的"全权交给你"不覆盖这一道。
