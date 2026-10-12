# -*- coding: utf-8 -*-
r"""从插件里挖出**国际版（WorkBuddy AI）的内置模型目录**。

理由：用户想要国际版是冲着 GPT 系等国外模型。**先确认它到底提供什么**，
再决定值不值得为它注册 GitHub —— 不然可能白折腾一场。
插件状态里报了 `Static fallback models: 16`，说明有内置目录。
"""
import json
import os
import re

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".tools", "plugin-inspect")
ver = None
if os.path.isdir(BASE):
    for d in sorted(os.listdir(BASE), reverse=True):
        if d.startswith("dsh-workbuddy-connect-") and \
           os.path.isdir(os.path.join(BASE, d)):
            ver = d
            break
if not ver:
    print("插件未解包。先跑：python tools/inspect_plugin.py dsh-workbuddy-connect")
    raise SystemExit(1)
PKG = os.path.join(BASE, ver, "package")
print("分析：%s\n" % ver)

text = ""
for root, dirs, files in os.walk(PKG):
    dirs[:] = [d for d in dirs if d != "node_modules"]
    for f in files:
        if f.endswith((".js", ".mjs", ".cjs", ".ts")):
            try:
                text += open(os.path.join(root, f), encoding="utf-8",
                             errors="replace").read() + "\n"
            except OSError:
                pass

print("=" * 74)
print("1) 所有出现的模型名（按常见厂商关键词）")
print("=" * 74)
MODELS = ["gpt", "o1", "o3", "o4", "claude", "gemini", "grok", "llama",
          "glm", "deepseek", "kimi", "minimax", "hunyuan", "hy3", "qwen",
          "doubao", "seed"]
hits = {}
for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._\- ]{2,40})"', text):
    name = m.group(1).strip()
    low = name.lower()
    if any(k in low for k in MODELS):
        # 过滤掉明显是路径/字段名的
        if any(t in low for t in ("http", ".js", ".json", "null", "true")):
            continue
        hits[name] = hits.get(name, 0) + 1

for name, n in sorted(hits.items(), key=lambda x: (-x[1], x[0]))[:60]:
    print("  %-44s ×%d" % (name, n))

print("\n" + "=" * 74)
print("2) 直接找 GPT / Claude / Gemini 字样附近的上下文")
print("=" * 74)
for kw in ("gpt-", "claude", "gemini", "grok"):
    ms = list(re.finditer(r'"([^"]{0,30}%s[^"]{0,30})"' % re.escape(kw),
                          text, re.I))
    print("\n  [%s] 命中 %d 处" % (kw, len(ms)))
    seen = set()
    for m in ms[:12]:
        v = m.group(1)
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        print("      %s" % v[:70])

print("\n" + "=" * 74)
print("3) 找内置模型目录结构（fallback / BUILTIN_MODELS 之类）")
print("=" * 74)
for kw in ("fallbackModels", "FALLBACK_MODELS", "builtinModels",
           "BUILTIN_MODELS", "staticModels", "defaultModels"):
    n = text.count(kw)
    if n:
        print("  %-22s ×%d" % (kw, n))
        m = re.search(re.escape(kw) + r"\s*[:=]\s*", text)
        if m:
            seg = text[m.end():m.end() + 700]
            print("      片段: %s" % re.sub(r"\s+", " ", seg)[:600])
