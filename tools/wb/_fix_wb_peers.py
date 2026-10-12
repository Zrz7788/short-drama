# -*- coding: utf-8 -*-
r"""补齐 dsh-workbuddy-connect 缺失的 peer 依赖，并修复被写坏的 allowBuilds。

## 为什么要动 profile 的 node_modules

插件产物真的 `import` 了四个 profile 里没有的包：
    @deepseek-ai/dsh-atomic-write, dsh-home-paths, dsh-llm, dsh-llm-pi-ai
（`dsh-context` 等已装插件不 import 这些，所以它们没事。）

pnpm 配了 `autoInstallPeers: false`，所以这些 peer 不会被自动装上。
版本用 `0.2.0-rc.2`（= npm 的 `next` 标签，与桌面内核一致）。

## 为什么要立刻修回 allowBuilds

DSH 的 plugin manager 有个 **bug**：`dsh plugin --profile desktop add <name>`
会把 `<name>` 追加到 `allowBuilds:` 下面**而不给值**，
于是该键变成 null → pnpm 再次报 `ERR_PNPM_IGNORED_BUILDS` → 依赖检查失败。

所以顺序必须是：装完 → **立刻把 allowBuilds 修回显式 false**。

⚠️ 这两步不能只做前一步，否则插件又会因为依赖检查失败而加载不了。
"""
import os
import re
import subprocess
import sys

PROF = os.path.expanduser(r"~/.dsh/profiles/desktop")
WS = os.path.join(PROF, "pnpm-workspace.yaml")
PNPM = r"G:\deepseek\resources\runtime\pnpm\bin\pnpm.mjs"
NODE = r"G:\deepseek\resources\runtime\node\node.exe"
if not os.path.exists(NODE):
    NODE = r"C:\Users\zrz\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"

PEERS = ["@deepseek-ai/dsh-atomic-write", "@deepseek-ai/dsh-home-paths",
         "@deepseek-ai/dsh-llm", "@deepseek-ai/dsh-llm-pi-ai"]
VER = "0.2.0-rc.2"

print("=== 1) 安装缺失的 peer（用 profile 自带的 pnpm）===")
for exe in (PNPM,):
    if not os.path.exists(exe):
        print("找不到 pnpm: %s" % exe)
        sys.exit(1)
print("  pnpm: %s" % PNPM)
print("  node: %s" % NODE)

args = [NODE, PNPM, "add", "--save-exact"] + ["%s@%s" % (p, VER) for p in PEERS]
print("  执行: %s" % " ".join(args[2:]))
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
p = subprocess.run(args, cwd=PROF, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=1800, env=env)
print("  退出码: %s" % p.returncode)
for ln in ((p.stdout or "") + (p.stderr or "")).splitlines()[-25:]:
    print("    %s" % ln[:160])


def fix_allowbuilds():
    """把 allowBuilds 里没有值的键补成 false，并删掉误插的包名。"""
    raw = open(WS, encoding="utf-8").read()
    lines = raw.splitlines()
    out, fixed, removed = [], [], []
    in_block = False
    for ln in lines:
        m = re.match(r"^([A-Za-z])", ln)
        if m:
            in_block = ln.strip().startswith("allowBuilds:")
            out.append(ln)
            continue
        if in_block and ln.strip():
            s = ln.strip()
            if s.startswith("#"):
                out.append(ln)
                continue
            # 误插的条目：没有冒号，且看起来像 pkg@ver
            if ":" not in s:
                if re.match(r"^[\w@/.\-]+@[\w.\-]+$", s):
                    removed.append(s)
                    continue
                out.append(ln)
                continue
            key, _, val = s.partition(":")
            key = key.strip()
            val = val.strip()
            # 已经修过的（true/false）不动
            if val in ("true", "false"):
                out.append(ln)
                continue
            # 没值或是占位符 -> 明确拒绝
            out.append("%s%s: false"
                       % (ln[:len(ln) - len(ln.lstrip())], key))
            fixed.append(key)
        else:
            out.append(ln)
    new = "\n".join(out) + "\n"
    if new != raw:
        with open(WS, "w", encoding="utf-8") as f:
            f.write(new)
    return fixed, removed


print("\n=== 2) 修回 allowBuilds ===")
f1, r1 = fix_allowbuilds()
print("  补成 false 的键: %s" % (f1 or "无"))
print("  删掉的误插条目: %s" % (r1 or "无"))
print("\n  现在的内容：")
print("    " + open(WS, encoding="utf-8").read().replace("\n", "\n    "))

print("\n=== 3) 校验：再跑一次 pnpm install 看能否通过 ===")
p2 = subprocess.run([NODE, PNPM, "install"], cwd=PROF, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=1800, env=env)
print("  退出码: %s" % p2.returncode)
for ln in ((p2.stdout or "") + (p2.stderr or "")).splitlines()[-15:]:
    print("    %s" % ln[:160])
print("\n  %s" % ("✅ 依赖检查通过" if p2.returncode == 0
                  else "❌ 仍然失败，见上面输出"))
