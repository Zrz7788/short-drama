# 仿真人短剧 · 项目工程底座

用火山方舟（Ark）的 Seedance / Seedream 生产 9:16 竖屏仿真人短剧的工程仓库。

## 目录

```
knowledge/          知识库：方法与依据（人读）
  01-制作方法/        全流程总览、配音与口型架构、角色资产法、剧本规范、结构模板、
                      角色一致性 SOP、抽卡策略、分镜表规范
  02-模型与成本/      模型 ID 与单价、单集成本模型
  03-项目档案/        每部剧的设定
  04-复盘/            实测数据回流，用来修正 01 和 02
db/                 数据库
  schema.sql          表结构唯一真源（15 表），纯加法
  views.sql           索引与视图，必须最后执行
  init_db.py          建库 / 增量迁移 / 写入价目
  selftest.py         schema 自检：插模拟数据、验算视图、整体回滚
  export.py           导出纯文本 SQL 便于进 git
pipeline/           管线阶段脚本
  common.py           共用层：路径、数据库连接、ffmpeg 调用
  llm.py              LLM 共用层：OpenAI 兼容调用、JSON 抽取、结构校验
  ark.py              Ark 客户端：图片（同步）与视频（异步任务）
  prompts/            提示词模板（script / entities / storyboard / shot_prompt
                      / portrait / turnaround）
  stage_script.py     第 1 阶段：生成剧本（含版本管理）
  stage_entities.py   第 2 阶段：提取人物/场景/道具并分级
  stage_refs.py       第 3/4 阶段：人物/场景/道具素材（--phase 区分）
  stage_storyboard.py 第 5 阶段：生成分镜（含按镜抽卡策略）
  stage_prompts.py    第 6 阶段：提示词定稿（确定性组装，不走 LLM）
  stage_firstframes.py 第 7 阶段：生成首帧图（关键帧）
  stage_video.py      第 8 阶段：视频生成（draft / direct / final 三种模式）
  frames.py           第 9 阶段：抽首尾帧、建立镜头续接关系
  assemble.py         第 10 阶段：拼接成集、可选烧字幕
  selftest.py         管线端到端自检（合成素材 + 临时库）
  selftest_llm.py     第 1/2/5/6 阶段自检（本地假模型服务 + 临时库）
  selftest_ark.py     第 3/4/7/8 阶段自检（本地假 Ark 服务 + 临时库）
  selftest_e2e.py     全链路集成测试（十阶段串起来，产出真 MP4）
pipeline_defs/      流水线清单（声明式契约）
  short-drama.yaml   阶段、工件、工具、门禁、判据 —— 我们唯一那条流水线
skills/             流水线自己的技能目录（**不是** .dsh/skills）
  pipelines/short-drama/  各阶段导演技能 + 总编排
  meta/                   门禁协议、审查、预算、一致性
data/                数据库文件与导出（*.db 不进 git）
.dsh/skills/         智能体技能
  ark-seedance/      Ark 调用参考（模型 ID、单价、Draft、避坑）
tools/              工具
  setup_git.py       记录 MinGit 的获取方式，保证可复现
  setup_ffmpeg.py    下载便携版 ffmpeg
  validate_manifest.py  流水线清单校验（薄封装，解析器在 pipeline/manifest.py）
  install_skill.py   技能获取与安装（带质量门禁：硬性拒绝 + 提示）
  check_ark.py       Ark 配置检查（零成本：故意发错参数看错误码，区分密钥错与权限没开）
  verify.py          全量验收（含全链路集成）
  verify_ffmpeg.py   ffmpeg 功能验证（编码器/滤镜/中文字幕）
.tools/              便携工具链，不进 git
  mingit/            Git 2.56.0（免安装版）
  ffmpeg/            FFmpeg n9.0（免安装版）
  ssh/               GitHub SSH 密钥
```

## 快速开始

```powershell
$git = "$PWD\.tools\mingit\cmd\git.exe"

# 全量验收：git + 安全 + 知识库 + 数据库 + 管线 + 全链路，共 33 项检查
python tools\verify.py

# 建/校验数据库（含增量迁移）
python db\init_db.py

# schema 自检：模拟一集数据、验算视图、回滚
python db\selftest.py

# 管线：抽帧与衔接，再拼接成集
python pipeline\frames.py --episode-id 1
python pipeline\assemble.py --episode-id 1

# 导出文本备份
python db\export.py

# git 操作
& $git status
& $git log --oneline
```

## 本机环境要点

### 1. Git 是便携版，不在 PATH 上

系统上原本没有 git，`winget` 也是坏的（Store 别名桩，退出码非零）。
已将官方 MinGit 2.56.0 解压到 `.tools/mingit/`，用绝对路径调用：

```
.tools/mingit/cmd/git.exe
```

要让它上 PATH（新开终端生效）：

```powershell
[Environment]::SetEnvironmentVariable('Path',
  "$env:Path;$PWD\.tools\mingit\cmd", 'User')
```

### 2. git 的远程操作需要提权（重要）

受限沙箱下 **MinGit 的所有远程操作都会失败**：

```
fatal error - couldn't create signal pipe, Win32 error 5
fatal: Could not read from remote repository.
```

原因是 MinGit 基于 MSYS2，`git push/fetch/clone/ls-remote` 都要起子进程
（`ssh.exe` / `git-upload-pack`），而 MSYS 起子进程需要创建信号管道——
沙箱禁止命名管道，于是直接失败。**这不是网络问题**，SSH 链路本身是通的。

| 操作 | 受限模式 | 提权后 |
|---|---|---|
| `init` / `add` / `commit` / `log` / `status` | ✅ 正常 | ✅ 正常 |
| `push` / `fetch` / `clone` / `ls-remote` | ❌ 信号管道失败 | ✅ 正常 |

**本地版本控制完全可用**，只有同步到远程时需要更宽的权限。

**三种处理方式**（按推荐度）：

**1. 切换会话权限模式（推荐）**

`dsh-sandbox-policy` 的 `mode` 可在运行时通过 GUI 的策略控件切换，切换会记入会话日志、
跨重启保留，每个会话独立。预设对应关系（取自 base bundle 的 `cordis.patch.yml`）：

| 预设 | 沙箱模式 | 审批 |
|---|---|---|
| `read-only` | 拒绝写入 | 每次询问 |
| `workspace-write` | 只写工作区 | 每次询问 ← 当前 |
| `danger-full-access` | 不隔离 | 不询问 |

切到 `danger-full-access` 后 `push` 不再需要批准。

> ⚠️ **代价是 agent 不再受限**：文件写入失去边界，命令执行不再询问。这是拿隔离换便利，
> 请自行权衡。如果只想推代码，用完切回 `workspace-write` 更稳。

**2. 启动时用环境变量**

base bundle 里 `mode` 的取值是 `process.env.DSH_PERMISSION_MODE ?? 'workspace-write'`，
`approval` 的 policy 也由同一个变量决定。即用 `DSH_PERMISSION_MODE=danger-full-access`
启动 DSH 可达到同样效果。

**3. 保持现状，每次 push 批准一次** —— 最安全，代价是点一下批准。

**已实测**：提权到 `danger-full-access` 后，`git push -u` 与 `git clone` 全流程正常
（远程收到 3 个提交、20 个文件，clone 回读一致）。

### 3. 调 API 不能用 curl / Invoke-WebRequest

沙箱下这两个走 Windows schannel，会报 `SEC_E_NO_CREDENTIALS`，TLS 握手失败。
**用 Python 或 Node 发请求**（走 OpenSSL，实测正常）。

### 4. PowerShell 控制台中文乱码

跑脚本前加：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 5. ffmpeg 是便携版，同样不在 PATH 上

官方 BtbN 构建（FFmpeg n9.0，GPL 版，含 libx264 与 libass），解压在 `.tools/ffmpeg/`：

```
.tools/ffmpeg/bin/ffmpeg.exe
.tools/ffmpeg/bin/ffprobe.exe
```

已清掉 159 MB 的 `ffplay.exe`（用不到）。重新获取：`python tools\setup_ffmpeg.py`

**验证**：`python tools\verify_ffmpeg.py` —— 不只查版本，而是真合成视频、真抽尾帧、
真拼接、真烧中文/ASCII 字幕，共 17 项。已全部通过。

## 管线阶段脚本

整条生产流程与脚本的对应关系。**生成环节（3/4/7/8）待 `ARK_API_KEY`**，
其余阶段现在就能跑。

| 阶段 | 命令 | 产出 | 状态 |
|---|---|---|---|
| 0 定交付规格 | 手工填 `project` | 分辨率/时长 | — |
| **1 生成剧本** | `python pipeline\stage_script.py --episode-id N --brief "…"` | `script`、`scene` | ✅ |
| **2 提取实体+分级** | `python pipeline\stage_entities.py --episode-id N` | `entity` | ✅ |
| 3 人物素材 | `python pipeline\stage_refs.py --episode-id N --phase characters` | `image_generation`、`entity_ref(portrait)` | ✅ |
| 4 场景素材 | `python pipeline\stage_refs.py --episode-id N --phase scenes` | `entity_ref(front/side/back)` | ✅ |
| **5 生成分镜** | `python pipeline\stage_storyboard.py --episode-id N` | `shot`、`shot_entity` | ✅ |
| **6 提示词定稿** | `python pipeline\stage_prompts.py --episode-id N` | `shot.prompt`、`negative_prompt` | ✅ |
| 7 首帧图 | `python pipeline\stage_firstframes.py --episode-id N` | `shot.keyframe_path` | ✅ |
| 8 视频生成 | `python pipeline\stage_video.py --episode-id N --mode draft\|direct\|final` | `generation` | ✅ |
| **9 抽帧与衔接** | `python pipeline\frames.py --episode-id N` | `*_first/last.png`、`continues_from` | ✅ |
| **10 拼接与字幕** | `python pipeline\assemble.py --episode-id N [--srt F]` | `out/E0N.mp4`、`asset` | ✅ |

### 第 1/2/5/6 阶段的 LLM 配置

`pipeline/llm.py` 用 urllib 直接打 `/chat/completions`，**不引任何第三方依赖**，
所以同一套代码可指向火山方舟、DeepSeek 或任何 OpenAI 兼容网关。

```powershell
$env:LLM_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'   # 默认值
$env:LLM_API_KEY  = '...'                                        # 未设时回退 ARK_API_KEY
$env:LLM_MODEL    = '...'                                        # 必填，不设默认值
```

**`LLM_MODEL` 刻意没有默认值**——猜错模型 ID 会让人在无关的地方排查半天，
不如直接报错说清楚缺什么。

两种驱动方式（可混用）：

```powershell
# A. 直接调模型
python pipeline\stage_script.py --episode-id 1 --brief "一个关于化验单的悬疑短剧"

# B. agent 驱动：跳过调用，读入外部产出的 JSON
python pipeline\stage_script.py --episode-id 1 --brief "…" --from-json result.json

# 只看会发出去的提示词，不调用
python pipeline\stage_script.py --episode-id 1 --brief "…" --dump-prompt
```

**实体分级是第 2 阶段的核心产出**，它直接决定要出多少张图：

| tier | 出图 | 判据 |
|---|---|---|
| `main` | 人物：大头照+全身照（2 张）；场景：正视+站位+俯视（3 张） | 是否需要在多个镜头里保持长得一样 |
| `secondary` | 只出定妆照（1 张） | |
| `background` | 不出图 | 路人、一次性道具 |

脚本跑完会按 `price_book` 里的图片单价，直接报出"预计要出多少张、约多少钱"。

### 第 3/4 阶段的要点

两个阶段共用一个脚本（`stage_refs.py`），用 `--phase` 区分——它们的流程完全相同，
只是「给谁出图」和「出哪些视角」不同：

| phase | 对应阶段 | 给谁出 | 视角 |
|---|---|---|---|
| `portrait` | 第 3 阶段 | `main` + `secondary` | `portrait`（定妆照） |
| `turnaround` | 第 4 阶段 | **只有 `main`** | `front` / `side` / `back` |

分级在这里兑现成钱：`main` 出 4 张、`secondary` 出 1 张、`background` 出 0 张。

**派生视角以基底视角为参考图生成（图生图）**，这是刻意的：纯文生图无法保证
是同一个实体。基底是锚点，派生从锚点长出来。顺序是「全身照 → 大头照」
「正视图 → 俯视图/站位图」。

⚠️ **不出三视图。** 官方明确：三视图/多视图当参考素材会让模型识别成多个不同
主体，直接引发双胞胎问题。人物只要「大头照 + 全身照」两张。

其他行为：

- **幂等**：已存在的视角不重复出图，要重出加 `--force`
- **记账**：每次调用写 `image_generation`，并按 `price_book` 的图片单价写 `cost_ledger`
- **失败也留痕**：调用失败会写一条 `status='failed'` 的 `image_generation` 记录，
  带上错误信息，而不是静默跳过
- `--dry-run` 先算张数与金额，不花钱

**Ark 客户端（`pipeline/ark.py`）的字段名不是猜的**：请求体与响应结构取自一个
真实跑通的第三方实现。但**视频任务的响应结构没有对着真实服务验证过**（没有 key），
所以解析一律防御式——取不到预期字段时把**原始响应落盘**到 `.tools/ark-debug/`
并在报错里给出路径。第一次真实调用要么成功，要么直接告诉我们要改哪里，
而不是抛一句无从下手的 `KeyError`。

### 第 7 阶段的要点：最后一道便宜的关卡

首帧图约 **¥0.2/张**，视频约 **¥4–5.8/条**，差着 20–30 倍。
所以正确的打法是在首帧图上多抽几次选最好的一张，再去生视频——
而不是把预算烧在视频抽卡上。**这是成片前最后一个便宜的关卡。**

- 提示词直接取第 6 阶段定稿的 `shot.prompt`（实体描述已逐字内联）
- 把该镜关联实体的参考图作为**图生图输入**传进去（最多 4 张，按
  主体 → 道具 → 布景排序），保证画面里的角色就是定妆照那个
- `--take N` 每镜抽 N 张，候选都落在 `assets/keyframes/`，选定后手工把要用的
  那张写回 `shot.keyframe_path`
- 产出写 `shot.keyframe_path`，**刻意与 `first_frame_path` 分开**：
  后者的语义是"这一镜实际用的首帧"，会被第 9 阶段的续接逻辑覆盖
  （续接时它等于上一镜的尾帧）

```powershell
python pipeline\stage_firstframes.py --episode-id 1 --dry-run     # 先看张数与金额
python pipeline\stage_firstframes.py --episode-id 1 --take 2      # 每镜抽 2 张挑
python pipeline\stage_firstframes.py --episode-id 1 --only 3,4,5  # 只重做难镜头
```

### 第 8 阶段的要点：三段式，把选择留在便宜的地方

视频生成分三种模式，对应 Draft 工作流的三段：

| 模式 | 处理谁 | 分辨率 | 每条成本 |
|---|---|---|---|
| `--mode draft` | `strategy='draft'` 的镜头 | 480p 样片 | 时长 × ¥0.26/秒 |
| `--mode direct` | `strategy='direct'` 的镜头 | 1080p 直出 | 时长 × ¥1.45/秒 |
| `--mode final` | 有样片的镜头 | 1080p 成片 | 时长 × ¥1.45/秒 |

**为什么不一键到底**：样片便宜、成片贵。把选择留在便宜的那一段——draft 阶段
可以放心多抽，final 阶段每个镜头只出一次。

```powershell
python pipeline\stage_video.py --episode-id 1 --mode draft --dry-run
python pipeline\stage_video.py --episode-id 1 --mode draft --max-cost 200
python pipeline\stage_video.py --episode-id 1 --mode final --pick 3:2,5:1
```

**花钱保护**：`--max-cost` 超过预算直接中止。`price_book` 缺价目时**拒绝在未知花费
下继续**，而不是把成本记 0 然后闷头跑。

出片用的输入图按这个顺序解析：

1. 该镜 `continues_from` 有值 → 用**上一镜的尾帧**（续接，保证跨镜头连续）
2. 否则用该镜的 `keyframe_path`（第 7 阶段的关键帧）
3. 都没有 → 纯文生视频，会明确提示（一致性最差）

其他行为：失败的调用也写一条 `generation` 记录并带错误信息；没有指定选中项时，
第一条成功的成片会自动设为 `selected_generation_id`，避免拼片时无从下手。

> ⚠️ **一处未核实的字段名**：官方文档只说"通过样片对应的 ID 发起成片生成"，
> **没有公布字段叫什么**。`ark.DRAFT_REF_FIELD`（默认 `draft_task_id`）是推测值，
> 改这一处即可。第一次真实调用若不对，原始响应会落在 `.tools/ark-debug/`。

### 第 5 阶段的要点：抽卡策略

第 5 阶段把剧本拆成镜头，**同时为每个镜头决定抽卡策略**——这是它最大的价值，
因为策略直接决定成本：

| 镜头类型 | strategy | draws_planned |
|---|---|---|
| 正脸特写、情绪戏、对手戏 | `draft` | 4–6 |
| 常规叙事镜头 | `draft` | 2–3 |
| 空镜、背影、手部、转场 | `direct` | 1–2 |

脚本会拒绝"一刀切"的产出，并在跑完时按 `price_book` 报出视频成本预估。

另外脚本会**校验实体引用**：分镜里出现的实体名必须逐字存在于 `entity` 表，
自己新造一个名字会被直接拒绝。纯空镜也必须挂上所在布景——否则这个镜头
没有任何视觉锚点，出图时无从约束。

### 第 6 阶段的要点：刻意不走 LLM

提示词定稿是**确定性拼接**，不调用任何模型。理由只有一条，但足以定案：

> 实体的外观描述块必须**逐字复制**进每个镜头的提示词。一旦让模型"润色"，
> 同一个人在不同镜头里就会被描述成不同的人——**这正是跨镜头崩脸的起点**。

代价是提示词读起来不如人写的顺，收益是跨镜头逐字一致、完全可复现、零成本。
而且脚本里对这一点做了**机械校验**：任何实体的描述块没有原样出现在提示词里，
就立刻报错。自检里还有一项反向验证——故意改坏模板，确认它真的会失败。

改了实体描述或改了模板，重跑一次第 6 阶段即可，不必再调模型。

```powershell
python pipeline\stage_prompts.py --episode-id 1            # 组装并写库
python pipeline\stage_prompts.py --episode-id 1 --show 3   # 打印第 3 镜的成品
```

### 自检

`python pipeline\selftest_llm.py` —— 本地起一个**假模型服务**，真跑第 1/2/5/6
四个阶段。刻意让假模型返回带 ``` 围栏和前置解释文字的内容（真实模型就是这样），
只测干净路径等于没测。共 49 项，覆盖：

- JSON 抽取的 5 种脏形态 + 垃圾输入
- 剧本版本管理（v1 降级、v2 上位、旧稿保留）
- 实体提取幂等重跑、`--from-json`
- **模型返回垃圾时干净失败，且一行脏数据都不写库**
- 未配置 LLM 时报错指出缺哪个变量
- 分镜的抽卡策略分化、实体引用校验、空镜必须挂布景
- 提示词逐字校验
- **反向验证**：改坏模板必须立刻失败
- 改实体描述后，新描述传导到全部含它的镜头
- 生产库全程未被写入

### 第 9/10 阶段的要点

- **抽帧**产出 `assets/frames/E{集}S{镜}_first.png` 与 `_last.png`
- **衔接**默认把相邻镜头串起来：第 N+1 镜的首帧 = 第 N 镜的尾帧，
  `continues_from` 指向第 N 镜。真实短剧有硬切，需要断开时把该镜的
  `continues_from` 置空再重跑
- **拼接**优先 `-c copy`（不重编码，秒级）；各镜头参数不一致时自动退回
  重编码并归一化尺寸
- 拼片用 `shot.selected_generation_id` 决定每个镜头取哪一条抽卡结果；
  没指定时退回该镜头第一条 `succeeded` 记录

**端到端自检**：`python pipeline\selftest.py` —— 用 ffmpeg 合成假素材 + 临时库，
真跑一遍两个阶段（含"参数不一致 → 自动重编码"和"中文字幕烧录"），
并确认没碰生产库与真实 `assets/`。共 28 项。

## 流水线清单（声明式契约）

`pipeline_defs/short-drama.yaml` 用**声明式**方式定义整条流程：阶段、依赖的工件、
可用工具、门禁开关、审查关注点、成功判据。形态参照了
[OpenMontage](https://github.com/calesthio/OpenMontage) 的 `pipeline_defs` 契约。

```yaml
stages:
  - name: keyframes
    skill: pipelines/short-drama/keyframe-director
    required_artifacts_in: [shot_prompts, reference_sheets]
    produces: [keyframes]
    tools_available: [stage_firstframes]
    checkpoint_required: true
    human_approval_default: true
    review_focus:                     # 给人看的审查关注点
      - 这是成片前最后一个便宜的关卡
    success_criteria:                 # 机器可判的判据
      - 每个 shot 的 keyframe_path 非空且文件存在
    governance:
      - 禁止为了「挣得」这次审查而先跑视频
```

几个从它那里学来、我们认为值钱的设计：

- **工件（artifact）驱动**：阶段之间靠具名工件串起来，不靠隐式状态。
  校验器会做拓扑检查——某个阶段依赖的工件，前面必须真的有阶段产出它
- **`review_focus` 与 `success_criteria` 分离**：前者给人看，后者给机器判
- **`decision_log` 是一等工件**：每次选择记下备选与理由
- **门禁是逐道的**：之前批过不覆盖后面的门禁，除非当时就把预授权记进 `decision_log`
- **`checkpoint_required` / `human_approval_default` 两个开关**：前者决定要不要存档，
  后者决定要不要停下问人

### 与 `.dsh/skills/` 的区别

两个 `skills` 目录是**两回事**，别混：

| 目录 | 归谁 | 作用 |
|---|---|---|
| `.dsh/skills/` | DSH 智能体框架 | 我会自动加载的技能（如 `ark-seedance`） |
| `skills/` | 本流水线自己 | 阶段导演技能文档，由流水线的执行层读 |

### 校验

```powershell
python tools\validate_manifest.py
```

零依赖（这台机器没装 PyYAML）。它查的不是 YAML 语法，而是**我们的契约**：
门禁阶段有没有写 `review_focus`、`skill` 有没有登记、工件有没有重复产出、
依赖的工件前面有没有人产出。当前结果：**结构 0 问题、契约 0 问题**。

## 执行层：让清单变成约束

上面「清单是声明、执行层还没写」那段曾经是真的。现在是假的了。

| 文件 | 职责 |
|---|---|
| `pipeline/manifest.py` | 解析清单（自写的迷你 YAML，这台机器没有 PyYAML）+ 数据模型 |
| `pipeline/checkpoint.py` | 存档、门禁、决策记录、续跑点的 API |
| `pipeline/run.py` | 统一入口：状态、跑阶段、批准、一路跑到门禁 |

### 一条命令跑一集

```powershell
python pipeline\run.py --episode-id 1                  # 现在走到哪、下一步是什么
python pipeline\run.py --episode-id 1 --run script     # 跑一个阶段
python pipeline\run.py --episode-id 1 --auto           # 一路跑到下一道人工门禁
python pipeline\run.py --episode-id 1 --approve script --note "钩子够狠"
python pipeline\run.py --pending                       # 有哪些在等人批
python pipeline\run.py --episode-id 1 --run script --dry-run   # 只过门禁，不真跑
```

**门禁是真的。** 前面有人工门禁没批准，`--run` 直接报错**退出码 2**，
不是打印一句提醒然后照跑：

```
跳门禁：要开始 script，但它前面的 spec 还没完成（当前状态：未开始）。
  流水线必须按顺序走；先跑完 spec。
```

### 三条被代码强制的规矩

1. **门禁逐道绑定。** 批准是逐阶段的；之前批过一次不覆盖后面的门——
   由 `checkpoint.guard()` 执行
2. **不许自己给自己盖章。** 带人工门禁的阶段，未获批准时 `finish()` 拒绝写成
   `completed`。判据来自**清单的声明**，不是存档碰巧处于哪个状态
3. **存档不销毁。** 重开阶段时 `revision` 递增，旧记录保留。
   "门禁转移与阶段版本永不销毁"必须是真的，否则审计无从谈起

### 续跑

崩在中途不用从头来。`resume_point()` 区分四种中断原因：
还没开始 / 中断 / 失败 / 卡在人工门禁。

**进入阶段时先写一条 `in_progress`**——这既是续跑的依据，
也是告诉看板"还活着，不是卡住了"。

### 校验

```powershell
python tools\validate_manifest.py    # 只校验清单，不碰数据库
python pipeline\selftest_run.py      # 执行层自检，34 项
```

解析器**只有一份**，在 `pipeline/manifest.py`；`validate_manifest.py` 是薄封装。
（原来两处各写各的，必然分叉——第一版就因为不认折行和行尾注释误报了一堆假问题。）

## 数据库结构

三段式执行，顺序不能颠倒（`db/init_db.py` 自动按序调用）：

```
1. db/schema.sql   建表，全部 IF NOT EXISTS —— 纯加法
2. migrate()       给已存在的表补新列（ALTER TABLE）
3. db/views.sql    建索引与视图 —— 引用第 2 步新加的列
```

**为什么必须分三段**：`CREATE TABLE IF NOT EXISTS` 对已存在的表完全不动，
所以加列不会被自动迁移。实测踩过：给 `shot` 加 `scene_id` 后，
跟在后面的索引直接报 `no such column`。

**加列的正确做法**：改 `schema.sql`（新库的目标状态）+ 在 `init_db.py` 的
`COLUMN_MIGRATIONS` 里登记一条 ALTER（老库的升级路径），然后递增 `SCHEMA_VERSION`。

数据域与承载表：

| 数据域 | 表 |
|---|---|
| 剧本 | `script`、`scene` |
| 集 | `episode` |
| 镜头 | `shot` |
| 素材 | `asset` |
| 生成任务 | `generation` |
| 成本 | `cost_ledger`、`price_book` |

辅助表：`project`、`character`、`meta`。视图：`v_episode_cost`、`v_episode_outline`、
`v_shot_progress`、`v_price_book`。

## 接入远程仓库

**远程已接好**：`git@github.com:Zrz7788/short-drama.git`（分支 `main`，公开仓库）。

SSH 密钥在 `.tools/ssh/`（**不进 git**）。公钥：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILb9uMXKJ8mw8DQlghO96GIplzPT40ft118xO1xdJQeM zrz@short-drama
```

指纹（贴到 GitHub 后可以在页面上核对，确认加对了）：

```
SHA256:o6SMoggis1x/SKLWEQFCNuoexmll5awxinn2AqM+McU  (ED25519)
```

> 注意那个 `s1x` 是**数字 1**，不是小写字母 l。这串里 `l`/`1`/`I` 极易看错，
> 核对指纹是确认"加的就是本地这把"的唯一可靠办法。

### 换一台机器要重做的三步

**① 用 Windows 原生 ssh，别用 MinGit 自带的**

`.tools/mingit/usr/bin/ssh.exe` 是 MSYS 程序，在受限沙箱下创建不了命名管道：

```
*** fatal error - couldn't create signal pipe, Win32 error 5
```

改用 `C:\Windows\System32\OpenSSH\ssh.exe`——它不是 MSYS 程序，不走命名管道。

**② known_hosts 不存在时 ssh 会弹交互提示，把命令挂死**

**不要靠 TOFU**（第一次连接盲信）。用官方接口写一份：

```powershell
# 取 api.github.com/meta 的 ssh_keys 字段，写成 .tools\ssh\known_hosts
# 覆盖 github.com 与 ssh.github.com 两个主机名
```

**③ push 需要一次提权**

git 推送时会调用 MSYS 的 `sh.exe`，**同样撞命名管道**。这与用哪个 ssh 无关，
是 git 自己的包装层。受限沙箱下必须提权一次；
`git add/commit/config/remote` 等本地操作不受影响。

### 配置（已生效，记在这里备查）

```powershell
& $git remote add origin git@github.com:Zrz7788/short-drama.git
& $git config core.sshCommand '"C:/Windows/System32/OpenSSH/ssh.exe" -i "<repo>/.tools/ssh/id_ed25519" -o UserKnownHostsFile="<repo>/.tools/ssh/known_hosts" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes'
& $git branch -M main
& $git push -u origin main      # ← 只有这一步需要提权
```

### 公开仓库：每次推之前要过一遍

本仓库是**公开**的。推之前先确认：

| 项 | 状态 |
|---|---|
| 私钥 / `.env` / `*.db` / `known_hosts` | 已被 `.gitignore` 挡住，`tools\verify.py` 会扫 |
| `.tools/`（便携 git、ffmpeg、72MB 参照语料） | 整个目录不进 git |
| **第三方技能包** | **不进 git**（见下） |

**第三方技能包为什么不进 git**：`skills/techniques/` 下 12 个包来自 OpenMontage
（**AGPL-3.0**）、2 个来自 ai-mandrama-skills（MIT）。本仓库公开，
转发 AGPL 内容会带来许可义务，所以 `.gitignore` 里排除了包内容，
**只保留出处清单** `skills/techniques/_provenance.json`——它记着每个包的
来源、路径、许可、警告项，本身就是一份"用过什么、从哪来"的记录。

换台机器 clone 下来后一条命令装回来：

```powershell
python tools\install_skill.py --restore
```

（优先走本地 tarball：那 12 个包逐个走 GitHub API 会很快撞上未认证限速 60 次/小时。
tarball 不在时会说明怎么获取。）

### 建仓库时的两个坑

1. 必须建**空**仓库——**不要勾** README / .gitignore / license，否则和本地历史冲突
2. 建完要**把公钥加到账号**（Settings → SSH and GPG keys），否则 push 报
   `Permission denied (publickey)`——网络和主机密钥都对，纯粹是公钥没登记

**Gitee 是备选**：`gitee.com` 从这台机器 SSH 只要 **0.16s**（GitHub 是 0.63s），
而 **GitHub 的网页（443）在这台机器上超时**，只有 SSH（22）和 API 通。
如果哪天 GitHub 网页打不开，转 Gitee 步骤一样，只换地址。

第 3 步在受限沙箱下会报信号管道错误，需要更宽权限——见「本机环境要点」§2 的三种处理方式。
后续每次 `push` 同理。

## 项目纪律

1. **密钥不进 git。** `ARK_API_KEY` 放环境变量或本地 `.env`（已被 `.gitignore` 挡住）
2. **价格与模型 ID 必须标来源和置信度。** 无出处的数字不写进 `knowledge/02-*`
3. **`knowledge/01-*` 的方法在拿到真实数据前都是假设**，跑完回流到 `04-复盘/`
4. **角色技术设定只在数据库维护**，不要在项目档案里重复
5. **知识库与 skill 要同步改**——两处不同步就会误导智能体

## 当前状态

| 环节 | 状态 |
|---|---|
| Git 本地仓库 | ✅ 已初始化 |
| SQLite 生产库 | ✅ 15 表 / 6 视图 / 19 索引，schema v3，27 项自检通过 |
| 知识库 | ✅ 11 个 md（8 篇内容 + 3 个索引/模板），含全流程总览 |
| 数据模型 | ✅ 统一实体（人物/场景/道具）+ 参考图 + 镜头续接 |
| `ark-seedance` skill | ✅ 已建，会话内即时生效 |
| ffmpeg 工具链 | ✅ 便携版 n9.0，17 项功能验证通过 |
| 第 1/2/5/6 阶段脚本 | ✅ 剧本、实体分级、分镜、提示词定稿，49 项自检通过 |
| 第 3/4/7/8 阶段脚本 | ✅ 人物/场景/道具素材、首帧图、视频生成；Ark 字段名取自真实实现，76 项自检通过 |
| 第 9/10 阶段脚本 | ✅ 抽帧、衔接、拼接、字幕，28 项端到端自检通过 |
| 全量验收 | ✅ `python tools\verify.py`，33 项 |
| **十个阶段脚本** | ✅ **全部写完并通过自检** |
| **全链路集成** | ✅ 十阶段串起来跑通，产出 14 秒成片（合成素材） |
| GitHub 远程 | ⏳ 待接入（SSH 密钥已生成、链路实测通；受限模式下 push 需提权） |
| Ark API Key | ⏳ **唯一剩余项**：插上钥匙即可跑真实生成 |
| 真实 API 验证 | ⏳ 视频任务的响应结构与 draft 关联字段名尚未对真实服务验证过 |
