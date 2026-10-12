# -*- coding: utf-8 -*-
r"""深挖 github-copilot 相关目录，找已登录的 GitHub 账号用户名。

**只看用户名线索，不输出任何 token / 密码明文。**
"""
import json
import os
import re

LOCAL = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")
HOME = os.path.expanduser("~")

ROOTS = [
    os.path.join(LOCAL, "github-copilot-sdk"),
    os.path.join(LOCAL, "github-copilot-git-2.53.0-4"),
    os.path.join(LOCAL, "github-copilot"),
    os.path.join(APPDATA, "github-copilot"),
    os.path.join(HOME, ".copilot"),
    os.path.join(HOME, ".config", "github-copilot"),
    os.path.join(APPDATA, "Code"),
    os.path.join(APPDATA, "Code - Insiders"),
    os.path.join(LOCAL, "Programs", "Microsoft VS Code"),
]

USER_KEYS = re.compile(
    r'"(?:login|username|userName|githubLogin|accountName|displayName|'
    r'hostLogin|user)"\s*:\s*"([A-Za-z0-9\-_.@]{2,60})"')

print("=" * 74)
print("扫描目录")
print("=" * 74)
for r in ROOTS:
    print("  %-58s %s" % (r, "✅" if os.path.isdir(r) else "❌"))

print("\n" + "=" * 74)
print("逐个目录找「用户名样式」的内容")
print("=" * 74)
for r in ROOTS:
    if not os.path.isdir(r):
        continue
    print("\n── %s" % r)
    files = []
    for root, dirs, fs in os.walk(r):
        dirs[:] = [d for d in dirs
                   if d not in ("node_modules", "Cache", "GPUCache",
                                "Code Cache", "logs")]
        for f in fs:
            p = os.path.join(root, f)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            if s > 3 * 1024 * 1024:
                continue
            files.append((p, s))
    print("    %d 个候选文件" % len(files))

    found = {}
    for p, s in files:
        if not p.lower().endswith((".json", ".jsonc", ".yaml", ".yml",
                                   ".ini", ".cfg", ".conf", ".db", ".txt",
                                   ".log", "")):
            continue
        try:
            raw = open(p, "rb").read()
        except OSError:
            continue
        txt = raw.decode("utf-8", "replace")
        for m in USER_KEYS.finditer(txt):
            v = m.group(1)
            if v.lower() in ("true", "false", "null", "none", "user",
                             "username", "admin", "root"):
                continue
            found.setdefault(v, []).append(os.path.relpath(p, r))
    if found:
        for v, where in sorted(found.items(), key=lambda x: -len(x[1]))[:15]:
            print("      %-34s  <- %s" % (v, where[0][:58]))
    else:
        print("      没找到用户名样式的内容")

print("\n" + "=" * 74)
print("特别检查：Copilot CLI / SDK 的登录态文件")
print("=" * 74)
for cand in (os.path.join(LOCAL, "github-copilot-sdk", "auth.json"),
             os.path.join(HOME, ".copilot", "config.json"),
             os.path.join(HOME, ".copilot", "auth.json"),
             os.path.join(APPDATA, "github-copilot", "config.json")):
    if os.path.exists(cand):
        print("  ✅ %s (%d B)" % (cand, os.path.getsize(cand)))
        try:
            d = json.load(open(cand, encoding="utf-8"))
            def walk(n, pre=""):
                if isinstance(n, dict):
                    for k, v in n.items():
                        if any(t in k.lower() for t in
                               ("login", "user", "name", "email", "account")):
                            print("       %s%s = %s" % (pre, k, str(v)[:70]))
                        else:
                            walk(v, pre + k + ".")
            walk(d)
        except Exception as e:
            print("       (解析失败: %s)" % e)
    else:
        print("  ❌ %s" % cand)
