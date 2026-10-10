# 美术导演 — 短剧流水线（定妆照 + 角色表）

## 何时使用

实体分级之后。给需要的实体出参考图，**并把它们冻结成后续所有镜头的一致性锚点**。

> 原话（来自 Seedance 制作方法）：**"The piece is won or lost here, before any
> video generates."** 片子在这里就赢了或输了——在生成任何视频之前。

本阶段有两个子阶段：`art/portrait`（定妆照）与 `art/turnaround`（角色表）。
**它们是两道独立的门。**

## 前置

| 层 | 资源 | 用途 |
|---|---|---|
| 上游工件 | `entity_roster` | 要出图谁、出到什么程度 |
| 表 | `entity_ref` / `image_generation` | 落参考图与生成记录 |
| 工具 | `pipeline/stage_refs.py --phase portrait\|turnaround` | 出图 |
| 提示词 | `pipeline/prompts/portrait.md`、`turnaround.md` | 交给模型的指令 |
| 知识 | `knowledge/01-制作方法/角色资产法.md` | **方法依据（必读）** |

## 流程

### 子阶段一：定妆照（portrait）

给 `main` + `secondary` 出。**这是一致的锚点，会被 60-100 集反复引用。**

**图省这点钱最亏**——一张 ¥0.2，而它锚定的是全片每一个出现该角色的镜头。

#### 10 次法则（验收标准）

> **接受一个资产之前，用不同姿势和光照生成 10 次。它必须在 10 次里都认得出来。**

| 层级 | 抽检次数 | 理由 |
|---|---|---|
| `main` | **10 次** | 全片一致性的锚点 |
| `secondary` | 3-5 次 | 出场少，风险低 |

成本账：10 张 ≈ ¥2。而一次崩脸返工的是它出场的**所有**镜头，每条视频 ¥4-5.8。
**这个杠杆极其不对称——10 次法则的真正含义是"在最便宜的地方做到不放心为止"。**

```powershell
python pipeline\stage_refs.py --episode-id 1 --phase portrait
python pipeline\stage_refs.py --episode-id 1 --phase portrait --take 3   # 多出几张挑
```

### 子阶段二：角色表（turnaround）

**⚠️ 这里要改掉一个常见做法。** 直觉是出 front / side / back 三视图、
每张都带脸。但更有效的做法是：

| # | 图 | 关键 |
|---|---|---|
| 1 | 全身正面，**把脸去掉** | 脸换成空白/中性头部 |
| 2 | 全身背面 | |
| 3 | 3/4 特写，**带脸** | 做**两个版本：微笑 与 中性** |

**为什么全身图要去掉脸**：远景里脸又小又糊，**模型会复制那个糊**。
如果整套参考里唯一清晰的脸就是那张特写，**它就会用那张**。

**为什么必须有微笑版**：没有微笑版本，**模型会自己发明牙齿**。

只给 `main` 出。**必须以定妆照为参考图生成**（图生图），否则不是同一个人。

```powershell
python pipeline\stage_refs.py --episode-id 1 --phase turnaround
```

### 冻结（本阶段最重要的动作）

人审通过后，把 `entity_ref.approved=1`，并指定 `is_primary=1` 的那张为锚点。

**冻结之后禁止重出。** 没有这个动作，第 30 集有人重出一次定妆照，
后面全部崩——而且要很久才发现。

```sql
SELECT e.name, r.view, r.approved, r.is_primary FROM entity_ref r
  JOIN entity e ON e.id = r.entity_id WHERE e.project_id = ? ORDER BY e.name, r.view;
```

### 场景怎么办

场景（`location`）**不要只出一张正面静止图**。

> **"静止图给你一堵墙，缓慢移动的镜头给你几何结构。"**

短剧的镜头要在同一空间里从不同角度拍。只有一张正面图，模型换个角度就是
**重新想象一个空间**——观众未必说得出哪里不对，但会觉得"假"。

| 场景类型 | 做法 | 成本 |
|---|---|---|
| **核心场景**（占多数镜头的那 2-4 个空间） | 缓慢移动的空镜**视频** | 用 Draft 样片档出，约 ¥1 |
| 一次性场景（走廊、楼梯） | 静止图即可 | ¥0.2 |

核心场景那段空镜直接走 **motion 的 draft 档（480P）**就够了——看几何结构不需要 1080P。

## 常见陷阱

- **跳过角色表。** "定妆照看着够了"——侧脸、背面一旦出现就崩
- **全身图带脸。** 模型会复制远景里那张糊脸
- **没有微笑版。** 模型自己发明牙齿
- **三视图不以定妆照为参考图。** 那不是同一个人
- **审完不冻结。** 后面任何人能重出，一致性没有保障
- **所有场景都出静止图。** 换角度就变空间
- **给 `background` 级出图。** 纯浪费

## 质量门禁

- `main` 级有 portrait + 角色表三张，且全部 `approved=1`
- `main` 级每张有 `is_primary=1` 的锚点图
- `secondary` 级有 portrait
- `background` 级**无图**
- 角色表的生成请求里带了定妆照作为参考图（`image_generation` 有记录）
- `main` 级定妆照抽检 ≥ 10 张，且人确认"10 张都认得出"
- 核心场景有缓慢移动的空镜视频，不是静止图

---

## 门禁提醒（绑定）

两个子阶段**各自有人工门禁**。审完 `portrait` 才能进 `turnaround`。

批准：`python pipeline\run.py --episode-id N --approve art/portrait --note "..."`

**冻结动作和批准动作是同一件事的两面**——批准了就要落 `approved=1`。
