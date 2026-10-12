# -*- coding: utf-8 -*-
r"""从 WorkBuddy AI 的运行数据里捞出**登录链接**。

界面卡在 "Sign ing in..." + "Browser didn't open automatically?"，
说明它要开浏览器但没开成。登录链接（带 state 参数）会先写进它的
日志或本地存储，再交给浏览器。**直接从那里读出来，用户手动粘贴即可。**

登录链接形如：
  https://workbuddy.ai/login/?platform=workbuddy-ai&state=<uuid>&version=5.7.6
"""
import os
import re
import time

HOME = os.path.expanduser("~")
DIRS = [
    os.path.join(HOME, ".workbuddy-ai"),
    os.path.join(os.environ.get("APPDATA", ""), "WorkBuddy AI"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "WorkBuddy AI"),
]

LOGIN_RE = re.compile(
    rb"https://[A-Za-z0-9.\-]*workbuddy\.(?:ai|cn)/login/\?[^\s\"'<>\\]{10,400}")

now = time.time()
print("=" * 74)
print("在 WorkBuddy AI 的数据目录里找登录链接")
print("=" * 74)

found = {}
for base in DIRS:
    if not os.path.isdir(base):
        print("\n  ❌ %s" % base)
        continue
    print("\n  ── %s" % base)
    files = []
    for root, dirs, fs in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("binaries", "node_modules")]
        for f in fs:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            # 只要最近 2 小时有改动的，以及常见日志/存储文件
            if now - st.st_mtime < 7200 or \
               f.lower().endswith((".log", ".json", ".ldb", ".log.old")):
                if st.st_size < 20 * 1024 * 1024:
                    files.append((p, st.st_size, st.st_mtime))
    files.sort(key=lambda x: -x[2])
    print("     候选文件 %d 个（按修改时间倒序，最多看 60）" % len(files))
    for p, sz, mt in files[:60]:
        try:
            raw = open(p, "rb").read()
        except OSError:
            continue
        for m in LOGIN_RE.finditer(raw):
            u = m.group(0).decode("ascii", "ignore")
            found[u] = (p, time.strftime("%H:%M:%S", time.localtime(mt)))

print("\n" + "=" * 74)
print("找到的登录链接")
print("=" * 74)
if not found:
    print("  ❌ 没找到。可能：")
    print("     · 链接还没生成（要点了按钮才有）")
    print("     · 或者存在别处（内存里，未落盘）")
    print("  → 那就用界面上的『Copy Login Link』按钮。")
else:
    for u, (p, t) in sorted(found.items(), key=lambda x: x[1][1], reverse=True)[:6]:
        print("\n  ⏱ %s   来源: %s" % (t, os.path.basename(p)))
        print("  %s" % u)
    # 挑最新的一条单独高亮
    best = sorted(found.items(), key=lambda x: x[1][1], reverse=True)[0][0]
    print("\n" + "=" * 74)
    print("最新一条（复制这一行到 Edge 地址栏）：")
    print("=" * 74)
    print(best)
