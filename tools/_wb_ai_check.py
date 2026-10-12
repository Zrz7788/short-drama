# -*- coding: utf-8 -*-
r"""核对 WorkBuddy 国际版（WorkBuddy AI）是否装好、登录态是否落盘。

插件区分两版靠：
  - exe 基名：workbuddy.exe（国内）vs workbuddyai.exe（国际）
  - 凭据文件名：workbuddy-desktop.info vs workbuddy-desktop-ai.info
两者共用 %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth 目录。
"""
import os
import subprocess

HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")
AUTH = os.path.join(LOCAL, "CodeBuddyExtension", "Data", "Public", "auth")

print("=" * 74)
print("1) 凭据文件（最关键：国际版登录成功的唯一判据）")
print("=" * 74)
print("  目录: %s" % AUTH)
for fn, label in (("workbuddy-desktop.info", "国内版"),
                  ("workbuddy-desktop-ai.info", "国际版")):
    p = os.path.join(AUTH, fn)
    if os.path.exists(p):
        print("  ✅ %-28s %8d B   (%s)" % (fn, os.path.getsize(p), label))
    else:
        print("  ❌ %-28s 不存在        (%s)" % (fn, label))

print("\n" + "=" * 74)
print("2) 国际版可执行文件 workbuddyai.exe 在哪")
print("=" * 74)
found = []
for base in (LOCAL, os.environ.get("PROGRAMFILES", ""),
             os.environ.get("PROGRAMFILES(X86)", ""), "G:\\", "D:\\", "C:\\"):
    if not base or not os.path.isdir(base):
        continue
    try:
        for name in os.listdir(base):
            full = os.path.join(base, name)
            if not os.path.isdir(full):
                continue
            hit = os.path.join(full, "workbuddyai.exe")
            if os.path.exists(hit):
                found.append(hit)
            # 再看一层
            try:
                for n2 in os.listdir(full):
                    h2 = os.path.join(full, n2, "workbuddyai.exe")
                    if os.path.exists(h2):
                        found.append(h2)
            except OSError:
                pass
    except OSError:
        pass
for f in found:
    print("  ✅ %s  (%.0f MB)" % (f, os.path.getsize(f) / 1048576.0))
if not found:
    print("  常见路径下没找到 workbuddyai.exe")
    print("  → 查注册表")

print("\n" + "=" * 74)
print("3) 注册表卸载项（两版都在这里能查到）")
print("=" * 74)
import winreg
for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                   (winreg.HKEY_CURRENT_USER,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                   (winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")):
    try:
        k = winreg.OpenKey(hive, path)
    except OSError:
        continue
    for i in range(winreg.QueryInfoKey(k)[0]):
        try:
            sub = winreg.EnumKey(k, i)
            sk = winreg.OpenKey(k, sub)
            name = winreg.QueryValueEx(sk, "DisplayName")[0]
            if "Buddy" not in name:
                continue
            icon = ""
            try:
                icon = winreg.QueryValueEx(sk, "DisplayIcon")[0]
            except OSError:
                pass
            print("  %-26s  icon: %s" % (name, icon))
        except OSError:
            continue

print("\n" + "=" * 74)
print("4) 国际版数据目录（应出现在 AppData，与国内版分开）")
print("=" * 74)
for p in (os.path.join(APPDATA, "WorkBuddy AI"),
          os.path.join(LOCAL, "WorkBuddy AI"),
          os.path.join(HOME, ".workbuddy-ai"),
          os.path.join(HOME, ".workbuddyai")):
    if os.path.isdir(p):
        n = sum(len(fs) for _, _, fs in os.walk(p))
        s = 0
        for root, _d, fs in os.walk(p):
            for f in fs:
                try:
                    s += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        print("  ✅ %-44s %d 文件 / %.1f MB" % (p, n, s / 1048576.0))
    else:
        print("  ❌ %s" % p)

print("\n" + "=" * 74)
print("5) 正在运行的 WorkBuddy 进程")
print("=" * 74)
p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
print("  WorkBuddy.exe(国内): %s" % ("运行中" if "WorkBuddy" in p.stdout else "未运行"))
p2 = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddyAI.exe"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace")
print("  WorkBuddyAI.exe(国际): %s" % ("运行中" if "WorkBuddy" in p2.stdout else "未运行"))
