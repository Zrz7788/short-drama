# -*- coding: utf-8 -*-
r"""从 dsh-workbuddy-connect 里挖出**国际版（WorkBuddy AI）**的确切探测信息。

已知插件同时支持两版，差异集中在 variants 描述符里：
  - exe 基名 / 默认安装路径段
  - 凭据文件名（desktopFilename）
  - 环境变量名（WORKBUDDY_AI_ELECTRON_BIN / WORKBUDDY_AI_AUTH_FILE）
把它们全部列出来，用户装完国际版后可直接核对。
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

# 把所有 js 拼起来找 variants 段
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
print("代码总量 %.0f KB\n" % (len(text) / 1024))

print("=" * 74)
print("1) 两个 variant 的描述符（含 exe 名、路径段、凭据文件名）")
print("=" * 74)
# 找 exeBasename 与 defaultPathSegments 附近
for m in re.finditer(r'exeBasename:\s*"([^"]+)"', text):
    a = max(0, m.start() - 900)
    b = min(len(text), m.end() + 400)
    seg = re.sub(r"\s+", " ", text[a:b])
    print("\n  --- exeBasename = %s ---" % m.group(1))
    # 抽关键字段
    for key in ("desktopFilename", "ownFilename", "probeFilename",
                "catalogFilename", "displayName", "label", "id",
                "baseUrl", "chatBase", "defaultPathSegments",
                "authFileEnv", "electronBinEnv", "envPrefix"):
        for mm in re.finditer(r"%s:\s*(\"[^\"]*\"|\[[^\]]*\]|[A-Za-z0-9_.]+)"
                              % key, seg):
            print("      %-22s %s" % (key, mm.group(1)[:110]))
            break

print("\n" + "=" * 74)
print("2) 国际版相关的环境变量名（用来兜底）")
print("=" * 74)
envs = set(re.findall(r"(WORKBUDDY[A-Z0-9_]{2,40})", text))
for e in sorted(envs):
    if any(t in e for t in ("AI", "AUTH", "ELECTRON", "BIN", "FILE", "DIR")):
        print("  %s" % e)

print("\n" + "=" * 74)
print("3) 凭据文件名全集（国内版 vs 国际版）")
print("=" * 74)
for m in sorted(set(re.findall(r'desktopFilename:\s*"([^"]+)"', text))):
    print("  %s" % m)

print("\n" + "=" * 74)
print("4) 默认安装路径段（Windows）")
print("=" * 74)
for m in re.finditer(r'defaultPathSegments:\s*\[([^\]]*)\]', text):
    print("  [%s]" % re.sub(r"\s+", " ", m.group(1)))

print("\n" + "=" * 74)
print("5) 从 README 找国际版的下载/说明")
print("=" * 74)
rd = os.path.join(PKG, "README.md")
if os.path.exists(rd):
    t = open(rd, encoding="utf-8", errors="replace").read()
    for kw in ("WorkBuddy AI", "国际版"):
        for m in list(re.finditer(re.escape(kw), t))[:4]:
            a = max(0, m.start() - 130)
            b = min(len(t), m.end() + 190)
            print("  [%s] %s" % (kw, re.sub(r"\s+", " ", t[a:b])[:300]))
