---
name: ark-seedance
description: "火山方舟（Ark）Seedance 视频生成与 Seedream 图片生成的调用参考：模型 ID、单价、Draft 样片模式、任务轮询流程与计费要点。编写视频/图片生成脚本、选择模型档位或估算 AI 短剧成本时使用。"
whenToUse: "需要调用火山方舟生成视频或图片、批量出片、选择 Seedance/Seedream 档位、估算抽卡成本，或排查 Ark 接口报错时。"
---

# 火山方舟 Seedance / Seedream 调用参考

## 认证与端点

- API Key 从环境变量 `ARK_API_KEY` 读取，切勿写入会被提交的文件。
- 国内站 base URL：`https://ark.cn-beijing.volces.com/api/v3`
- 鉴权头：`Authorization: Bearer $ARK_API_KEY`

## 本机环境约束（重要）

本机沙箱下 **不要用 `curl.exe` 或 PowerShell 的 `Invoke-WebRequest` 调 API**：两者走 Windows
schannel，会因凭证不可用而报 `SEC_E_NO_CREDENTIALS`，TLS 握手直接失败。

**改用 Python `urllib`/`requests` 或 Node `fetch`**——两者走 OpenSSL，实测可正常访问 Ark。

## 视频模型

| 档位 | 模型 ID | 分辨率 | 单次时长 | 单价 |
|---|---|---|---|---|
| 旗舰 | `doubao-seedance-2-5-260628` | 480p/720p/1080p/4k | 4–15s | ¥1.45/秒 |
| 标准 | `doubao-seedance-2-0-260128` | 480p/720p/1080p/4k | 4–15s | ¥1.00/秒 |
| 快速 | `doubao-seedance-2-0-fast-260128` | 480p/720p | 4–15s | 待核对 |
| 便宜 | `doubao-seedance-2-0-mini-260615` | 480p/720p | 4–15s | 待核对 |
| 2.5 样片 | `doubao-seedance-2-5-260628` + `draft=true` | 480p | 4–15s | ≈¥0.26/秒 |

- **`fast` 与 `mini` 传 1080p 或 4k 会返回 400**，只能出 480p/720p。要 1080P 只能用 2.5 或 2.0 pro。
- `duration` 支持 **4–15 秒**，4 秒镜头直接传 4，不必按 5 秒取整。
- Seedance 2.x **原生支持音频生成与唇形同步**（`generate_audio` 默认 `true`），对白、音效、环境音可一步出，
  不需要另配口型服务；也可传 `reference_audio` 用自有配音驱动口型。

## Draft 样片模式（省钱关键）

先用 480P 低成本抽卡，确认满意后用样片 ID 出 1080P 成片；模型会复用该样片的提示词、参考素材、
`seed`、`ratio`、`duration` 做二次推理，保证成片与样片结构与运动一致。

- 开关：创建任务时传 `"draft": true`
- 官方测算：5 秒 1080P 镜头、抽卡 4 次命中 → **省 57%**；20 抽 1 → **省 77%**
- 由上述两个数据点反推，480P 样片单价 ≈ **¥0.26/秒**（约 1080P 的 1/5.6）
- **选型结论**：抽卡 1 次命中时直出最省；**抽卡 ≥2 次时 Draft 全面胜出**，抽得越多赢得越多。
  虽然 2.5 单价高于 2.0 pro，叠加 Draft 后总成本反而更低。
- 成片的局部纹理、复杂花纹、小字、人群人数可能与样片有细微差异，但镜头结构与运动表现会延续。

## 图片模型

| 模型 | ID |
|---|---|
| Seedream 4.0 | `doubao-seedream-4-0-250828` |
| Seedream 3.0 | `doubao-seedream-3-0-t2i-250415` |
| Seedream 5.0 pro | 已上线，**完整 ID 需在控制台确认，勿臆测后缀** |

图片接口：`POST /api/v3/images/generations`。

## 调用流程（异步任务）

1. `POST /api/v3/contents/generations/tasks` 创建任务，拿回任务 ID
2. `GET /api/v3/contents/generations/tasks/{id}` 轮询，间隔 5–10 秒，勿高频
3. 状态机：`queued → running → succeeded / failed / cancelled / expired`
4. 成功后取 `content.video_url` **立即下载**，URL 会过期
5. 5 秒 1080P 生成通常耗时 60–150 秒，轮询超时建议设 300 秒以上

请求体用官方 `content` 数组，文本与参考素材混排：

```json
{
  "model": "doubao-seedance-2-5-260628",
  "content": [
    {"type": "text", "text": "竖屏近景，女主角回头，电影感自然光"},
    {"type": "image_url", "image_url": {"url": "https://.../ref.png"}, "role": "reference_image"}
  ],
  "ratio": "9:16",
  "duration": 4,
  "resolution": "1080p",
  "draft": true,
  "generate_audio": true,
  "watermark": false
}
```

## 短剧成本要点

- 成本大头是**抽卡次数**，不是模型单价。把可用率从 3 次压到 2 次，比换便宜模型省得多。
- 角色一致性靠"首帧图先定死 + 参考图 + 记录 seed"，不要纯文生视频硬编。
- 单集 80 秒 ≈ 20 镜 × 4 秒；2.5 + Draft 抽卡 3 次 ≈ **¥178 视频成本 / 集**。
- 单集不贵，整部剧（60–100 集）才是预算主体。

## 避坑

- 网上关于 Ark 视频计费的**AI 生成 SEO 文章数字互相矛盾**（同一页出现 10 秒视频消耗 2200 与 5000 tokens
  两种说法），不要采信；单价以控制台模型详情页为准。
- 创建任务前先确认控制台已开通对应模型权限，否则报权限错误。
- 失败任务保留任务 ID 后重查，不要立即重复创建，避免重复计费。
