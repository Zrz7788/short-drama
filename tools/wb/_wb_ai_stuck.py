# -*- coding: utf-8 -*-
r"""查 WorkBuddy AI 登录卡在 "Sign in..." 的原因。

界面提示 "Browser didn't open automatically?"，说明 App 起浏览器失败。
要弄清两件事：
  1. 默认浏览器指向谁（可能被别的东西占了）
  2. **回调地址是不是 localhost** —— 我刚开的系统代理如果把回调挡了，
     浏览器登完也回不来，会一直卡在 "Sign in..."
"""
import os
import subprocess
import winreg

print("=" * 74)
print("1) 默认浏览器（http / https 的 ProgId 与命令）")
print("=" * 74)
for proto in ("http", "https"):
    p = (r"SOFTWARE\Microsoft\Windows\Shell\Associations"
         r"\UrlAssociations\%s\UserChoice" % proto)
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, p)
        pid = winreg.QueryValueEx(k, "ProgId")[0]
        print("  %-6s ProgId = %s" % (proto, pid))
        # 找它的命令
        for hive, root in ((winreg.HKEY_CLASSES_ROOT, ""),):
            for cand in (r"%s\shell\open\command" % pid,):
                try:
                    kk = winreg.OpenKey(hive, cand)
                    print("         命令 = %s" % winreg.QueryValueEx(kk, "")[0][:110])
                except OSError:
                    pass
    except OSError as e:
        print("  %-6s 读不到: %s" % (proto, e))

print("\n" + "=" * 74)
print("2) 系统代理当前状态（确认我开的代理还在）")
print("=" * 74)
k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
    try:
        print("  %-16s %s" % (name, winreg.QueryValueEx(k, name)[0]))
    except OSError:
        print("  %-16s (无)" % name)

print("\n" + "=" * 74)
print("3) WorkBuddy AI 是否在监听本地回调端口")
print("=" * 74)
p = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
listen = [ln.strip() for ln in (p.stdout or "").splitlines()
          if "LISTENING" in ln and ("127.0.0.1" in ln or "0.0.0.0" in ln)]
# 找 WorkBuddyAI 的 pid
q = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddyAI.exe", "/FO", "CSV"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
pids = set()
for ln in (q.stdout or "").splitlines()[1:]:
    parts = [x.strip('"') for x in ln.split('","')]
    if len(parts) > 1 and parts[1].isdigit():
        pids.add(parts[1])
print("  WorkBuddyAI.exe 的 PID: %s" % (sorted(pids) or "未运行"))
hit = [ln for ln in listen if any(" %s" % pid in ln for pid in pids)]
if hit:
    print("  它监听的端口：")
    for ln in hit[:10]:
        print("    %s" % ln[:100])
else:
    print("  没看到它监听本地端口（或进程未运行）")

print("\n" + "=" * 74)
print("4) 结论要点")
print("=" * 74)
print("  - 如果回调是 http://127.0.0.1:xxxx，我的 ProxyOverride 里有 127.*，")
print("    理论上不会被代理挡掉。")
print("  - 如果自动开浏览器失败，界面上有『Copy Login Link』可手动粘贴到浏览器。")
print("  - 浏览器登录完成后，必须能回调到 App；卡在 Sign in... 多半是回调没回来。")
