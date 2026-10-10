# 视频导演 — 短剧流水线（draft / direct / final）

## 何时使用

首帧图门禁通过之后。

**这是唯一会大额花钱的阶段。** 在此之前必须先把预算算清楚。

## 前置

| 层 | 资源 | 用途 |
|---|---|---|
| 上游工件 | `keyframes`、`shot_prompts` | 输入图与提示词 |
| 表 | `generation` / `cost_ledger` / `price_book` | 落生成与记账 |
| 工具 | `pipeline/stage_video.py --mode draft\|direct\|final` | 生成 |
| 知识 | `knowledge/01-制作方法/抽卡策略与Draft模式.md` | **省钱机制依据** |
| 元技能 | `skills/meta/cost-governor.md` | **预算三档** |

## 三段式：把「挑选」留在便宜的那一段

本阶段有三个子阶段，**对应 Draft 工作流的三段**：

| 子阶段 | 处理谁 | 分辨率 | 单价 | 门禁 |
|---|---|---|---|---|
| `motion/draft` | `strategy='draft'` 的镜头 | **480P 样片** | ¥0.26/秒 | **人批** |
| `motion/direct` | `strategy='direct'` 的镜头 | 1080P 直出 | ¥1.45/秒 | 自动 |
| `motion/final` | 有选中样片的镜头 | 1080P 成片 | ¥1.45/秒 | 自动 |

**为什么不一键到底**：样片便宜、成片贵（差 5.6 倍）。
**把选择留在便宜的那一段**——draft 阶段可以放心多抽，final 阶段每镜只出一次。

## 花钱之前必须先算账

```powershell
# 先看金额，不提交任何任务
python pipeline\stage_video.py --episode-id 1 --mode draft --dry-run
# 带上预算硬顶再真跑
python pipeline\stage_video.py --episode-id 1 --mode draft --max-cost 200
```

### 两道花钱保护

1. **`--max-cost`**：预估超预算直接中止
2. **`price_book` 缺价目时拒绝继续**

第 2 条是硬的。**把不知道价格的调用按 0 记账然后闷头跑，
是这套系统里最可能造成真实损失的行为。**

### 输入图解析顺序

1. 该镜 `continues_from` 有值 → 用**上一镜尾帧**（仅限真实延续，见下）
2. 否则用该镜的 `keyframe_path`
3. 都没有 → 纯文生视频，会明确提示（一致性最差）

### 迭代纪律（把成本框住）

| 规则 | 说明 |
|---|---|
| 每次只改一样东西 | 否则不知道是哪一处修好的 |
| **失败 10-15 次后简化或拆分场景，而不是改措辞** | 改措辞是撞墙 |
| 在最低分辨率上探索 | 只重跑有效的 |
| **判断整条片段，绝不只看单帧** | "一张好静帧可能来自一段第 9 秒就崩掉的生成" |
| 切点落在运动上 | 不要落在静止姿势上 |
| 留一份日志：版本、改了什么、结论 | |

### 纠错，而不是重生成

单个元素错了，去**编辑**它，并说清什么变、什么不变：

```
Around the 2-second mark, change only [X] from [before] to [after].
Keep the characters, movement, camera, lighting, composition and timing unchanged.
```

## 常见陷阱

- **不看 `--dry-run` 就开跑。** 这是唯一能一次烧掉几千块的操作
- **`price_book` 缺价目还继续。** 应该拒绝
- **draft 阶段舍不得抽。** 这是最便宜的试错通道，省这里最亏
- **final 阶段反复重出。** final 每镜只该出一次
- **只看单帧就判定成败。** 要看整条片段
- **失败十几次还在改措辞。** 该简化或拆分场景了
- **忘了下载。** 视频 URL 会过期，成功后立即下载（脚本已处理）

## 质量门禁

- **跑之前有金额确认**（`--dry-run` 或 `--max-cost`）
- `price_book` 里有对应模型的价目
- 每个 `shot` 至少一条 `status='succeeded'` 的 `generation`
- 每个 `shot.selected_generation_id` 非空
- `cost_ledger` 的 video 合计 **等于** `generation.cost` 合计（账目自洽）
- 单集总花费在 `budget_default_cny` 之内，或已被显式放行
- draft 样片条数 == `strategy='draft'` 镜头的 `draws_planned` 之和

---

## 门禁提醒（绑定）

**只有 `motion/draft` 有人工门禁**——人挑中哪条样片，是这里唯一需要判断的事。
`direct` 与 `final` 是机械的，自动放行。

批准：`python pipeline\run.py --episode-id N --approve motion/draft --note "选 a2/a4"`

跑 `final` 之前务必确认样片已经挑过——**没挑就直接出成片，等于把选择权交给了脚本。**
