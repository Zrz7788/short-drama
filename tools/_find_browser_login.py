# -*- coding: utf-8 -*-
r"""查浏览器里有没有存过 github.com 的账号（**只看域名与用户名，绝不读密码**）。

思路：Chromium 系浏览器的「已保存登录」存在 Login Data（sqlite）里，
字段是 origin_url / username_value，密码在 password_value（加密）。
**本脚本只查 origin 与 username，不碰 password_value。**
"""
import os
import shutil
import sqlite3
import tempfile

LOCAL = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")

BROWSERS = [
    ("Edge", os.path.join(LOCAL, "Microsoft", "Edge", "User Data")),
    ("夸克 Quark", os.path.join(LOCAL, "Quark", "User Data")),
    ("Chrome", os.path.join(LOCAL, "Google", "Chrome", "User Data")),
    ("Chrome(旧)", os.path.join(APPDATA, "Google", "Chrome", "User Data")),
]

print("=" * 74)
print("在各个浏览器里找 github.com 的已保存账号")
print("=" * 74)

for name, base in BROWSERS:
    if not os.path.isdir(base):
        print("\n── %s：未安装/无数据 (%s)" % (name, base))
        continue
    print("\n── %s" % name)
    profiles = [d for d in os.listdir(base)
                if d == "Default" or d.startswith("Profile ")]
    if not profiles:
        print("     没有 profile")
        continue
    for prof in profiles:
        src = os.path.join(base, prof, "Login Data")
        if not os.path.exists(src):
            continue
        # 浏览器在跑时会锁库，复制一份再读
        tmp = os.path.join(tempfile.gettempdir(),
                           "logindata-%s-%s.db" % (name[:6], prof.replace(" ", "")))
        try:
            shutil.copy2(src, tmp)
        except OSError as e:
            print("     %s：复制失败 %s" % (prof, str(e)[:40]))
            continue
        try:
            con = sqlite3.connect(tmp)
            rows = con.execute(
                "SELECT origin_url, username_value FROM logins "
                "WHERE origin_url LIKE '%github%'").fetchall()
            if rows:
                for url, user in rows:
                    print("     ✅ %-14s %-30s 用户: %s"
                          % (prof, url[:30], user or "(空)"))
            else:
                # 也看看所有记录的域名，确认库是可读的
                n = con.execute("SELECT COUNT(*) FROM logins").fetchone()[0]
                print("     %-14s 无 github 记录（该库共 %d 条登录）" % (prof, n))
            con.close()
        except sqlite3.Error as e:
            print("     %s：读取失败 %s" % (prof, str(e)[:50]))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

print("\n" + "=" * 74)
print("说明")
print("=" * 74)
print("  · 若上面列出用户名，说明你**存过**该账号的密码 —— 直接在登录页")
print("    让浏览器自动填充即可，不必走『Forgot password』。")
print("  · 本脚本**没有读取任何密码字段**。")
print("  · Quark 不一定是 Chromium 标准库结构，读不到属正常。")
