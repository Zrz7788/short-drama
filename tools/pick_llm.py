# -*- coding: utf-8 -*-
"""实测文本模型：哪些能用、哪个能稳定输出合法 JSON。

我们的第 1/2/5/6 阶段都要求模型返回**可解析的 JSON**（剧本、实体、分镜）。
所以选 LLM_MODEL 不能只看名字，得实测它是否听话。

控制台的显示名和 API 的模型 ID 不是一回事（"Doubao-Seed-2.1-pro" vs
`doubao-seed-2-1-pro-260915`），所以这里直接拿候选 ID 去试。

成本：每次调用只有几十个 token，几分钱。
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C   # noqa: E402

BASE = (os.environ.get("ARK_BASE_URL")
        or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
KEY = os.environ.get("ARK_API_KEY") or ""
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}

CANDIDATES = [
    "doubao-seed-2-1-pro-260915",
    "doubao-seed-2-1-pro-260628",
    "doubao-seed-2-0-pro-260215",
    "deepseek-v4-pro-ga-260813",
    "doubao-seed-2-1-turbo-260628",
    "doubao-seed-2-1-lite-260915",
    "glm-5-3-flash-260828",
]

PROMPT = ('只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。'
          '格式：{"hook": "一句话钩子", "beats": ["钩子","升级","扣子"]}')


def ask(model):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 200,
        "temperature": 0.3,
    }).encode()
    r = urllib.request.Request(BASE + "/chat/completions", data=body,
                               method="POST", headers=H)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            d = json.loads(resp.read().decode())
        txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return "ok", txt, d.get("usage") or {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            code = (json.loads(raw).get("error") or {}).get("code") or ""
            msg = (json.loads(raw).get("error") or {}).get("message") or ""
        except Exception:
            code, msg = "", raw[:100]
        return "http%s:%s" % (e.code, code), msg[:90], {}
    except Exception as e:
        return "err", str(e)[:90], {}


def json_ok(txt):
    """能不能直接 json.loads —— 这是流水线真正需要的能力。"""
    t = (txt or "").strip()
    if t.startswith("```"):
        # 我们的 extract_json 能处理围栏，但原生就干净更好
        return "fenced"
    try:
        json.loads(t)
        return "clean"
    except Exception:
        return "no"


print("=" * 78)
print("文本模型实测（要求：能调用 + 能输出可解析 JSON）")
print("=" * 78)
print("  %.60s..." % PROMPT)
print()
print("%-32s %-22s %-8s %s" % ("模型 ID", "调用结果", "JSON", "用量"))
print("-" * 78)
results = []
for m in CANDIDATES:
    st, txt, usage = ask(m)
    if st == "ok":
        j = json_ok(txt)
    else:
        j = "-"
    u = ("in %s / out %s" % (usage.get("prompt_tokens"), usage.get("completion_tokens"))
         if usage else "")
    print("%-32s %-22s %-8s %s" % (m, st, j, u))
    if st == "ok":
        results.append((m, j, txt))
        if j != "clean":
            print("      内容: %s" % (txt or "")[:110].replace("\n", " "))

print()
print("=" * 78)
usable = [m for m, j, _ in results if j in ("clean", "fenced")]
clean = [m for m, j, _ in results if j == "clean"]
print("  能调用      : %d / %d" % (len(results), len(CANDIDATES)))
print("  能直接解析JSON: %s" % (", ".join(clean) if clean else "无"))
print("  需去围栏才可解析: %s"
      % (", ".join(m for m, j, _ in results if j == "fenced") or "无"))
if clean:
    print("\n  建议写进 .env：")
    print("    LLM_MODEL=%s" % clean[0])
