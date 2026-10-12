# -*- coding: utf-8 -*-
r"""把 dsh-workbuddy-connect 注册进 profile 的 bundles。

## 问题

profile 的 `package.json` 里：
  - `dependencies`      有 dsh-workbuddy-connect 0.7.3  ✅
  - `dsh.profile.bundles`  **没有它**  ❌

DSH 启动时按 `bundles` 加载插件。所以包装了、依赖也补齐了、插件诊断
（`dsh plugin exec ... status`）也显示 `signed in`、积分正常，
**但界面里就是不出现「WorkBuddy」分组**——因为它从来没被加载。

`dsh plugin --profile desktop add` 只写了 dependencies，漏了 bundles。
（同一次操作还有另一个 bug：把包名误插到 pnpm-workspace.yaml 的
`allowBuilds:` 下面而不给值，导致 pnpm 报 ERR_PNPM_IGNORED_BUILDS。
那个已在 tools/_fix_wb_peers.py 里修掉。）

## 修法

把包名追加进 `dsh.profile.bundles`，**只加这一项**，其余原样。
改完必须重启 DSH 才生效。
"""
import json
import os
import shutil
import sys

PROF = os.path.expanduser(r"~/.dsh/profiles/desktop")
PKG = os.path.join(PROF, "package.json")
NAME = "dsh-workbuddy-connect"

if not os.path.exists(PKG):
    print("找不到 %s" % PKG)
    sys.exit(1)

d = json.load(open(PKG, encoding="utf-8"))
prof = (d.setdefault("dsh", {})).setdefault("profile", {})
bundles = prof.setdefault("bundles", [])

print("=== 修改前 bundles（%d 个）===" % len(bundles))
for b in bundles:
    print("  %s" % b)

if NAME in bundles:
    print("\n已经在 bundles 里，无需修改。")
    sys.exit(0)

if NAME not in (d.get("dependencies") or {}):
    print("\n⚠️ %s 不在 dependencies 里，先装它再跑本脚本。" % NAME)
    sys.exit(1)

# 放在同类（客户端插件）之后、保持可读：插到末尾即可，DSH 不依赖顺序
prof["bundles"] = bundles + [NAME]

bak = PKG + ".bak-before-bundles"
if not os.path.exists(bak):
    shutil.copy2(PKG, bak)
    print("\n已备份 -> %s" % bak)

with open(PKG, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
    f.write("\n")

d2 = json.load(open(PKG, encoding="utf-8"))
b2 = d2["dsh"]["profile"]["bundles"]
print("\n=== 修改后 bundles（%d 个）===" % len(b2))
for b in b2:
    print("  %s%s" % (b, "   ← 新加" if b == NAME else ""))
print("\ndependencies 数量: %d" % len(d2.get("dependencies", {})))
print("JSON 合法、可解析 ✅")
