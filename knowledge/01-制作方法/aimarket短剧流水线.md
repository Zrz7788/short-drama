# aimarket 的 AI 短剧流水线（一个反例）

> **出处**：`github.com/sev7n4/aimarket`（中文 AI 修图与电商设计平台，TypeScript）
> 提交 `06dff5f`（2026-06-15，PR #184）：
> `feat(drama): AI 短剧 Agent 流水线（Anchor First + 分镜制作）`
> 「LLM/规则分镜规划、角色三视图定稿、关键帧 i2v、TTS/口型/剪辑 mock 流水线、
> 创作台分镜预览 UI」，35 个文件 +3,169 行。
>
> **为什么要记它**：它的**架构选择值得看**（Anchor First、VLM 质检循环），
> 但它的**质检实现是个典型反例**——我们差点就可能写出同样的东西。

---

## 一、它的流水线结构

```
lib/drama/planner.ts          357 行   分镜规划（LLM + 规则）
lib/drama/executor.ts         631 行   执行
lib/drama/prompt-builders.ts  126 行   提示词组装
lib/drama/schema.ts           180 行   数据模型
lib/drama/estimate.ts          39 行   成本估算
lib/drama/runs.ts             219 行   运行记录
providers/drama/registry.ts    55 行   提供方注册
providers/vlm/character-audit.ts 67 行 ⭐ **用 VLM 审核角色一致性**
routes/drama.ts               248 行   接口
components/drama-studio-panel.tsx     创作台 UI
```

**「Anchor First」**：先把角色锚点（anchor）定稿，再做分镜。
提交里对应的是「**角色三视图定稿**」。

⚠️ **它用三视图**——这正是我们已证伪的（官方明确说多视角参考会让模型看到
多个主体 → "双胞胎问题"；三个独立来源一致）。**这条不要抄。**

---

## 二、⭐⭐ 反例：一个"失败即通过"的质检

`character-audit.ts` 的核心（原文，注释是我加的）：

```ts
const charMin  = input.characterMinScore ?? 75;
const styleMin = input.styleMinScore  ?? 70;

// ⚠️ 路径一：VLM 没开、或没有参考图 → 直接通过，还给了个 80 分
if (!isAgentVlmEnabled() || !input.referenceUrls.length) {
  return { pass: true, characterScore: 80, styleScore: 80,
           reason: "VLM 未启用或无参考图，默认通过", provider: "skip" };
}

try {
  const qc = await runVlmQualityCheck({ ... });

  // ⚠️⚠️ **分数是编的** —— 把布尔值 qc.pass 映射成硬编码常数
  const characterScore = qc.pass ? 78 : 65;
  const styleScore     = qc.pass ? 72 : 60;
  const pass = characterScore >= charMin && styleScore >= styleMin;

  return { pass, characterScore, styleScore,
           reason: pass ? `角色 ${characterScore} / 风格 ${styleScore}`
                        : `一致性不足：角色 ${characterScore}（需≥${charMin}）…` };
} catch {
  // ⚠️ 路径三：VLM 报错 → 通过，而且 75 正好卡在阈值上
  return { pass: true, characterScore: 75, styleScore: 70,
           reason: "VLM 审计失败，默认通过", provider: "fallback" };
}
```

### 为什么这是个反例

**它看起来像一套质检**——有分数、有阈值、有理由、有提供方。
**但它只做了一件事：把 VLM 的布尔结果 `qc.pass` 转成了文字。**

| 情形 | 结果 |
|---|---|
| VLM 说通过 | 78 ≥ 75 ✓ **通过** |
| VLM 说不通过 | 65 < 75 ✗ 不通过 |
| **VLM 没启用** | 80 ✓ **通过** |
| **VLM 报错** | 75 ✓ **通过**（正好卡在阈值） |

**三个问题**：

1. **分数是假的。** `qc.pass ? 78 : 65` —— 这不是"角色一致性得分 78"，
   这是"布尔值被渲染成了 78"。**它制造了精确的假象。**
   `reason` 里还会写「角色 78 / 风格 72」——**读起来像量出来的。**

2. **失败即通过（fail-open）。** 质检跑不了的时候，答案不是"通过"，是"**不知道**"。
   而这个实现里，**关掉 VLM 比打开 VLM 更容易通过**（80 分 vs 真实判定的 65）。
   **这给了系统一个"把质检关掉"的隐性激励。**

3. **catch 里 75 分正好等于 `charMin` 的默认值。** 一个默认值恰好卡在
   另一个默认值的阈值上——**说明阈值和兜底是分别拍的，没人对着看过。**

### ⭐ 对我们的意义

**我们没有 VLM 质检**，所以不存在这个 bug。**但我们要加的话，必须避开这三点。**

| 该做 | 不该做 |
|---|---|
| 质检跑不了 → 记 **unknown**，不记 pass | 质检跑不了 → 默认通过 |
| 分数要么真量出来，要么**不写分数** | 把布尔值渲染成分数 |
| 阈值和兜底**放在一起定** | 阈值 75、兜底 75，各拍各的 |

**而且注意**：我们在另一个地方做对了——`stage_refs.py`／`stage_video.py` 的
「**价目缺失就拒绝继续**」是 **fail-closed**。
**同一个系统里，质检该 fail-closed（宁可拦住），而不是 fail-open（宁可放行）。**

---

## 三、⭐ 但「用 VLM 审核角色一致性」这个**想法本身**我们该有

抛开上面那个实现，**它的意图是对的**：

> 拿**参考图** + **生成的图**，交给一个**视觉模型**去判断
> 「这是不是同一个角色、风格是否一致」，并给出通过/不通过。

**我们现在完全没有这一步。** 我们的第 3/4 阶段出完参考图之后：
- `v_entity_readiness` 只检查**字段填没填**（有没有 fullbody、有没有 headshot）
- **没有任何一步检查"这张图里的人，是不是我们要的那个人"**

**这正是"定妆压力测试"要解决的问题**（见 `Muse流水线评审.md` §四）：
> 三条放一起**一眼认得出是同一角色**，才派发依赖它的镜头。

**一个是靠人眼看，一个是靠 VLM 看。两者可以并存**：
VLM 先筛掉明显不一致的，人再定夺。

**实现要点（吸取上面的教训）**：
- VLM 给出的**原文理由**要落库，不要只存一个布尔
- **不编分数**；要排序就让 VLM 输出排序或成对比较
- VLM 不可用时**记 unknown**，任务标记为"未经审核"，
  而不是"已通过"——**由人来决定要不要在未审核状态下继续**
- 把「参考图 sha256」一起存进去（见 `dsh-film插件精华.md` §四），
  否则审的是旧图

---

## 四、可以直接进我们流程的改动

| 我们的现状 | 应该改成 | 依据 |
|---|---|---|
| 没有角色一致性审核 | 加**可选**的 VLM 审核（参考图 vs 生成图），**fail-closed 或记 unknown** | §三 |
| `v_entity_readiness` 只看字段齐不齐 | 字段齐 ≠ 图对。加一层**视觉**判定 | §三 |
| （如果将来加质检） | **不编分数**；不可用记 unknown 不记 pass；阈值与兜底一起定 | §二 |
| — | ⚠️ **不要抄它的三视图** | §一 |
