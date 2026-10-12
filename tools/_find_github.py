# -*- coding: utf-8 -*-
r"""找这台机器上已有的 GitHub 账号线索（**只找用户名，不读密码/令牌明文**）。

依据：桌面/开始菜单里有 GitHub Copilot 与 CC Switch —— Copilot 必须登录
GitHub 才能用，所以很可能已经有账号。找到用户名，用户就能直接用
「Sign in」而不是走注册流程。
"""
import json
import os
import re
import subprocess

HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA", "")
LOCAL = os.environ.get("LOCALAPPDATA", "")

print("=" * 74)
print("1) git 全局配置里的 user.name / user.email")
print("=" * 74)
for p in (os.path.join(HOME, ".gitconfig"),
          os.path.join(APPDATA, "git", "config")):
    if os.path.exists(p):
        print("  %s" % p)
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
            for ln in t.splitlines():
                if re.search(r"(name|email)\s*=", ln, re.I):
                    print("     %s" % ln.strip()[:100])
        except OSError:
            pass
    else:
        print("  ❌ %s" % p)

print("\n" + "=" * 74)
print("2) Windows 凭据管理器里有没有 github（只看目标名，不看密钥）")
print("=" * 74)
p = subprocess.run(["cmdkey", "/list"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
lines = (p.stdout or "").splitlines()
hit = [ln.strip() for ln in lines if "github" in ln.lower()
       or "git:" in ln.lower()]
if hit:
    for ln in hit[:12]:
        print("  %s" % ln[:110])
else:
    print("  没有 github 相关条目")

print("\n" + "=" * 74)
print("3) Git Credential 存储文件（只列存在性与键名）")
print("=" * 74)
for p in (os.path.join(HOME, ".git-credentials"),
          os.path.join(LOCAL, "GitHubDesktop"),
          os.path.join(APPDATA, "GitHub Desktop"),
          os.path.join(LOCAL, "github-copilot"),
          os.path.join(APPDATA, "Code", "User", "globalStorage",
                       "github.copilot")):
    print("  %-58s %s" % (p, "✅ 在" if os.path.exists(p) else "❌"))

print("\n" + "=" * 74)
print("4) VS Code / Copilot 里的 github 账号线索（只抓用户名样式）")
print("=" * 74)
targets = [
    os.path.join(APPDATA, "Code", "User", "globalStorage",
                 "state.vscdb"),
    os.path.join(APPDATA, "Code", "User", "settings.json"),
    os.path.join(APPDATA, "Code", "User", "globalStorage",
                 "storage.json"),
]
USER_RE = re.compile(r'"?(?:login|username|githubLogin|accountName)"?\s*[:=]\s*"([A-Za-z0-9\-]{1,39})"')
for t in targets:
    if not os.path.exists(t):
        print("  ❌ %s" % t)
        continue
    print("  ✅ %s (%d B)" % (t, os.path.getsize(t)))
    try:
        if t.endswith(".vscdb"):
            # sqlite：直接抓字节里的用户名样式
            raw = open(t, "rb").read()
            txt = raw.decode("utf-8", "replace")
        else:
            txt = open(t, encoding="utf-8", errors="replace").read()
        found = set(USER_RE.findall(txt))
        if found:
            print("       可能用户名: %s" % ", ".join(sorted(found)[:10]))
        else:
            # 退一步：找 github.com/ 后面的用户名
            g = set(re.findall(r'github\.com/([A-Za-z0-9\-]{2,39})', txt))
            g = {x for x in g if x not in ("features", "login", "settings",
                                           "about", "pricing", "orgs")}
            if g:
                print("       路径里的用户名: %s" % ", ".join(sorted(g)[:10]))
            else:
                print("       没抓到用户名")
    except OSError as e:
        print("       读取失败: %s" % e)

print("\n" + "=" * 74)
print("5) GitHub Desktop / Copilot 是否装过")
print("=" * 74)
for name in ("GitHub Desktop", "GitHubDesktop", "copilot", "Copilot"):
    for base in (LOCAL, APPDATA, os.environ.get("PROGRAMFILES", "")):
        if not base or not os.path.isdir(base):
            continue
        for d in os.listdir(base):
            if name.lower() in d.lower():
                print("  ✅ %s" % os.path.join(base, d))
