# -*- coding: utf-8 -*-
r"""核实 C 盘上 WorkBuddy / DSH 相关目录的实际占用。

背景：设置页显示「系统缓存目录 C:\Users\zrz\.workbuddy」已用 501.8 MB，
用户问对 C 盘有没有影响。**先量真实数字，再回答。**
"""
import os
import time

HOME = os.path.expanduser("~")

CANDIDATES = [
    (".workbuddy（设置页说的系统缓存目录）", os.path.join(HOME, ".workbuddy")),
    ("WorkBuddy（设置页说的工作空间路径）", os.path.join(HOME, "WorkBuddy")),
    (".dsh（DSH 的家目录）", os.path.join(HOME, ".dsh")),
    (".dsh/attachments（会话附件缓存）", os.path.join(HOME, ".dsh", "attachments")),
    (".dsh/logs", os.path.join(HOME, ".dsh", "logs")),
    (".dsh/cache", os.path.join(HOME, ".dsh", "cache")),
    (".dsh/sessions", os.path.join(HOME, ".dsh", "sessions")),
    ("AppData/Roaming/WorkBuddy", os.path.join(os.environ["APPDATA"], "WorkBuddy")),
    ("AppData/Local/CodeBuddyExtension（登录凭据）",
     os.path.join(os.environ["LOCALAPPDATA"], "CodeBuddyExtension")),
    ("AppData/Local/@genieworkbuddy-desktop-updater（415MB 安装包）",
     os.path.join(os.environ["LOCALAPPDATA"], "@genieworkbuddy-desktop-updater")),
    ("AppData/Roaming/@deepseek-ai/dsh-desktop",
     os.path.join(os.environ["APPDATA"], "@deepseek-ai", "dsh-desktop")),
    ("Documents/deepseek-harness（本项目）",
     os.path.join(HOME, "Documents", "deepseek-harness")),
]

print("%-52s %10s  %s" % ("目录", "占用", "文件数/最大文件"))
print("-" * 90)
total = 0
rows = []
for label, p in CANDIDATES:
    if not os.path.isdir(p):
        print("%-52s %10s" % (label, "不存在"))
        continue
    size = 0
    n = 0
    biggest = ("", 0)
    for root, dirs, files in os.walk(p):
        for f in files:
            fp = os.path.join(root, f)
            try:
                s = os.path.getsize(fp)
            except OSError:
                continue
            size += s
            n += 1
            if s > biggest[1]:
                biggest = (os.path.relpath(fp, p), s)
    total += size
    rows.append((size, label, n, biggest))
    print("%-52s %9.1f MB  %d 个文件" % (label, size / 1048576.0, n))
    if biggest[1] > 20 * 1048576:
        print("%-52s            └ 最大: %s (%.0f MB)"
              % ("", biggest[0][:44], biggest[1] / 1048576.0))

print("-" * 90)
print("%-52s %9.1f MB" % ("合计（上面这些）", total / 1048576.0))

print("\n=== C 盘剩余空间 ===")
import shutil
try:
    u = shutil.disk_usage("C:\\")
    print("  总 %.1f GB / 已用 %.1f GB / 剩余 %.1f GB"
          % (u.total / 2**30, u.used / 2**30, u.free / 2**30))
except OSError as e:
    print("  读取失败: %s" % e)

print("\n=== 可以安全清理的（候选）===")
safe = [
    ("安装包缓存（装完就没用了）",
     os.path.join(os.environ["LOCALAPPDATA"], "@genieworkbuddy-desktop-updater")),
    ("DSH 会话附件（图片缓存，会自我增长）",
     os.path.join(HOME, ".dsh", "attachments")),
    ("DSH 日志", os.path.join(HOME, ".dsh", "logs")),
    ("DSH 缓存", os.path.join(HOME, ".dsh", "cache")),
]
for label, p in safe:
    if os.path.isdir(p):
        s = 0
        for root, d, fs in os.walk(p):
            for f in fs:
                try:
                    s += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        print("  %-42s %8.1f MB   %s" % (label, s / 1048576.0, p))
