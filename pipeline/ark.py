# -*- coding: utf-8 -*-
"""火山方舟（Ark）客户端：图像生成（同步）与视频生成（异步任务）。

字段名不是猜的——
  图像：请求体与响应结构取自一个真实跑通的第三方 DSH 插件实现
        （response_format=url、image 为 data URL 数组、响应取 data[].url 或 b64_json）
  视频：创建任务返回任务 id，查询返回 status 与 content.video_url，
        来自两个独立来源一致的公开文档

**视频响应结构未对着真实服务验证过**（没有 key）。所以这里的解析一律防御式：
取不到预期字段时，把**原始响应**落盘到 .tools/ark-debug/ 并在报错里给出文件路径。
第一次真实调用要么成功，要么直接告诉我们该改哪里——而不是抛一句无从下手的 KeyError。

## 已知模型 ID

| 用途 | ID | 来源与置信度 |
|---|---|---|
| 图片（通用） | doubao-seedream-4-0-250828 | 官方控制台模型页 · 高 |
| 图片（高保真） | doubao-seedream-5-0-pro-260628 | 第三方实现配置 · 中 |
| 图片（中间档） | doubao-seedream-4-5-251128 | 第三方实现配置 · 中 |
| 视频 2.5 | doubao-seedance-2-5-260628 | 第三方网关表 · 中 |
| 视频 2.0 pro | doubao-seedance-2-0-260128 | 两个来源交叉 · 高 |

环境变量：ARK_API_KEY（必填）、ARK_BASE_URL、ARK_IMAGE_MODEL、ARK_VIDEO_MODEL
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

import pipeline.common as C

BASE_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"

MODEL_IMAGE = "doubao-seedream-4-0-250828"
MODEL_IMAGE_HIFI = "doubao-seedream-5-0-pro-260628"
MODEL_IMAGE_MID = "doubao-seedream-4-5-251128"
MODEL_VIDEO_25 = "doubao-seedance-2-5-260628"
MODEL_VIDEO_20 = "doubao-seedance-2-0-260128"

DEBUG_DIR = os.path.join(C.ROOT, ".tools", "ark-debug")

# 官方合法域：总像素 >= 2560x1440 且 <= 4096x4096，宽高比 [1/16, 16]
SIZE_PRESETS = ("1K", "2K", "4K")


class ArkError(Exception):
    pass


def resolve_config(base_url=None, api_key=None, image_model=None, video_model=None):
    return {
        "base_url": (base_url or os.environ.get("ARK_BASE_URL") or BASE_DEFAULT).rstrip("/"),
        "api_key": api_key or os.environ.get("ARK_API_KEY") or "",
        "image_model": image_model or os.environ.get("ARK_IMAGE_MODEL") or MODEL_IMAGE,
        "video_model": video_model or os.environ.get("ARK_VIDEO_MODEL") or MODEL_VIDEO_25,
    }


def require_key(api_key=None):
    cfg = resolve_config(api_key=api_key)
    if not cfg["api_key"]:
        raise ArkError(
            "ARK_API_KEY 未设置。\n"
            "设置方式（推荐写进工作区 .env，已被 .gitignore 挡住）：\n"
            "  ARK_API_KEY=你的密钥\n"
            "或： $env:ARK_API_KEY='...'\n"
            "另需在火山方舟控制台开通对应模型的调用权限。")
    return cfg


def _dump_debug(tag, payload):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, "%s-%s.json" % (tag, time.strftime("%Y%m%d-%H%M%S")))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _post(url, body, api_key, timeout, retries, tag):
    """POST JSON。429 按退避重试；4xx 一次性判定（重试无意义）。"""
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(1, retries + 2):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + api_key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
            try:
                return json.loads(raw)
            except ValueError:
                p = _dump_debug(tag, {"raw": raw[:20000]})
                raise ArkError("响应不是合法 JSON（原始内容已存 %s）" % p)
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body_text).get("error") or {}
            except ValueError:
                err = {"message": body_text[:300]}
            code = err.get("code") or ""
            msg = err.get("message") or body_text[:200]
            if e.code == 429 and attempt <= retries:
                last = "HTTP 429 限流：%s" % msg
                time.sleep(25 * attempt)
                continue
            if e.code == 401 or "AuthenticationError" in code:
                raise ArkError("鉴权失败（HTTP 401）：%s\n"
                               "检查 ARK_API_KEY 是否正确、是否已开通该模型权限。" % msg)
            if 400 <= e.code < 500:
                raise ArkError("参数或权限问题（HTTP %s / %s）：%s\n"
                               "请求体已存 %s"
                               % (e.code, code or "-", msg,
                                  _dump_debug(tag, {"request": body, "error": err})))
            last = "HTTP %s：%s" % (e.code, msg)
        except (urllib.error.URLError, TimeoutError) as e:
            last = "%s: %s" % (type(e).__name__, e)
        if attempt <= retries:
            time.sleep(min(2 ** attempt, 10))
    raise ArkError("调用失败（已重试）：%s" % last)


def _get(url, api_key, timeout, tag):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "replace")
        raise ArkError("查询失败（HTTP %s）：%s" % (e.code, body_text[:200]))
    except (urllib.error.URLError, TimeoutError) as e:
        raise ArkError("查询失败：%s: %s" % (type(e).__name__, e))
    try:
        return json.loads(raw)
    except ValueError:
        p = _dump_debug(tag, {"raw": raw[:20000]})
        raise ArkError("响应不是合法 JSON（原始内容已存 %s）" % p)


def to_data_url(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
    with open(path, "rb") as f:
        return "data:image/%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


def extract_images(resp, tag="image"):
    """从响应里取出图片 URL 或 b64。

    真实结构是 data: [{url}] 或 [{b64_json}]。取不到就把原始响应落盘再报错——
    比抛一个 KeyError 有用得多。
    """
    items = (resp or {}).get("data")
    if not isinstance(items, list):
        p = _dump_debug(tag, {"response": resp})
        raise ArkError("响应里没有 data 数组，无法取图。原始响应已存 %s\n"
                       "顶部字段：%s" % (p, sorted((resp or {}).keys())[:12]))
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("url"), str) and it["url"]:
            out.append(it["url"])
        elif isinstance(it.get("b64_json"), str) and it["b64_json"]:
            out.append("data:image/png;base64," + it["b64_json"])
    if not out:
        p = _dump_debug(tag, {"response": resp})
        raise ArkError("响应成功但没有图片结果。原始响应已存 %s" % p)
    return out


def generate_image(prompt, size="2K", reference=None, references=None, model=None,
                   base_url=None, api_key=None, timeout=300, retries=2):
    """同步生成图片。

    reference  单张参考图路径（图生图）
    references 多张参考图路径列表（官方支持最多 14 张）
    两张都传时合并去重；都没有则走文生图。

    返回 {"model": 实际模型, "urls": [...], "raw": 原始响应}
    """
    cfg = require_key(api_key)
    model = model or cfg["image_model"]
    if size not in SIZE_PRESETS and "x" not in size:
        raise ArkError("size 只能是 %s 或 '宽x高'，收到 %r" % (SIZE_PRESETS, size))

    refs = []
    for r in ([reference] if reference else []) + list(references or []):
        if r and r not in refs:
            refs.append(r)
    if len(refs) > 14:
        raise ArkError("参考图最多 14 张，收到 %d 张" % len(refs))

    body = {
        "model": model,
        "prompt": prompt,
        "response_format": "url",
        "size": size,
        "watermark": False,
    }
    if refs:
        for r in refs:
            if not os.path.exists(r):
                raise ArkError("参考图不存在：%s" % r)
        # 官方收的是 data URL 数组，不是 URL 字符串
        body["image"] = [to_data_url(r) for r in refs]

    resp = _post(cfg["base_url"] + "/images/generations", body,
                 cfg["api_key"], timeout, retries, "image")
    urls = extract_images(resp, "image")
    # 官方不保证回传 model，回落请求值以便追溯
    used = (resp.get("model") or "").strip() or model
    return {"model": used, "urls": urls, "raw": resp, "n_references": len(refs)}


# ------------------------------------------------------------------ 视频（异步任务）

def create_video_task(prompt, model=None, ratio="9:16", duration=4,
                      resolution="1080p", draft=False, reference_image=None,
                      generate_audio=True, watermark=False, extra_text=None,
                      base_url=None, api_key=None, timeout=120, retries=2):
    """创建视频生成任务，返回 {"task_id":..., "model":..., "raw":...}"""
    cfg = require_key(api_key)
    model = model or cfg["video_model"]
    content = [{"type": "text",
                "text": prompt if not extra_text else "%s\n%s" % (extra_text, prompt)}]
    if reference_image:
        if not os.path.exists(reference_image):
            raise ArkError("参考图不存在：%s" % reference_image)
        content.append({"type": "image_url",
                        "image_url": {"url": to_data_url(reference_image)},
                        "role": "reference_image"})
    body = {
        "model": model,
        "content": content,
        "ratio": ratio,
        "duration": duration,
        "resolution": resolution,
        "generate_audio": generate_audio,
        "watermark": watermark,
    }
    if draft:
        body["draft"] = True

    resp = _post(cfg["base_url"] + "/contents/generations/tasks", body,
                 cfg["api_key"], timeout, retries, "video-create")
    tid = resp.get("id") or resp.get("task_id")
    if not tid:
        p = _dump_debug("video-create", {"response": resp})
        raise ArkError("创建任务后拿不到任务 id。原始响应已存 %s\n"
                       "顶部字段：%s" % (p, sorted(resp.keys())[:12]))
    return {"task_id": tid, "model": resp.get("model") or model, "raw": resp}


def get_video_task(task_id, base_url=None, api_key=None, timeout=60):
    cfg = require_key(api_key)
    return _get("%s/contents/generations/tasks/%s" % (cfg["base_url"], task_id),
                cfg["api_key"], timeout, "video-get")


TERMINAL = ("succeeded", "success", "failed", "error", "cancelled", "canceled", "expired")


def video_url_of(resp):
    """从查询响应里取视频地址，兼容几种可能的嵌套。"""
    if not isinstance(resp, dict):
        return None
    content = resp.get("content")
    if isinstance(content, dict) and isinstance(content.get("video_url"), str):
        return content["video_url"]
    if isinstance(content, list):
        for it in content:
            if isinstance(it, dict) and isinstance(it.get("video_url"), str):
                return it["video_url"]
    if isinstance(resp.get("video_url"), str):
        return resp["video_url"]
    return None


def wait_video_task(task_id, timeout=600, interval=8, base_url=None, api_key=None,
                    on_poll=None):
    """轮询直到终态或超时。返回最后一次响应（附带 _elapsed）。"""
    t0 = time.time()
    last = {}
    while True:
        last = get_video_task(task_id, base_url=base_url, api_key=api_key)
        status = str(last.get("status") or "").lower()
        if on_poll:
            on_poll(status, time.time() - t0)
        if status in TERMINAL:
            last["_elapsed"] = round(time.time() - t0, 1)
            return last
        if time.time() - t0 > timeout:
            p = _dump_debug("video-timeout", {"task_id": task_id, "last": last})
            raise ArkError("任务 %s 在 %.0f 秒内未结束（最后状态 %r）。"
                           "最后一次响应已存 %s" % (task_id, timeout, status, p))
        time.sleep(interval)


def download(url, dest):
    """下载到本地，返回字节数。data URL 也能处理。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    if url.startswith("data:"):
        head, _, b64 = url.partition(",")
        blob = base64.b64decode(b64)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "dsh-pipeline"})
        with urllib.request.urlopen(req, timeout=600) as r:
            blob = r.read()
    with open(dest, "wb") as f:
        f.write(blob)
    return len(blob)


def add_ark_args(parser):
    parser.add_argument("--base-url", default=None, help="Ark 端点（默认 ARK_BASE_URL）")
    parser.add_argument("--image-model", default=None, help="图片模型 ID")
    parser.add_argument("--video-model", default=None, help="视频模型 ID")
    return parser
