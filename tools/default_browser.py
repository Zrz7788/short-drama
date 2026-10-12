# -*- coding: utf-8 -*-
r"""把默认浏览器临时切到 Edge（WorkBuddy AI 的 OAuth 自动打开失败时用）。

## 背景

WorkBuddy AI 登录卡在 "Sign in..."，界面提示
"Browser didn't open automatically?"。诊断结果：
  - 默认浏览器 ProgId = QuarkHTM，命令 `quark.exe --single-argument %1`
  - **夸克当时没有运行**
  - WorkBuddyAI 在等 127.0.0.1:18488 的回调（服务是活的）

所以自动开浏览器失败了，OAuth 流程起不来。切到 Edge 通常能解决。

## 安全设计

- **先备份** http / https 两个 ProgId，写到 .tools/browser-backup.json
- 只改 HKCU（当前用户），不动机器级
- `--restore` 原样还原
- 可选：用默认程序 UI 自己设（`--ui` 会打开设置页，最保险）

⚠️ Windows 10/11 对默认程序有**用户选择保护**：虽然有 UserChoice 键，
但系统可能不认非 UI 途径的修改（会校验 Hash）。
所以本脚本同时提供**回退路径**：直接改不了就让用户走 UI，或用
「复制登录链接 + 手动粘到浏览器」。
"""
import argparse
import json
import os
import subprocess
import sys
import winreg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAK = os.path.join(ROOT, ".tools", "browser-backup.json")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROGID = "MSEdgeHTM"


def userchoice_key(proto):
    return (r"SOFTWARE\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\%s\UserChoice" % proto)


def read_choice():
    out = {}
    for proto in ("http", "https"):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, userchoice_key(proto))
            out[proto] = winreg.QueryValueEx(k, "ProgId")[0]
        except OSError:
            out[proto] = None
    return out


def set_choice(progid):
    """尝试写 UserChoice。可能被系统的 Hash 校验挡住 —— 会如实报告。"""
    ok = []
    for proto in ("http", "https"):
        try:
            k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                   userchoice_key(proto), 0,
                                   winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, "ProgId", 0, winreg.REG_SZ, progid)
            ok.append(proto)
        except OSError as e:
            print("  ⚠️ 写 %s 失败: %s" % (proto, e))
    return ok


def main():
    ap = argparse.ArgumentParser(description="临时切换默认浏览器")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--to-edge", action="store_true", help="切到 Edge")
    g.add_argument("--restore", action="store_true", help="从备份还原")
    g.add_argument("--ui", action="store_true", help="打开系统默认应用设置页")
    args = ap.parse_args()

    if args.ui:
        print("打开「默认应用」设置页。请在浏览器列表里把 Microsoft Edge 设为默认。")
        subprocess.run(["cmd", "/c", "start", "",
                        "ms-settings:defaultapps"], check=False)
        return 0

    print("=== 当前默认浏览器 ===")
    cur = read_choice()
    for k, v in cur.items():
        print("  %-6s %s" % (k, v))

    if args.show:
        return 0

    if args.to_edge:
        if not os.path.exists(EDGE):
            print("\n找不到 Edge：%s" % EDGE)
            return 1
        os.makedirs(os.path.dirname(BAK), exist_ok=True)
        if not os.path.exists(BAK):
            with open(BAK, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            print("\n已备份 -> %s" % BAK)
        else:
            print("\n备份已存在 -> %s" % BAK)
        ok = set_choice(EDGE_PROGID)
        print("\n=== 修改后 ===")
        now = read_choice()
        for k, v in now.items():
            print("  %-6s %s" % (k, v))
        good = all(v == EDGE_PROGID for v in now.values())
        print()
        if good:
            print("✅ 已切到 Edge。")
            print("   ⚠️ 但 Windows 可能不认非 UI 途径的默认程序修改。")
            print("   如果 WorkBuddy AI 还是打不开浏览器，就用界面上的")
            print("   「Copy Login Link」+ 手动粘贴到 Edge。")
        print("\n   若无效，跑这句走系统 UI：python tools\\default_browser.py --ui")
        return 0

    if args.restore:
        if not os.path.exists(BAK):
            print("\n没有备份：%s" % BAK)
            return 1
        with open(BAK, encoding="utf-8") as f:
            b = json.load(f)
        for proto, progid in b.items():
            if progid:
                set_choice(progid)
        print("\n=== 已还原 ===")
        for k, v in read_choice().items():
            print("  %-6s %s" % (k, v))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
