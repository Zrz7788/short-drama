# -*- coding: utf-8 -*-
r"""验证 @honcho-ai/dsh-honcho 能不能在 dsh 0.2.0-rc.2 上加载。

## 为什么要验

它 README 明写「正在运行的 dsh，版本不高于 0.2.0-rc.1」，
而本机内核是 **0.2.0-rc.2**——比它声明支持的上限还新。

这正是 WorkBuddy 插件踩过的坑：插件 import 了宿主不提供的包，
`ERR_MODULE_NOT_FOUND` 直接崩。所以**先看它 import 了什么**，
再决定值不值得装。
"""
import os
import re

ROOT = r".tools/honcho/pkg/package"
PROFILE_NM = os.path.expanduser(r"~/.dsh/profiles/desktop/node_modules")

print("=" * 74)
print("1) 它 import 了哪些 @deepseek-ai/* 包（这些要宿主提供）")
print("=" * 74)
pat_from = re.compile(r"""from\s*["'](@deepseek-ai/[^"']+)["']""")
pat_dyn = re.compile(r"""import\(\s*["'](@deepseek-ai/[^"']+)["']""")
imports = set()
for cur, dirs, fs in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d != "node_modules"]
    for f in fs:
        if not f.endswith((".js", ".mjs", ".cjs")):
            continue
        t = open(os.path.join(cur, f), encoding="utf-8", errors="replace").read()
        imports.update(pat_from.findall(t))
        imports.update(pat_dyn.findall(t))

if not imports:
    print("  （没有直接 import @deepseek-ai/* —— 好消息）")
for i in sorted(imports):
    there = os.path.exists(os.path.join(PROFILE_NM, i, "package.json"))
    print("  %-44s %s" % (i, "在 profile 里" if there
                          else "**缺失（会 ERR_MODULE_NOT_FOUND）**"))

print()
print("=" * 74)
print("2) core-shim.js 的作用（兼容层？）")
print("=" * 74)
p = os.path.join(ROOT, "lib", "core-shim.js")
if os.path.exists(p):
    t = open(p, encoding="utf-8", errors="replace").read()
    # 找它的导出名与注释里的说明
    print("  文件 %d 字节" % len(t))
    head = "\n".join(t.splitlines()[:26])
    print("\n".join("  " + l for l in head.splitlines()))
    print()
    print("  它导出的名字：")
    for m in re.finditer(r"export\s+(?:async\s+)?(?:function|const|class)\s+(\w+)", t):
        print("    %s" % m.group(1))
else:
    print("  没有 core-shim.js")

print()
print("=" * 74)
print("3) peers.js（它怎么处理 peer 依赖）")
print("=" * 74)
p = os.path.join(ROOT, "lib", "peers.js")
if os.path.exists(p):
    t = open(p, encoding="utf-8", errors="replace").read()
    print("  文件 %d 字节" % len(t))
    for m in re.finditer(r"(?:require|import|from)\s*[\(]?\s*[\"']([^\"']+)[\"']", t):
        print("    依赖: %s" % m.group(1))
    print()
    print("  前 30 行：")
    print("\n".join("  " + l for l in t.splitlines()[:30]))
