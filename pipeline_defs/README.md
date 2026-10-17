# 殡仪馆的阿九 — AI 短剧生产管线

## 项目概述
AI 竖屏短剧（漫剧）生产线：剧本 → 分镜 → 白膜 → H3/Seedance 出片 → 配音 → 拼片。

## 目录结构
- `skill/` — 创作技能（short-drama-write/storyboard/assets/video-prompts/produce）
- `knowledge/01-制作方法/` — 方法论精华（白膜管线/投喂规范/案例索引/生态清单）
- `pipeline/` — 生产管线脚本（stage_script/storyboard/prompts/refs/firstframes/video + 门禁）
- `data/` — 提示词数据
- `剧集/` — 剧本和分镜
- `HANDOFF.md` — 项目交接文档

## 生产管线
```
剧本（short-drama-write skill）
  → 入库（stage_script.py --from-md）
  → 分镜（stage_storyboard.py --scene N）
  → 白膜（Blender Python）
  → 白模自检（三查：穿模/转弯/跳帧）
  → 提示词定稿（stage_prompts.py）
  → 三层门禁（precheck + compliance_gate + 人工审）
  → 首帧出图（Seedream）
  → H3 图生视频（MiniMax H3 · 768P）
  → 中文配音（speech-2.8）
  → 剪辑拼片（ffmpeg）
```

## 环境
- Python 3.13+ / Blender 4.2 LTS / Node.js 22+
- 火山方舟 API（Seedance/Seedream）
- MiniMax API（H3 视频 / speech-2.8 TTS）

## 许可
项目原创内容版权归创作者所有。第三方资料归原作者所有（MIT/Apache 2.0）。
