# -*- coding: utf-8 -*-
r"""起一个**私有**的 Clash 内核，只为让 agent 能联网取证。

## 为什么不直接启动 Clash Verge 的 GUI

  1. GUI 需要桌面会话。本环境**连无头浏览器都起不来**（实测 Chrome/Edge 都报
     "无法在当前会话中打开"），GUI 大概率同样起不来
  2. 更重要：**不该动你的生产配置**。用你的 profile 起内核会写你的
     cache.db、占用你的端口。所以我拉一份订阅到工作区，用
     **独立目录 + 独立端口**起私有实例，你的 Clash Verge 一个字节都不动。

## 已实测的结论（别再重新试一遍）

  - 本机网络**正常**：DNS 通、TCP 443 通、百度/Bing 直连都 200
  - `web_fetch` 坏 → 用 `tools/fetch_page.py` 代替
  - 浏览器**完全起不来**（提权到 danger-full-access 也一样）→ 别在浏览器上耗
  - **Clash 内核能起来**：`C:\Program Files\Clash Verge\verge-mihomo.exe`
    直连 GitHub 超时，**走代理 200**

## 用法

  python tools/clash_private.py --start     # 拉订阅 + 写配置 + 起内核
  python tools/clash_private.py --check     # 测代理通不通
  python tools/clash_private.py --stop      # 停掉私有内核

私有实例：HTTP/SOCKS 混合端口 **7897**，external-controller 127.0.0.1:19097
（都避开 Clash Verge 的 7890/7891/7892，两者可同时存在互不干扰）
"""
import argparse
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIV = os.path.join(ROOT, ".tools", "clash-priv")
SUB = os.path.join(PRIV, "subscription.yaml")
CONFIG = os.path.join(PRIV, "config.yaml")
PIDFILE = os.path.join(PRIV, "core.pid")
CORE = r"C:\Program Files\Clash Verge\verge-mihomo.exe"

SUB_URL = "https://api.aishia.cc/tool/clash/7788/688107"
MIXED_PORT = 7897
CTRL_PORT = 19097
UA = "ClashforWindows/0.19.23"     # 机场按 UA 返回 Clash 格式


def _port_open(port, host="127.0.0.1"):
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def fetch_subscription():
    os.makedirs(PRIV, exist_ok=True)
    print("拉订阅：%s" % SUB_URL)
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        info = r.headers.get("subscription-userinfo") or ""
    text = raw.decode("utf-8", "replace")
    with open(SUB, "w", encoding="utf-8") as f:
        f.write(text)
    # ⚠️ 节点是**行内 map** 格式 `proxies: [{name: x, ...}]`，所以按 `- name:`
    #    去数会数到 0 或只数到组。这里按 `type:` 数更准。
    n_node = len(re.findall(r"type:\s*(?:vless|vmess|trojan|hysteria|hysteria2|ss|ssr|tuic|wireguard)\b", text))
    print("  已落盘 %d 字符，解析到节点 %d 个" % (len(text), n_node))
    if info:
        print("  流量：%s" % info)
    return n_node


def build_config():
    """把订阅改成**独立端口**的运行配置。

    ⚠️ 两个坑，都实测踩过：

    1. **不能"在末尾追加同名键来覆盖"**——mihomo 用严格 YAML 解析，遇到
       `mapping key "mixed-port" already defined` 会直接 **fatal 退出**。
       第一版就是这么写的，而且我把 stderr 丢掉了，内核 fatal 了我还在报告
       "已启动"，白等半天。
    2. **PyYAML 在这个环境里没有**，所以不能靠解析器。改成**行级精确编辑**：
       只替换顶层的 `key: ...` 行，`port`/`socks-port` 直接删掉（它们会和
       Clash Verge 的 7890/7891 撞）。
    """
    if not os.path.exists(SUB):
        raise SystemExit("没有订阅文件，先 --start")
    with open(SUB, encoding="utf-8") as f:
        lines = f.read().splitlines()

    want = {"mixed-port": str(MIXED_PORT),
            "external-controller": "127.0.0.1:%d" % CTRL_PORT,
            "allow-lan": "false",
            "log-level": "warning"}
    drop = ("port", "socks-port")          # 会和 Clash Verge 撞，删掉
    out, seen = [], set()
    for ln in lines:
        m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", ln)
        if m:
            k = m.group(1)
            if k in want:
                out.append("%s: %s" % (k, want[k]))
                seen.add(k)
                continue
            if k in drop:
                continue                    # 整行丢掉
        out.append(ln)
    for k, v in want.items():
        if k not in seen:
            out.append("%s: %s" % (k, v))

    # ⚠️ 第三个坑：订阅带的 rule-providers 全部指向 `cdn.jsdelivr.net`，
    #    而 jsdelivr 在这个网络下**拉不下来**（内核日志里一排 `error ... EOF`）。
    #    规则集全空 → 所有流量落到一条没有可用规则的 MATCH 上 → 连接被中止
    #    （WinError 10053）。私有实例不需要那套分流，干脆不要规则集：
    #    国内直连、其余走代理，两条就够。
    out = _drop_block(out, "rule-providers")
    out = _drop_block(out, "rules")
    out.append("rules:")
    out.append("  - GEOIP,CN,DIRECT")
    out.append("  - MATCH,🌐 节点选择" if _has_group(lines, "🌐 节点选择")
               else "  - MATCH,DIRECT")

    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write("# 由 tools/clash_private.py 生成——私有实例，独立端口，勿手改\n")
        f.write("\n".join(out) + "\n")
    # 自检：同名顶层键只能出现一次，否则 mihomo 必 fatal
    seen_keys = [re.match(r"^([A-Za-z][\w-]*)\s*:", x).group(1)
                 for x in out if re.match(r"^([A-Za-z][\w-]*)\s*:", x)]
    dup = {k for k in seen_keys if seen_keys.count(k) > 1}
    if dup:
        raise SystemExit("生成配置有重复顶层键，mihomo 会 fatal：%s" % sorted(dup))
    return CONFIG


def _has_group(lines, name):
    return any(("name: %s" % name) in ln or ("name: \"%s\"" % name) in ln
               for ln in lines)


def _drop_block(lines, key):
    """删掉一个顶层块（`key:` 及其所有缩进行）。"""
    out, dropping = [], False
    for ln in lines:
        if re.match(r"^%s\s*:" % re.escape(key), ln):
            dropping = True
            continue
        if dropping:
            if ln[:1].isspace() or not ln.strip():
                continue
            dropping = False
        out.append(ln)
    return out


def start():
    n = fetch_subscription()
    if n == 0:
        print("⚠️ 没解析到节点，仍会尝试启动")
    cfg = build_config()
    print("配置：%s" % cfg)
    if not os.path.exists(CORE):
        print("找不到内核：%s" % CORE)
        return 1
    # 已在跑就先停
    stop(quiet=True)
    # 内核是**控制台程序**（不是 GUI），所以能在这个会话里起来——已实测
    #
    # ⚠️ 必须显式脱离父进程的作业对象。否则命令一结束，整棵进程树被回收，
    #    内核跟着死：上一次就是"启动成功、下一个命令就 10061 拒绝连接"。
    #    DETACHED_PROCESS 让新进程不继承控制台，CREATE_NEW_PROCESS_GROUP
    #    让它不受父进程收到的 Ctrl 事件影响。
    log = open(os.path.join(PRIV, "core.log"), "w", encoding="utf-8")
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP",
                 "CREATE_NO_WINDOW", "CREATE_BREAKAWAY_FROM_JOB"):
        flags |= getattr(subprocess, name, 0)
    try:
        p = subprocess.Popen([CORE, "-d", PRIV, "-f", cfg],
                             stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL,
                             creationflags=flags)
    except OSError:
        # 某些环境不允许 BREAKAWAY，退回不带它的组合
        flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        p = subprocess.Popen([CORE, "-d", PRIV, "-f", cfg],
                             stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL,
                             creationflags=flags)
    with open(PIDFILE, "w") as f:
        f.write(str(p.pid))
    # 起来之后**一定要确认它真的在听**——上次把 stderr 丢了，内核 fatal 退出
    # 我却在报告"已启动"，白等半天。
    import time
    for _ in range(20):
        time.sleep(0.5)
        if p.poll() is not None:
            with open(os.path.join(PRIV, "core.log"), encoding="utf-8") as f:
                tail = f.read().strip().splitlines()[-6:]
            print("❌ 内核启动失败，退出码 %s。日志尾部：" % p.returncode)
            for ln in tail:
                print("   %s" % ln[:160])
            return 1
        if _port_open(MIXED_PORT):
            print("已启动内核 PID %d，混合端口 %d 正在监听" % (p.pid, MIXED_PORT))
            print("日志：%s" % os.path.join(PRIV, "core.log"))
            print("你的 Clash Verge 未被动过（端口不冲突，可同时开）")
            return 0
    print("⚠️ 内核进程在，但 %d 端口没监听。看日志：%s"
          % (MIXED_PORT, os.path.join(PRIV, "core.log")))
    return 1


def stop(quiet=False):
    if not os.path.exists(PIDFILE):
        if not quiet:
            print("没有记录在案的私有内核")
        return 0
    with open(PIDFILE) as f:
        pid = int(f.read().strip() or 0)
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True)
        if not quiet:
            print("已停止私有内核 PID %d" % pid)
    except Exception as e:
        if not quiet:
            print("停止失败：%s" % e)
    os.remove(PIDFILE)
    return 0


def check():
    px = "http://127.0.0.1:%d" % MIXED_PORT
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": px, "https": px}))
    tests = [
        ("gstatic 204", "https://www.gstatic.com/generate_204"),
        ("GitHub（直连超时，代理应通）", "https://github.com"),
        ("维基百科", "https://zh.wikipedia.org/wiki/%E9%81%97%E4%BD%93%E9%98%B2%E8%85%90"),
        ("百度（国内，应走直连）", "https://www.baidu.com"),
    ]
    ok = 0
    for name, u in tests:
        try:
            r = op.open(u, timeout=25)
            r.read(200)
            print("  OK   HTTP %s  %s" % (r.status, name))
            ok += 1
        except Exception as e:
            print("  ERR  %s: %s  %s" % (type(e).__name__, str(e)[:44], name))
    print("\n%d / %d 通过" % (ok, len(tests)))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="私有 Clash 内核（给 agent 取证用）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--build", action="store_true", help="只生成配置，不启动")
    args = ap.parse_args()
    if args.start:
        return start()
    if args.stop:
        return stop()
    if args.check:
        return check()
    print("配置已生成：%s" % build_config())
    return 0


if __name__ == "__main__":
    sys.exit(main())

