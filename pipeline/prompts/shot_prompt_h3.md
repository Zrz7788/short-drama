# H3 六段式提示词模板（MiniMax H3 图生视频/文生视频）

> 来源：漫剧老李 V7.4.20 h3-adapter.md + awesome-seedance-2-prompts 黄金结构 + 我们 EP002 实测
> 适用模型：MiniMax-H3 / MiniMax-H3 Max
> 接口：POST https://api.minimaxi.com/v2/video_generation

## 必填参数

| 参数 | 取值 | 说明 |
|---|---|---|
| model | MiniMax-H3 | 或 MiniMax-H3-Max（更快，480P/768P）|
| duration | 4-15（整数秒）| 对齐分镜时长 |
| resolution | 768P / 2K | 768P=¥0.5/s，2K=¥0.8/s |
| ratio | 9:16（竖屏短剧）| 禁 adaptive |
| content | 数组 | [{type:text}, {type:image_url}] |

## 六段式正文结构（英文，台词保中文）

### 第一段：风格总声明（Global Style）
一段话定死全片视觉。放在 content[0].text 的开头。
关键词：Photorealistic cinematic realism + 色调 + 胶片质感 + 环境 + 光线。
```
Cold grey-blue palette, cinematic realism, 35mm film texture, natural light,
Chinese county-town funeral home environment.
```

### 第二段：场景与空间（Environment）
描述空间、光线来源、氛围物件。一个场景写一次，同场景多镜可复用。
```
Interior of a funeral home office: dark wooden desk, potted orchid with
freshly watered dark soil, metal mesh waste basket, wall clock, natural
window light from the left, paper shredder on the desk corner.
```

### 第三段：核心人物（Character Lock）
每个出场人物一段描述。同名同描述=跨镜一致性锚。
```
A young Chinese woman, 28, black low ponytail, dark charcoal work jacket
over grey apron, sleeves with faint ash stains.
An older Chinese man, early 50s, grey short hair, black-framed glasses,
light grey Zhongshan-style office jacket.
```

### 第四段：时间轴动作（Timeline Actions）
按 `0-{N}s: [镜头标注] 人物动作 + 微表情 + 对白` 分段写。
每段 2-5 秒。微表情写到"器官级"（瞳孔/喉结/耳垂/指尖）。
对白保中文，用引号。
```
0-3s: [Close-up] She taps ash off her sleeve cuff three times,
then says: "昨晚三点，三〇〇的炉门，敲了三下。"
```

### 第五段：负面清单（Negative Constraints）
固定追加在 content[0].text 末尾。
```
No text overlay, no watermark, no floating text, no subtitles,
no split screen, no transitions between shots.
```

### 第六段：技术尾块（Technical Footer）
可选。分辨率/帧率/种子锁定/画面比例的补充说明。
```
9:16 vertical, 24fps, stable camera, no camera shake.
```

## 首帧/尾帧输入

首帧（first_frame）：用白膜渲帧或 Seedream 写实关键帧。
尾帧（last_frame）：可选，用于动作终态锁定。
```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}, "role": "first_frame"}
```

## 多参考图（H3 独有能力，≤9 张）

可以同时挂：
- 1 张白膜首帧（构图锁定）
- 1 张角色定妆照（一致性锁定）
- 1 张场景参考（环境锁定）
按顺序放，前面的权重更高。

## 计费

按 token：输出时长×宽×高×帧率÷1024 × ¥0.07/千tok。
768P 6s ≈ 195K tok ≈ ¥13.7。
图生视频含首帧输入 token（多图参考会更贵）。
