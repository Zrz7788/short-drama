# -*- coding: utf-8 -*-
"""查出当前 key 实际能用哪些图片/视频模型 —— 全程免费，**不会创建任何任务**。

背景：模型可用性是**按账号开通**的，不是全局的。代码里写死的默认 ID 很可能在
你的账号上没开通，报错却是 "does not exist or you do not have access to it"，
看着像权限问题，实际是 ID 不对。

    python tools/probe_models.py

三步：
  1. GET /models —— 只读接口，列出方舟的全局模型目录（本账号 135 个）
  2. 图片模型：发一个**故意缺 prompt** 的请求。
     缺 prompt 必然失败，所以永远创建不出任何东西。
       400 -> 模型已开通（服务端读到了请求，只是参数不对）
       404 -> 没开通
  3. 视频模型：发一个 **duration 非法**的请求。
     ⚠️ 这一步最初写错了：当时只传 model + content，服务端**真的把任务建出来了**
     （HTTP 200），一下子提交了 4 个真实视频任务，按实际用量计费，而且没有
     可用的取消接口（DELETE 对 running 的任务返回 409）。
     现在改成传 `duration: 999`——任何一个正常模型都会因此 400，
     **在建任务之前就被参数校验拦下**。
     所以 400 = 已开通，404 = 没开通，且绝不会产生费用。
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C   # noqa: E402  触发 .env 加载

BASE = (os.environ.get("ARK_BASE_URL")
        or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
KEY = os.environ.get("ARK_API_KEY") or ""
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}


def call(path, body=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def err_code(body):
    try:
        return (json.loads(body).get("error") or {}).get("code") or ""
    except Exception:
        return ""


def probe_image(mid):
    """缺 prompt -> 必然失败，创建不出任何东西。"""
    st, body = call("/images/generations", {"model": mid, "size": "1K"})
    return st, err_code(body)


def probe_video(mid):
    """duration 非法 -> 在建任务之前就被拦下。**绝不能省略这个字段**，
    否则服务端会真的创建任务（实测踩过）。"""
    st, body = call("/contents/generations/tasks", {
        "model": mid,
        "content": [{"type": "text", "text": "probe"}],
        "duration": 999,          # 合法域 4-15，故意越界
    })
    return st, err_code(body)


def main():
    print("=" * 72)
    print("模型可用性探测（免费，不创建任何任务）")
    print("=" * 72)
    print("  base: %s" % BASE)

    st, body = call("/models", None, "GET")
    if st != 200:
        print("\n  GET /models 失败 HTTP %s：%s" % (st, body[:200]))
        print("  （这个接口不通就只能靠控制台「开通管理」看）")
        return 1
    ids = [x.get("id") for x in (json.loads(body).get("data") or [])
           if isinstance(x, dict)]
    print("  目录里共 %d 个模型\n" % len(ids))

    img_cands = [i for i in ids if "seedream" in (i or "").lower()]
    vid_cands = [i for i in ids if "seedance" in (i or "").lower()]

    print("--- 图片模型（Seedream）---")
    img_ok = []
    for m in sorted(img_cands):
        st, code = probe_image(m)
        usable = (st == 400)
        if usable:
            img_ok.append(m)
        print("  %-36s HTTP %-4s %s" % (m, st, "✅ 已开通" if usable else "❌ 未开通"))

    print("\n--- 视频模型（Seedance）---")
    vid_ok = []
    for m in sorted(vid_cands):
        st, code = probe_video(m)
        usable = (st == 400)
        if usable:
            vid_ok.append(m)
        print("  %-36s HTTP %-4s %s  %s"
              % (m, st, "✅ 已开通" if usable else "❌ 未开通", code[:26]))

    print("\n" + "=" * 72)
    print("结论")
    print("=" * 72)
    print("  已开通的图片模型:")
    for m in (img_ok or ["（无）"]):
        print("    " + m)
    print("  已开通的视频模型:")
    for m in (vid_ok or ["（无）"]):
        print("    " + m)
    if img_ok:
        print("\n  建议写进 .env（覆盖代码默认值）：")
        print("    ARK_IMAGE_MODEL=%s" % img_ok[0])
    print("\n  代码里当前的默认值：")
    from pipeline import ark as A
    print("    ARK_IMAGE_MODEL 默认 = %s  %s"
          % (A.MODEL_IMAGE, "✅ 已开通" if A.MODEL_IMAGE in img_ok else "❌ 未开通"))
    print("    视频默认       = %s  %s"
          % (A.MODEL_VIDEO_25, "✅ 已开通" if A.MODEL_VIDEO_25 in vid_ok else "❌ 未开通"))
    return 0 if (img_ok and vid_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
