# 成片导演 — 短剧流水线（拼接 / 字幕 / 混音）

## 何时使用

最后一个阶段。把镜头拼成一集，烧字幕，可选混音。

## 前置

| 层 | 资源 | 用途 |
|---|---|---|
| 上游工件 | `continuity_map` | 每个镜头的选中成片 |
| 可选工件 | 字幕文件（`subtitle_file`） | 烧录 |
| 表 | `asset` / `episode.status` | 落成片记录 |
| 工具 | `pipeline/assemble.py` | 拼接 + 字幕 |
| 技能 | `skills/techniques/edge-tts-chinese-roleplay` | 混音模板 |
| 技能 | `skills/techniques/ai-mandrama-pipeline` | 中文字幕踩坑 |

## 流程

### 1. 拼接

```powershell
python pipeline\assemble.py --episode-id 1 --out out
python pipeline\assemble.py --episode-id 1 --out out --srt subs\E01.srt
```

脚本**优先走 `-c copy`**（不重编码，快且无损），参数不一致时**自动回退到重编码**
并做归一化。这条回退不是失败，别把它当错误看。

### 2. 中文字幕：一个必须避开的坑

> ❌ **libass `subtitles=` 滤镜烧 ASS 字幕会出方块 □□□**
> 原因通常是 libass 找不到 ASS 里指定的字体。

**✅ 正确做法：`drawtext` + `textfile=` + 直接字体路径**

| 技术点 | 为什么 |
|---|---|
| **`textfile=` 而不是 `text=`** | 避中文标点转义地狱 |
| `enable='between(t,start,end)'` | 控制每段显示时段 |
| `borderw=2:bordercolor=black` + `shadowx/y=2` | 强对比，保证手机小屏可读 |
| 字体路径含空格要转义 | `${FONT// /\\ }` |

**我们的 `verify_ffmpeg.py` 已经验证过中文烧录可用**，所以这条路是通的。
但如果字幕出现方块，先怀疑字体路径。

### 3. 字幕按角色分色（值得采纳）

一部剧里谁在说话，观众应该一眼看出来：

| 角色 | 颜色 | 字号 |
|---|---|---|
| 旁白 | 白 `#FFFFFF` | 34pt |
| **主角** | **黄 `#FFDD00`** | 36pt（**关键台词放大到 60pt**） |
| 系统/画外 | 绿 `#00FF00` | 36pt |
| 反派 | 红 `#FF1515` | 36pt |
| 配角 | 灰 `#DDDDDD` | 32pt |

### 4. 混音（只有走外部 TTS 时才需要）

**如果走模型原生音频，这一步可以省掉**——音频已经在视频里了。

走外部 TTS 时，ffmpeg 的职责是**按时间码铺音频 + 多轨混音**：

```bash
ffmpeg -y -i input_video.mp4 -i seg_01.mp3 ... -i bgm.wav \
  -filter_complex "
[1:a]aresample=44100,adelay=500|500,volume=14[t1];
...
[16:a]aresample=44100,adelay=4000|4000,volume=0.18[bgm];
[t1]...[t15][bgm]amix=inputs=16:dropout_transition=0[aout]" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k -shortest output.mp4
```

三个非直观的参数：

- **`volume=14`** —— 补 `amix` 默认按 1/N 的衰减（N 段输入就衰减 N 倍）
- **`volume=0.18`** —— BGM 压到 18%，不盖配音
- **不要加 `normalize=0`** —— 老版 ffmpeg 不识别（我们是 n9.0，没事）

> ⚠️ **再次强调：ffmpeg 不做口型对齐。** 它只做时间轴与混音。
> 口型是模型的事。想靠 ffmpeg 对口型是选错了工具。

### 5. 缺失镜头

默认**拒绝出残缺版**。缺镜头就是缺镜头，拼出一集少一块的东西比不出更糟。

### 6. 响度

交付前做一次响度标准化（EBU R128），保证手机外放和耳机都不难听。
这是 ffmpeg 比剪映精确的地方。

## 剪映什么时候用

**pipeline 里 95% 用 ffmpeg，最后的人工润色用剪映**（预算允许的话）。

| 交给 ffmpeg | 交给剪映 |
|---|---|
| 批量跑 100 集 | 卡点剪辑（自动节拍检测） |
| 精确字幕时间轴 | 转场/特效库 |
| 响度标准化 | 凭眼睛调色 |
| 无人值守、可复现 | 文字动效 |

机械环节用 ffmpeg，**"手感"那一公里**用剪映。

## 常见陷阱

- **缺失镜头直接拼。** 应该拒绝
- **字幕用 libass 烧 ASS。** 中文可能出方块
- **`text=` 直接写中文。** 标点转义会出问题
- **混音忘了补 `volume`。** 多段一起混会整体变轻
- **BGM 不压低。** 盖住配音
- **以为 ffmpeg 能对口型。** 它不能
- **成片出了但没登记到 `asset`。** 后面找不到

## 质量门禁

- `out/E{seq}.mp4` 存在且 `ffprobe` 可读
- **成片时长与各镜素材之和偏差 ≤ 0.5 秒**
- 字幕已正确烧录（中文字体正常，无方块）
- 响度已标准化
- `asset` 表有 `kind='final'` 的记录
- `episode.status = 'assembled'`
- 缺失镜头数为 0（或已被显式确认放行）

---

## 门禁提醒（绑定）

本阶段有人工门禁。做完后**结束回合**，等人批。

批准：`python pipeline\run.py --episode-id N --approve assemble --note "..."`

**这是整集的最后一道关。** 过了这道门，第 N 集就交付了。
