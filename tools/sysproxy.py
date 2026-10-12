# -*- coding: utf-8 -*-
r"""打开系统代理（指向本机私有 Clash 内核 127.0.0.1:7897）。

## 为什么需要

WorkBuddy AI（国际版）是 Electron 程序，**默认跟随系统代理**。
它的三个注册入口（Google / GitHub / X）在国内直连都不通：

    accounts.google.com   直连超时
    github.com            能连但读取超时
    x.com / twitter.com   直连不通

走代理后三个都是 HTTP 200（已实测）。

## 安全设计

- **先备份**原来的 ProxyEnable / ProxyServer，写到同目录的 .bak 文件
- 只改当前用户的 Internet Settings（HKCU），不动机器级设置
- 用 InternetSetOption 广播变更，让已开的程序立即感知
- `--restore` 可以把备份原样还原

⚠️ 这会把**整机当前用户的流量**都走代理。用完想关就跑 `--restore`，
或者直接用 `--off` 关闭代理开关（保留地址）。
"""
import argparse
import json
import os
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
BAK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   ".tools", "sysproxy-backup.json")
PROXY = "127.0.0.1:7897"


def broadcast():
    """通知系统代理已变更（否则已运行的程序还以为没代理）。"""
    try:
        import ctypes
        from ctypes import wintypes
        wininet = ctypes.WinDLL("wininet.dll")
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        for opt in (INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH):
            wininet.InternetSetOptionW(None, opt, None, 0)
        return True
    except Exception as e:
        print("  ⚠️ 广播变更失败（不影响设置本身）: %s" % e)
        return False


def read():
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
    out = {}
    for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
        try:
            out[name] = winreg.QueryValueEx(k, name)[0]
        except OSError:
            out[name] = None
    return out


def write(enable, server=None, override=None):
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                       winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                      1 if enable else 0)
    if server is not None:
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
    if override is not None:
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, override)


def main():
    ap = argparse.ArgumentParser(description="开关系统代理（指向本机 Clash）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", action="store_true", help="打开代理并指向 %s" % PROXY)
    g.add_argument("--off", action="store_true", help="只关闭代理开关")
    g.add_argument("--restore", action="store_true", help="从备份还原")
    g.add_argument("--show", action="store_true", help="只看当前设置")
    args = ap.parse_args()

    print("=== 当前设置 ===")
    cur = read()
    for k, v in cur.items():
        print("  %-16s %s" % (k, v))

    if args.show:
        return 0

    if args.on:
        os.makedirs(os.path.dirname(BAK), exist_ok=True)
        if not os.path.exists(BAK):
            with open(BAK, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            print("\n已备份原始设置 -> %s" % BAK)
        else:
            print("\n备份已存在（不覆盖）-> %s" % BAK)
        # 绕过本地/内网，避免本机服务被代理
        write(True, PROXY, "localhost;127.*;10.*;172.16.*;192.168.*;<local>")
        broadcast()
        print("\n=== 修改后 ===")
        for k, v in read().items():
            print("  %-16s %s" % (k, v))
        print("\n✅ 系统代理已开启，指向 %s" % PROXY)
        print("   现在打开的程序可能需要重开才生效。")
        return 0

    if args.off:
        write(False)
        broadcast()
        print("\n✅ 已关闭代理开关（地址保留）")
        return 0

    if args.restore:
        if not os.path.exists(BAK):
            print("\n没有备份可还原：%s" % BAK)
            return 1
        with open(BAK, encoding="utf-8") as f:
            b = json.load(f)
        write(bool(b.get("ProxyEnable") or 0),
              b.get("ProxyServer"),
              b.get("ProxyOverride"))
        broadcast()
        print("\n=== 已还原 ===")
        for k, v in read().items():
            print("  %-16s %s" % (k, v))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
