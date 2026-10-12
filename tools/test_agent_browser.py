# -*- coding: utf-8 -*-
r"""实测 agent-browser 能不能在这个环境跑起来。

**为什么要实测而不是读文档**：本会话里浏览器一律起不来
（Chrome / Edge 在 --headless=new 与旧 --headless 下都 0 输出，连 --version 都没有，
提权到 danger-full-access 也一样）。agent-browser 无论怎么实现，最终都要驱动一个
浏览器进程——所以它极可能撞同一堵墙。**能跑通才算数，读文档不算。**

它自带浏览器的话就不依赖系统 Chrome；所以这一步必须真跑。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".tools", "agent-browser-test")
NODE = r"C:\Users\zrz\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
# ⚠️ 这个 runtime 只打包了 pnpm，**没有 npm**（node/lib/node_modules 不存在）。
PNPM = (r"C:\Users\zrz\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies"
        r"\pnpm\bin\pnpm.cjs")
PROXY = "http://127.0.0.1:7897"


def run(args, **kw):
    env = dict(os.environ)
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = PROXY
    env["npm_config_proxy"] = PROXY
    env["npm_config_https_proxy"] = PROXY
    env["npm_config_registry"] = "https://registry.npmjs.org/"
    env["npm_config_cache"] = os.path.join(WORK, "npm-cache")
    env["npm_config_store_dir"] = os.path.join(WORK, "pnpm-store")
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(args, cwd=WORK, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env,
                       timeout=kw.pop("timeout", 600))
    return p


def show(title, p, tail=30):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)
    print("退出码: %s" % p.returncode)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if out:
        print("--- stdout ---")
        for ln in out.splitlines()[-tail:]:
            print("  " + ln[:170])
    if err:
        print("--- stderr ---")
        for ln in err.splitlines()[-tail:]:
            print("  " + ln[:170])


def main():
    os.makedirs(WORK, exist_ok=True)
    print("工作目录: %s" % WORK)
    if not os.path.exists(PNPM):
        print("找不到 pnpm：%s" % PNPM)
        return 1

    # 1. 装
    print("\n>>> 1) pnpm add agent-browser（走代理）")
    p = run([NODE, PNPM, "add", "agent-browser@0.39.0",
             "--config.confirmModulesPurge=false"], timeout=900)
    show("install", p, tail=18)
    if p.returncode != 0:
        return 1

    binjs = os.path.join(WORK, "node_modules", "agent-browser", "bin",
                         "agent-browser.js")
    if not os.path.exists(binjs):
        print("装完了但找不到 bin：%s" % binjs)
        return 1
    print("\nbin 存在: %s" % binjs)

    # 2. 版本（最基础的"能不能启动"）
    print("\n>>> 2) agent-browser --version")
    p = run([NODE, binjs, "--version"], timeout=120)
    show("--version", p, tail=15)

    # 3. 帮助（看它有哪些命令、怎么驱动浏览器）
    print("\n>>> 3) agent-browser --help")
    p = run([NODE, binjs, "--help"], timeout=120)
    show("--help", p, tail=45)

    # 4. 真取一个页面（这一步决定成败）
    print("\n>>> 4) 真取一个页面（决定成败的一步）")
    p = run([NODE, binjs, "open", "https://example.com"], timeout=180)
    show("open example.com", p, tail=25)

    return 0


if __name__ == "__main__":
    sys.exit(main())
