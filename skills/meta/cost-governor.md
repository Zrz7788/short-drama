# 预算治理 — 元技能

## 何时使用

**任何会花钱的阶段之前。** 具体说：`art`（出参考图）、`keyframes`（出首帧）、
`motion`（出视频）。其中 `motion` 是唯一能一次烧掉几千块的阶段。

## 三档模式

| 模式 | 行为 | 什么时候用 |
|---|---|---|
| `observe` | 只记账，不拦 | 第一次试跑，想先看真实消耗 |
| `warn` | 超阈值警告，继续 | 常规生产 |
| `cap` | 超过硬顶直接中止 | **默认。** 尤其在第一集 |

硬顶来自清单：`orchestration.budget_default_cny: 200`（单集）、
`hard_cap_cny: 400`。整部剧按集数乘。

## 协议

### 1. 花钱之前必须先估

```powershell
python pipeline\stage_video.py --episode-id N --mode draft --dry-run
```

没看到金额之前不要真跑。`--dry-run` 会给出条数与金额，不提交任何任务。

### 2. 单价只有一个真源：`price_book`

不允许把单价硬编码在别处。查不到价目时：

- **`art` 与 `keyframes`**：成本记 0，但要**明确警告**并展示张数
- **`motion`**：**拒绝继续**。原话是"拒绝在未知花费下继续"——
  视频最贵，宁可不跑，也不能在不知道单价的情况下批量提交

这条规则是硬的。把不知道价格的调用按 0 记账然后闷头跑，是这套系统里
最可能造成真实损失的行为。

### 3. 每阶段结束给花费快照

```sql
SELECT video_cost, total_cost FROM v_episode_cost WHERE episode_seq = ?;
SELECT category, ROUND(SUM(amount), 2) FROM cost_ledger
 WHERE episode_id = ? GROUP BY category;
```

在**要向人请示的门禁**上，摘要里必须带：

```
### 目前花费
已花 ¥[X] / 预算 ¥[Y]    图片 ¥[a]  视频 ¥[b]
下游还预计需要 ¥[c]（[依据]）
```

人在批准昂贵下游之前有权知道这些。

### 4. 账目必须自洽

`cost_ledger` 的合计与 `generation.cost`+`image_generation.cost` 的合计必须相等。
对不上说明有调用没记账，或者记了两次。

```sql
SELECT
  (SELECT COALESCE(SUM(cost),0) FROM generation) +
  (SELECT COALESCE(SUM(cost),0) FROM image_generation) AS 记录侧,
  (SELECT COALESCE(SUM(amount),0) FROM cost_ledger)      AS 流水侧;
```

## 单位与口径（容易搞混的地方）

- `cost_ledger.unit`：`second`（视频按秒）/ `image`（图片按张）/ `ktoken`
- **视频按 `shot.duration_s` 计费，不是按实际素材时长**。我们的假 Ark 测试里
  素材只有 1 秒但账单按 4 秒算——这是对的，真实 API 也按请求的 duration 收费
- Draft 样片与成片**单价不同**：¥0.26/秒 vs ¥1.45/秒（见 `price_book.variant`）

## 一键省钱的三条（按杠杆排序）

1. **把挑选留在便宜的那一段。** 在首帧图上抽卡（¥0.2/张）比在视频上抽
   （¥4-5.8/条）便宜 20-30 倍。详见 `knowledge/01-制作方法/抽卡策略与Draft模式.md`
2. **按镜分策略。** 正脸特写 `draft` 抽 4-6 次，空镜 `direct` 抽 1-2 次。
   全片一刀切是同时浪费钱和毁质量
3. **实体分级要克制。** `background` 级不出图。把它当 `main` 出全套素材，
   等于为路人付 4 倍的钱

## 常见陷阱

- **不看 `--dry-run` 就开跑 `motion`。** 这是唯一能一次烧掉几千块的操作
- **`price_book` 缺价目还继续跑视频。** 应该拒绝
- **只在最后算总账。** 那时候已经晚了，应该每阶段都看
- **把 `cost_ledger` 当唯一真源去改。** 它是流水；`generation.cost` 是调用记录。
  两者要一致，但不该手工改其中一边

## 质量门禁

- 每个花钱阶段之前有金额确认（`--dry-run` 或 `--max-cost`）
- `motion` 之前 `price_book` 有对应模型的价目
- 每道向人请示的门禁，摘要里都有花费快照
- `cost_ledger` 与调用记录的合计一致
- 单集总花费在 `budget_default_cny` 之内，或已被显式放行
