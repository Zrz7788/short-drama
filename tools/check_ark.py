# -*- coding: utf-8 -*-
"""Ark 配置检查 —— 零成本确认密钥与模型权限。

原理：发一个**故意参数错误**的请求，从错误码反推状态。

    401 / 鉴权类  -> 密钥无效
    403 / 权限类  -> 密钥有效，但模型权限没开
    400 参数类    -> 密钥有效（服务端读到了请求，只是参数不对）

这样不用花一分钱，就能把"key 错了"和"没开模型权限"这两种失败区分开——
它们报错长得很像，而处理方式完全不同。

顺带检查 .env 有没有被读到、LLM_MODEL 配了没有。

用法： python tools/check_ark.py [--json]
退出码 0 = 密钥可用；1 = 有问题。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C   # noqa: E402  触发 .env 加载
from pipeline import ark as A      # noqa: E402

DUMP = os.path.join(C.ROOT, ".tools", "ark-debug")


def probe(path, body, method="POST"):
    url = A.BASE_DEFAULT.rstrip("/") + path
    key = os.environ.get("ARK_API_KEY") or ""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def classify(status, txt):
    """从错误码与错误体判断状态。

    ⚠️ 方舟对"模型没开通"用的是 **404 InvalidEndpointOrModel.NotFound**，
    而不是 403 —— 报错原文里写着 "does not exist **or you do not have access
    to it**"。第一版把它当成"端点不存在"，把结论报成了"状态不明"。
    判据要看错误体里的字，不能只看 HTTP 状态码。

    另一头：任务查询返回 `ResourceNotFound` 其实是**鉴权成功**的证据——
    服务端读懂了请求、去查了资源，只是资源不存在。密钥无效会在查资源之前
    就返回 401。所以这一条应当判为"密钥可用"。
    """
    t = (txt or "").lower()
    try:
        code = (json.loads(txt).get("error") or {}).get("code") or ""
    except Exception:
        code = ""
    if status == 401 or "authentication" in t or "invalidapikey" in t \
            or "invalid api key" in t:
        return "bad_key", "密钥无效"
    if "you do not have access" in t or "not activated" in t \
            or "no permission" in t or "not authorized" in t:
        return "no_permission", "密钥有效，但该模型没开通"
    if code == "ResourceNotFound" or "is not found" in t:
        return "ok", "密钥有效（查了个不存在的资源，属预期）"
    if status == 403:
        return "no_permission", "密钥有效，但被拒绝访问"
    if status == 400:
        return "ok", "密钥有效（只是参数不对，正是我们故意的）"
    if code == "InvalidEndpointOrModel.NotFound":
        return "bad_model", "密钥有效，但模型 ID 不存在（核对控制台的模型列表）"
    if status == 404:
        return "not_found", "端点或资源不存在"
    if status is None:
        return "network", "请求失败（网络或端点问题）"
    return "unknown", "HTTP %s" % status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    os.makedirs(DUMP, exist_ok=True)

    key = os.environ.get("ARK_API_KEY") or ""
    llm_model = os.environ.get("LLM_MODEL") or ""
    s1, r1 = probe("/images/generations", {"model": A.MODEL_IMAGE, "size": "1K"})
    s2, r2 = probe("/contents/generations/tasks/nonexistent-id", None, "GET")
    c1, v1 = classify(s1, r1)
    c2, v2 = classify(s2, r2)

    if args.json:
        print(json.dumps({"env_loaded": C.ENV_LOADED, "key_len": len(key),
                          "llm_model": llm_model, "image": {"status": s1, "verdict": c1},
                          "task": {"status": s2, "verdict": c2}},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print("Ark 配置检查（零成本：故意发错参数的请求，看错误码）")
        print("=" * 64)
        print("  .env 载入条数 : %d" % C.ENV_LOADED)
        print("  ARK_API_KEY   : %s" % ("已设置（长度 %d）" % len(key) if key else "**未设置**"))
        print("  base url      : %s" % A.BASE_DEFAULT)
        print("  LLM_MODEL     : %s" % (llm_model or "**未设置**（第 1/2/5/6 阶段会报错）"))
        print("  默认图片模型  : %s" % A.MODEL_IMAGE)
        print("  默认视频模型  : %s" % A.MODEL_VIDEO_25)
        print()
        print("  [图片接口] 故意缺 prompt -> HTTP %s" % s1)
        print("             %s" % v1)
        print("  [视频接口] 故意用不存在的任务 ID -> HTTP %s" % s2)
        print("             %s" % v2)
        print()
        print("=" * 64)
        if c1 == "ok" or c2 == "ok" or c1 == "no_permission" or c2 == "no_permission" \
                or c1 == "bad_model" or c2 == "bad_model":
            print("结论：**密钥有效**（服务端接受并处理了请求）。")
        if c1 == "bad_key" or c2 == "bad_key":
            print("结论：**密钥无效**。去控制台核对，或换一个。")
        if c1 == "no_permission" or c2 == "no_permission":
            print("      但**模型权限没开**：去方舟控制台 →「开通管理」→ 开通对应模型。")
            print("      注意方舟对未开通的模型返回的是 404 而不是 403，")
            print("      报错原文是「does not exist **or you do not have access to it**」。")
        if c1 == "bad_model" or c2 == "bad_model":
            print("      但**模型 ID 不对**：去控制台核对模型列表，")
            print("      然后用 ARK_IMAGE_MODEL / ARK_VIDEO_MODEL 覆盖默认值。")
        if c1 not in ("ok", "no_permission", "bad_model") \
                and c2 not in ("ok", "no_permission", "bad_model"):
            print("结论：状态不明，看下面的原始响应。")
        if not llm_model:
            print("\n注意：LLM_MODEL 未设置 —— 图片/视频阶段能跑，")
            print("      但第 1/2/5/6 阶段（剧本/实体/分镜/提示词）会报错。")
        print("\n原始响应片段：")
        print("  " + (r1 or "")[:300].replace("\n", "\n  "))
        print("  " + (r2 or "")[:300].replace("\n", "\n  "))

    with open(os.path.join(DUMP, "check_ark.json"), "w", encoding="utf-8") as f:
        json.dump({"image": {"status": s1, "body": r1[:2000]},
                   "task": {"status": s2, "body": r2[:2000]}},
                  f, ensure_ascii=False, indent=2)
    return 0 if ("ok" in (c1, c2)) else 1


if __name__ == "__main__":
    sys.exit(main())
