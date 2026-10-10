# AI 短剧《末世招队员，我只挑最废的》第一集 制作过程

> 制作时间: 2026-05-08 23:50 ~ 2026-05-09 05:00 (约 5 小时)
> 工具: Dreamina CLI + edge-tts + ffmpeg + macOS `say` + Pillow
> 模型: Dreamina 5.0 (生图) / Seedance 2.0fast 720p (生视频)

---

## 1. 项目背景

基于"鱼子酱 AI 漫剧 1 节"课程的 SOP，使用现成素材（剧本一卡、分镜表、角色介绍）端到端制作第一集。

### 输入素材（用户提供）

| 文件 | 用途 |
|---|---|
| `00_剧本/一卡_v1.docx` | 动态漫剧本（14 个镜头剧情） |
| `01_分镜表/第一集_分镜表_v1.xlsx` | 9 列结构分镜表（场景/人物/动作/提示词等） |
| `02_核心场景与角色/第一集_核心场景与角色.docx` | 角色设定参考 |
| `09_提示词模板/分镜表提示词.txt` | 分镜表生成模板 |
| `09_提示词模板/人物生图提示词描述词.txt` | 角色提示词词典 |

### 第一集剧情（14 镜头）

废墟开场 → 庄园泳池（陆明 + 美女举杠铃）→ 陆明甩飞杠铃 → 办公室猝死闪回 → 系统觉醒 → 末世集市招宁曦 → 队长1 踹宁曦 → 陆明扶起 → 陆明召雷装逼 → 配角争抢加入 → 陆明选宁曦 → 系统结算

---

## 2. 工具栈

| 工具 | 用途 | 备注 |
|---|---|---|
| **Dreamina CLI** | 角色三视图、分镜单图、单镜头视频 | 字节豆包旗下，maestro VIP |
| **edge-tts** (Python) | 中文 TTS 配音 | 微软 Azure 同源，免费，10+ 中文神经语音 |
| **macOS `say`** | 早期配音方案（已弃用） | 中文 voice 实际只有 Tingting/Sinji 工作 |
| **ffmpeg** | 视频拼接、字幕、调色、混音、转场、9:16 重排 | 系统已装 |
| **Python (PIL/openpyxl/python-docx)** | 解析 docx/xlsx + 后台 polling 脚本 | |

---

## 3. 制作流程（按 SOP 9 阶段对照）

### 阶段一：剧本一卡 ✅

跳过——直接用用户提供的 `一卡_v1.docx`。

### 阶段二：分镜表 ✅

跳过——直接用用户提供的 `第一集_分镜表_v1.xlsx`。从 xlsx 抽取出 14 个主镜头 + 每镜头的 prompt + 对白 + 运镜信息。

### 阶段三：核心场景与角色介绍 ✅

跳过——直接用用户提供的 docx。

### 阶段四：角色资产库（三视图）— 关键迭代 ⭐

#### v1：写实风（按 SOP 默认）

```bash
dreamina text2image \
  --prompt="角色三视图设计稿，正面/侧面/背面..." \
  --ratio=16:9 --resolution_type=2k --model_version=5.0 --poll=90
```

生成 4 张三视图（陆明庄园 / 陆明集市 / 宁曦 / 比基尼美女）。**用户反馈：角色不够好看**。

#### v2：日漫赛璐璐二次元风（采纳）

切换风格关键词：
- ❌ "8K高清，电影级质感，写实风格"
- ✅ "日漫赛璐璐平涂上色，类似《全职高手》《魔道祖师》乙游风格"

生成 4 张 v2 三视图。**用户反馈："还是用V2的版本吧"** —— 锁定 v2。

#### v3：温柔治愈系（被弃用）

用户中途说"需要更帅气、明媚、温柔"，我矫枉过正做了 v3（治愈系日漫《夏目友人帐》风），但用户最终还是选 v2。**经验教训：用户说"明媚温柔"指的是相对方向，不是切换风格基调。**

#### 角色 prompt 心得

| 角色 | 关键词 |
|---|---|
| **陆明（爽文男主）** | 桃花眼略上挑、剑眉斜飞、薄唇微抿带邪魅、黑色微长碎发气流感、慵懒锋利眼神 |
| **宁曦（F 级女主）** | 纤瘦娇小、黑色长发披肩微卷、樱桃唇、楚楚可怜被欺负气质、破旧但本身好看的反差 |
| **比基尼美女** | 沙漏型胸大腰细、浅棕色大波浪卷长发、温柔明媚大姐姐型 |
| **陆明集市造型** | 用 `image2image` 拿庄园造型当锚定换装（深棕风衣 + 黑高领），保持人物一致性 |

---

### 阶段五：单镜头生图（14 张）

**14 张分镜按 v2 二次元风一气呵成**。

| 镜头号 | 命令类型 | 锚定参考 |
|---|---|---|
| 1（废墟） | text2image | 无人物 |
| 2（庄园甩杠铃） | image2image | 陆明庄园 + 美女 |
| 3（庄园碰杯） | image2image | 陆明庄园 + 美女 |
| 4（办公室猝死） | text2image | 闪回独立场景 |
| 5（系统觉醒） | text2image | 仅陆明背影 |
| 6（陆明特写） | image2image | 陆明庄园 |
| 7（集市踹宁曦） | image2image | 陆明集市 + 宁曦 |
| 8（陆明扶起） | image2image | 陆明集市 + 宁曦 |
| 9（陆明激动） | image2image | 陆明集市 + 宁曦 |
| 10（召雷） | image2image | 陆明集市 |
| 11（雷消失） | image2image | 陆明集市 |
| 12（配角争抢） | image2image | 陆明集市 |
| 13（陆明选宁曦） | image2image | 陆明集市 + 宁曦 |
| 14（系统结算） | image2image | 陆明集市 + 宁曦 |

**核心心法**：有人物的镜头**必须用 image2image**，把对应角色的三视图作为参考图传入，否则角色每帧脸都不一样。

#### ⚠️ Dreamina CLI 关键踩坑

1. **i2i 不能并行** —— 并行 4 个 i2i 任务全部 silent fail，必须严格串行
2. **5.0 模型对长 prompt 严格审查** —— "末世废土"+"战术腰带"+"军靴" 组合触发"final generation failed"。用 4.6 模型或缩短 prompt 或 i2i 换装规避
3. **`tail -N` 会截断 image_url** —— 早期我把 dreamina 输出 pipe 到 `tail -15`，把图片 URL 截掉了。改用 `2>&1 | tee /tmp/out` 完整保留，再 grep
4. **submit_id 提取要严格** —— bash 字符串污染问题：把 stderr 的 log 行也当 sid 传给 query_result，导致一直返 unknown。修复：`grep -oE '[a-f0-9]{8}-[a-f0-9]{4}...' | head -1`

---

### 阶段六：单镜头生视频（14 段）

**模型选型**：`seedance2.0fast` 720p 5s/段（maestro VIP 推荐），每段 ~25-30 credit。

#### ⚠️ 服务端单用户并发上限 = 1

7 段第一批 + 7 段第二批共 14 段，**每段必须串行排队**（dreamina 服务端单用户视频任务最多 1 个 active）。每段实际耗时：
- 队列等待 ~10-12 分钟（队列长度 ~600，每 30 秒消耗 30 位）
- 实际生成 ~30-60 秒
- 单段总计 ~12-15 分钟

**14 段共耗时 ~3 小时**（含 1 段镜头8 提交失败，最终 13 段成功）。

#### 后台无人值守 polling 脚本

```python
# /tmp/i2v_polling.py 核心逻辑
def submit_one(id, image, prompt):
    # 提交 + 提取 submit_id (严格 UUID 正则)
    # 失败重试 3 次

def wait_and_download(sid, label):
    # 每 30 秒查一次 query_result
    # 直到 video_url 出现，下载 mp4
    # 80 次 poll = 40 分钟超时
```

`watchdog` 脚本监测 polling 完成 → 自动调起 master 拼接脚本（用户睡觉期间无人值守）。

---

### 阶段七：配音 — 三版迭代 ⭐

#### v2：macOS `say`（被弃用）

```bash
say -v Tingting -o output.aiff "陆明：不准训练哈"
```

**踩坑**：`say -v "?"` 列出 10 个中文 voice，但**实际只有 Tingting (zh_CN) 和 Sinji (zh_HK) 真正能合成中文**——其他 Reed/Eddy/Sandy/Rocko 都是空音（0.02s 头部）。检测方法：
```bash
say -v Reed -o /tmp/test.aiff "你好"
afinfo /tmp/test.aiff | grep duration  # < 0.05s = 失败
```

#### v3：edge-tts 神经语音（采纳）

```bash
pip3 install edge-tts
edge-tts --voice "zh-CN-YunxiNeural" --rate="+5%" --pitch="+3Hz" \
  --text "不准训练哈" --write-media output.mp3
```

**角色声线分配**：

| 角色 | Voice | 风格定位 |
|---|---|---|
| 陆明（男主） | `zh-CN-YunxiNeural` | 阳光小说男主，活泼 |
| 队长1 / 路人 | `zh-CN-YunjianNeural` | 体育激情型，粗暴男 |
| 配角男 | `zh-CN-YunxiaNeural` | 卡通可爱，谄媚搞笑 |
| 系统 / 旁白 | `zh-CN-XiaoxiaoNeural` | 温柔，新闻小说级 |

#### v4：SSML 节奏精调

每段单独调 `--rate` 和 `--pitch`：

| 镜头 | rate | pitch | 效果 |
|---|---|---|---|
| 路人喊救命 | +25% | +10Hz | 紧张惊恐 |
| 陆明摆烂调侃 | +8% | +3Hz | 阳光轻松 |
| 陆明 OS 自嘲 | -5% | -3Hz | 沉重内心 |
| **陆明"起！"** | **-30%** | **-5Hz** | **慢且低，霸气定海** |
| 系统女音 | -15% | -2Hz | 机械感降速 |
| 配角谄媚抢 | +20% | +10Hz | 小丑感拔尖 |

---

### 阶段八：BGM + SFX 合成

#### BGM（60 秒，4 段不同氛围拼接）

用 ffmpeg `sine` 滤镜合成 ambient drone（不是真实音乐，是占位 BGM，可后续在剪映里替换专业曲目）：

```bash
# 末世低吟 13s — C2(65Hz) + G2(98Hz) + 低八度 49Hz
ffmpeg -f lavfi -i "sine=f=65:d=13" -f lavfi -i "sine=f=98:d=13" -f lavfi -i "sine=f=49:d=13" \
  -filter_complex "[0][1][2]amix=3,tremolo=f=0.4:d=0.5,afade=t=in:st=0:d=2,afade=t=out:st=11:d=2,volume=2.5" \
  -ar 44100 末世.wav

# 闪回轻音 12s — C4+E4+G4 三和弦 + vibrato
# 集市紧张 25s — 多频段叠加 + tremolo 颤动
# 系统科技 7s — 高频 sine 跳动
```

#### SFX（4 个音效）

```bash
# 闪电雷击 — brown noise + sine 60Hz
# 瞬移 woosh — sine 2000Hz sweep + asetrate 变速
# 系统弹窗 ding — sine 880+1320Hz 双音
# 踹倒 thud — sine 100Hz + brown noise
```

#### ⚠️ ffmpeg 旧版踩坑

系统装的是 ffmpeg N-92585（2018 版），新版 amix 滤镜的 `normalize=` 参数不识别。**去掉 `normalize=0`，改用 `volume=N` 手动补回 amix 自动衰减**。

---

### 阶段九：成片拼接（master 脚本）

#### 5 步 pipeline

1. **每镜头标准化 clip**：720p 30fps + 调色（contrast 1.05、saturation 1.12、vignette PI/5）+ fade in/out 0.3s
2. **拼接**：片头 4s + 14 镜头 + 片尾 4s = **65s 总长**
3. **字幕烧入**：见下"字幕踩坑"
4. **混音**：BGM(0.18 vol) + 15 段 TTS(每段 adelay+volume=14) + 4 个 SFX = 共 20 输入流 amix
5. **9:16 竖版**：`scale=720:-1,pad=720:1280:0:(1280-ih)/2:black`

#### 片头/片尾标题卡

```bash
ffmpeg -f lavfi -i "color=c=#0a0a14:s=1280x720:d=4" \
  -vf "drawtext=fontfile='/System/Library/Fonts/STHeiti Medium.ttc':\
       fontsize=72:fontcolor=white:x=(w-tw)/2:y=h/2-80:text='末世招队员', \
       drawtext=...fontcolor=#FFD700:text='我只挑最废的', \
       drawtext=...fontcolor=#888888:text='第一集 摆烂的开端', \
       fade=t=in:st=0:d=0.8,fade=t=out:st=3.2:d=0.8" intro.mp4
```

#### ⚠️ 字幕渲染重大踩坑（v4 → v4.1）

**v4 用 `subtitles=` 滤镜（libass）烧 ASS 字幕**：

```bash
ffmpeg ... -vf "subtitles='sub.ass':fontsdir=/System/Library/Fonts" ...
```

**结果：中文全部变方块 □□□**。原因：libass 在 macOS 上找不到 ASS 里指定的"STHeiti Medium"字体（fontconfig 未注册 ttc）。

**v4.1 修复方案：改用 `drawtext` 滤镜 + textfile + 直接字体路径**：

```bash
FONT='/System/Library/Fonts/STHeiti Medium.ttc'
ffmpeg -i v4_raw.mp4 -filter_complex "
[0:v]drawtext=fontfile=${FONT// /\\ }:fontsize=36:fontcolor=#FFDD00:\
     textfile=/tmp/subs/03.txt:\
     enable='between(t,9.5,14)':\
     x=(w-text_w)/2:y=h-100:\
     borderw=2:bordercolor=black:\
     shadowcolor=black:shadowx=2:shadowy=2[v3];
[v3]drawtext=...[v4];
... (15 段链式 drawtext)
" v4.1.mp4
```

**关键技术点**：
- `textfile=` 替代 `text=`，避开中文逗号/感叹号转义噩梦
- `enable='between(t,start,end)'` 控制每段字幕显示时段
- `borderw=2:bordercolor=black + shadowx/y` 强对比保证手机可读
- 字体路径里的空格用 `${FONT// /\\ }` 转义

#### 字幕样式（按角色）

| 角色 | 颜色 | 字号 |
|---|---|---|
| 旁白 | 白 #FFFFFF | 34pt |
| **陆明** | **黄 #FFDD00** | 36pt（关键台词"起！"放大到 60pt）|
| 系统 | 绿 #00FF00 | 36pt |
| 反派 | 红 #FF1515 | 36pt |
| 配角 | 灰 #DDDDDD | 32pt |

---

## 4. 关键踩坑总结

| # | 踩坑 | 修复 |
|---|---|---|
| 1 | Dreamina i2i 并行任务 silent fail | 严格串行，每个任务等完再下一个 |
| 2 | Dreamina 服务端单用户视频并发=1 | 后台 polling + watchdog 串行队列 |
| 3 | bash submit_id 被 stderr 污染 | `grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-...'` 严格 UUID 正则 |
| 4 | macOS `say` 列表里 90% 中文 voice 是空音 | 切到 `edge-tts`（免费 + 10+ 真神经语音）|
| 5 | ffmpeg 老版 `normalize=` 不识别 | 删除参数 + `volume=N` 手动补 |
| 6 | libass `subtitles=` 滤镜中文变方块 | 改用 `drawtext + textfile + STHeiti.ttc` |
| 7 | 5.0 模型对长 prompt 严格审查 | i2i 换装代替 t2i / 缩短 prompt / 换 4.6 模型 |
| 8 | `tail -N` 截断 dreamina image_url | `tee /tmp/out` 完整保留 |
| 9 | 中文 fontfile 路径含空格 | `${FONT// /\\ }` bash 转义 |
| 10 | dreamina credit 失败也扣费 | 任务结构化 + 重试限次（最多 3 次）|

---

## 5. Credit 与时间统计

### Credit 消耗（dreamina maestro）

| 阶段 | 消耗 |
|---|---|
| 4 张 v2 三视图 | ~12 |
| 3 张 v3 三视图（被弃） | ~9 |
| 14 张 v2 分镜单图 | ~42 |
| 7 段第一批 image2video | ~175 |
| 6 段第二批 image2video（镜头8 失败 skip） | ~150 |
| **总计** | **~390 credit**（起 6198 → 剩 ~5810）|

### 时间分配

| 阶段 | 耗时 |
|---|---|
| 素材分析 + 项目目录 | 5 min |
| 角色资产 v1 → v2 → v3 迭代 | 30 min |
| 14 张分镜单图 | 25 min |
| 7 段第一批视频（生成）| 100 min |
| 配音 v1/v2/v3 + BGM/SFX 合成 | 20 min |
| 7 段第二批视频（无人值守）| 90 min |
| Master 拼接（自动）| 8 min |
| 字幕修复 v4.1 | 5 min |
| **总计** | **~5 小时**（其中 70% 是视频生成排队）|

---

## 6. 最终交付（7 个 mp4）

```
~/Downloads/AI短剧_末世招队员/08_成片/
├── 第一集_成片_v1.mp4              (15.5MB, 57s) — 静默 + 字幕
├── 第一集_成片_v2_配音.mp4         (15.9MB, 56s) — macOS say（机器声）
├── 第一集_成片_v3_配音升级.mp4     (16.5MB, 57s) — edge-tts 神经语音
├── 第一集_成片_v4_发布级.mp4       (18.9MB, 1m02s) — 字幕变方块
├── 第一集_成片_v4_竖版.mp4         (7.2MB,  1m02s) — 9:16，字幕也是方块
├── 第一集_成片_v4.1_字幕修复.mp4   (19.2MB, 1m02s) ⭐ 推荐发布版
└── 第一集_成片_v4.1_竖版.mp4       (7.3MB,  1m02s) ⭐ 抖音/小红书推荐
```

### v4.1 包含的全部增强

- ✅ 14 镜头标准化 720p/30fps + 整体调色（contrast/saturation/vignette）
- ✅ 13 段动态视频 + 1 段静帧 zoompan（镜头8 image2video 失败 fallback）
- ✅ 片头 4s 标题卡（末世招队员 / 我只挑最废的 / 第一集 摆烂的开端）
- ✅ 片尾 4s 下集预告
- ✅ 镜头间 0.3s fade in/out 模拟交叉转场
- ✅ 15 段角色字幕（陆明黄/系统绿/反派红/配角灰，关键台词"起！"放大）
- ✅ edge-tts SSML 升级配音（每段单独调 rate/pitch）
- ✅ 60s 4 段氛围 BGM（末世/闪回/集市/系统）
- ✅ 4 个 SFX（thunder/woosh/kick/thud）
- ✅ 9:16 竖版自动适配

---

## 7. 课程 SOP 对照（哪些没严格执行）

| SOP 阶段 | 状态 | 备注 |
|---|---|---|
| 1️⃣ 剧本一卡 | ✅ 用现成 | |
| 2️⃣ 分镜表 | ✅ 用现成 | |
| 3️⃣ 角色介绍 | ✅ 用现成 | |
| 4️⃣ 资产库三视图 | ✅ | |
| 5️⃣ 单镜头生图 | ✅ | |
| 6️⃣ 单镜头生视频 | ✅（13/14）| 镜头8 image2video 失败用静帧 |
| 7️⃣ **首尾帧素材** | ❌ 跳过 | 用 ffmpeg fade 转场代替 |
| 8️⃣ **剪映工程** | ❌ 跳过 | 直接 ffmpeg 出 mp4 |
| 9️⃣ 成片 | ✅ | |

如要严格执行 SOP，可：
- 把 14 张分镜单图 + 7 张静帧 + 7 段 mp4 + BGM/SFX/TTS 全部素材导入剪映
- 在剪映里手动调节奏、加专业 BGM、调字幕样式
- 输出 4K 版

---

## 8. 后续可优化方向

1. **真实音乐 BGM** —— 从 Pixabay/Mixkit/YouTube Audio Library 下载专业曲，替代 ffmpeg synthesize 的占位 ambient
2. **专业配音演员** —— edge-tts 神经语音已经不错但仍是机器声，要"演技感"得真人或商用 TTS（如字节火山引擎 TTS 角色化声线）
3. **首尾帧衔接** —— 课程 SOP 里的标准动作，让镜头之间过渡更自然（如镜头1 末尾天空 = 镜头2 开始天空）
4. **剪映精修** —— 把所有素材给剪映重做，加专业转场（缩放、抖动、双重曝光）
5. **完整剧集** —— 第一集只 1 分钟，按 SOP 第一集应 ~3 分钟。可补每镜头 2-3 个细分子镜头扩充
6. **多集制作** —— 共需 100+ 分钟、~30 集才能成完整剧

---

## 9. 关键文件路径速查

```
项目根: ~/Downloads/AI短剧_末世招队员/

├── 00_剧本/              一卡_v1.docx
├── 01_分镜表/            第一集_分镜表_v1.xlsx
├── 02_核心场景与角色/    第一集_核心场景与角色.docx
├── 03_资产库/第一集/角色/  4 张 v2 + 4 张 v3 + 4 张 v1 三视图
├── 04_单镜头生图/第一集/   14 张 v2 分镜单图 + 5 张 v1
├── 05_单镜头生视频/第一集/ 13 段 mp4（镜头8 缺）
├── 06_首尾帧素材/第一集/   （未使用，预留）
├── 08_成片/               7 个版本 mp4
└── README_制作过程.md     本文档
```

工作脚本（运行时产物，不属于交付）：
```
/tmp/i2v_polling.py            后台视频生成 polling
/tmp/watchdog_v4.sh            等 polling 完调起 master
/tmp/master_v4.sh              主拼接脚本（176 行）
/tmp/ep1_audio_v4/             15 段 SSML TTS + BGM/SFX/片头片尾
/tmp/v4_subs/                  15 段字幕 textfile
/tmp/ep1_v4_build/             中间产物（每镜头 mp4 + raw 拼接版）
```

---

*文档生成时间: 2026-05-09 05:00*
*Co-Authored-By: Claude Opus 4.7 (1M context)*
