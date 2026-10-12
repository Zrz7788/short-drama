# -*- coding: utf-8 -*-
r"""查 WorkBuddy AI（国际版）支持哪些登录方式。

微信登录是国内版特有的；国际版通常用邮箱 / Google / Apple / GitHub。
**从程序里挖，不猜。** 同时测这些登录域名的可达性（国内直连多半不通，
但走代理可能通 —— 这决定用户能不能真的登进去）。
"""
import os
import re

AI_DIR = r"G:\workBuddyguoji\WorkBuddyAI"
ASAR = os.path.join(AI_DIR, "resources", "app.asar")

print("=" * 74)
print("1) 国际版安装目录")
print("=" * 74)
print("  %s  -> %s" % (AI_DIR, "存在" if os.path.isdir(AI_DIR) else "不存在"))
if os.path.isdir(AI_DIR):
    for n in sorted(os.listdir(AI_DIR))[:14]:
        p = os.path.join(AI_DIR, n)
        print("    %s %s" % ("[D]" if os.path.isdir(p) else "   ", n))

print("\n" + "=" * 74)
print("2) 从 app.asar 里找登录方式关键词")
print("=" * 74)
if not os.path.exists(ASAR):
    print("  找不到 %s" % ASAR)
    raise SystemExit(1)
print("  %s (%.0f MB)" % (ASAR, os.path.getsize(ASAR) / 1048576.0))

KEYS = [b"wechat", b"WeChat", b"weixin", b"google", b"Google", b"apple",
        b"Apple", b"github", b"GitHub", b"email", b"password",
        b"sso", b"SSO", b"oauth", b"OAuth", b"phone", b"verification code",
        b"sign up", b"sign in", b"log in", b"register"]

counts = {k: 0 for k in KEYS}
URLPAT = re.compile(rb"https?://[a-zA-Z0-9.\-]+(?:/[A-Za-z0-9._\-/]*)?")
auth_urls = {}
CHUNK = 8 << 20
with open(ASAR, "rb") as f:
    while True:
        buf = f.read(CHUNK)
        if not buf:
            break
        for k in KEYS:
            counts[k] += buf.count(k)
        for m in URLPAT.finditer(buf):
            u = m.group(0).decode("ascii", "ignore")
            low = u.lower()
            if any(t in low for t in ("login", "signin", "sign-in", "auth",
                                      "oauth", "sso", "account", "passport",
                                      "google", "apple", "github")):
                auth_urls[u] = auth_urls.get(u, 0) + 1

print("\n  关键词出现次数：")
for k in KEYS:
    n = counts[k]
    if n:
        print("    %-18s ×%d" % (k.decode(), n))

print("\n  认证相关 URL（前 25）：")
for u, n in sorted(auth_urls.items(), key=lambda x: -x[1])[:25]:
    print("    %-72s ×%d" % (u[:72], n))

print("\n" + "=" * 74)
print("3) 登录域名的可达性（直连）")
print("=" * 74)
import socket
import urllib.request
hosts = ["accounts.google.com", "appleid.apple.com", "github.com",
         "www.workbuddy.ai", "api.workbuddy.ai", "codebuddy.ai"]
for h in hosts:
    try:
        ip = socket.gethostbyname(h)
    except OSError as e:
        print("  DNS %-24s 解析失败" % h)
        continue
    s = socket.socket()
    s.settimeout(5)
    try:
        s.connect((ip, 443))
        print("  TCP %-24s ✅ 通 (%s)" % (h, ip))
    except OSError as e:
        print("  TCP %-24s ❌ %s" % (h, str(e)[:40]))
    finally:
        s.close()
