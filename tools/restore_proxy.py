# -*- coding: utf-8 -*-
r"""重启 DSH 后恢复联网取证能力。

## 为什么要这个

本机直连只能到国内（百度/Bing 通，维基/GitHub 不通）。
之前的做法是起一个**私有 Clash 内核**（用你自己的订阅，独立端口，
不碰你的 Clash Verge）。但那个内核跑在 DSH 的后台作业里，
**DSH 一重启就被连带杀掉**，于是 `web_fetch` 之外的路又断了。

这个脚本把「拉订阅 + 起内核 + 验证」一次做完，重启后跑一条命令即可。

## 用法（重启 DSH 后）

    python tools/clash_private.py --start     # 起内核
    python tools/clash_private.py --check     # 验证

⚠️ 内核**必须作为 DSH 的后台作业运行**（否则命令一结束就被回收）。
用 run_in_background 跑 `clash_private.py --serve`，或直接跑：

    <node> ... 不行——内核是原生 exe，直接：

    "C:\Program Files\Clash Verge\verge-mihomo.exe" -d .tools\clash-priv \
        -f .tools\clash-priv\config.yaml

## 已验证的事实（别再重复试）

- 本机网络正常：DNS 通、TCP 443 通、百度/Bing 直连 200
- `web_fetch` 这个工具本身坏（报 fetch failed），**不是没网**
- 浏览器（Chrome / Edge / agent-browser）在本会话**一律起不来**——
  Chrome 连 crashpad 都自杀（OpenProcess 拒绝访问 0x5），别再试
- 直连 **npm registry 可用**（不需要代理）；维基/GitHub 必须走代理
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = r"C:\Program Files\Clash Verge\verge-mihomo.exe"
PRIV = os.path.join(ROOT, ".tools", "clash-priv")
CFG = os.path.join(PRIV, "config.yaml")


def main():
    if not os.path.exists(CORE):
        print("找不到内核：%s" % CORE)
        return 1
    if not os.path.exists(CFG):
        print("没有配置：%s" % CFG)
        print("先跑：python tools/clash_private.py --start")
        return 1
    print("内核: %s" % CORE)
    print("配置: %s" % CFG)
    print()
    print("在 DSH 里用后台作业启动它（命令一结束它会被回收，所以必须后台）：")
    print('  & "%s" -d "%s" -f "%s"' % (CORE, PRIV, CFG))
    print()
    print("起来后验证：python tools/clash_private.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
