# -*- coding: utf-8 -*-
r"""看一个 DSH 插件包到底是什么 —— 拉 tarball、解包、报告关键信息。

**为什么不用 dsh plugin add 直接装**：先看清它声明了什么（是不是 dsh.bundle、
有没有 postinstall、会写哪些文件、要不要读某个 App 的本地凭据），再决定装不装。
HANDOFF 里记过一次教训：open-design-mcp 装了才发现**不是 DSH 插件**。

用法：python tools/inspect_plugin.py dsh-workbuddy-connect
"""
import io
import json
import os
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tools", "plugin-inspect")
PROXY = "http://127.0.0.1:7897"


def op():
    o = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    o.addheaders = [("User-Agent", "Mozilla/5.0"), ("Accept", "application/json")]
    return o


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "dsh-workbuddy-connect"
    os.makedirs(OUT, exist_ok=True)
    o = op()
    print("=== registry 元数据：%s ===" % name)
    meta = json.loads(o.open("https://registry.npmjs.org/" + name, timeout=25).read())
    latest = meta["dist-tags"]["latest"]
    v = meta["versions"][latest]
    print("  最新版本   : %s" % latest)
    print("  发布时间   : %s" % str(meta.get("time", {}).get(latest))[:19])
    print("  许可证     : %s" % v.get("license"))
    print("  作者       : %s" % json.dumps(v.get("author"), ensure_ascii=False)[:80])
    print("  仓库       : %s" % json.dumps(v.get("repository"), ensure_ascii=False)[:110])
    print("  依赖       : %s" % json.dumps(v.get("dependencies"), ensure_ascii=False)[:200])
    print("  scripts    : %s" % json.dumps(v.get("scripts"), ensure_ascii=False)[:200])
    print("  dsh 声明   : %s" % json.dumps(v.get("dsh"), ensure_ascii=False)[:300])
    # 25 个版本，看一下版本节奏
    vs = sorted(meta["versions"].keys(),
                key=lambda k: meta["time"].get(k, ""))
    print("  版本历史   : %s" % " ".join(vs[-10:]))

    url = v["dist"]["tarball"]
    print("\n=== 下载 tarball ===")
    print("  %s" % url)
    blob = o.open(url, timeout=60).read()
    print("  %d bytes" % len(blob))

    tgz = os.path.join(OUT, "%s-%s.tgz" % (name, latest))
    with open(tgz, "wb") as f:
        f.write(blob)
    ex = os.path.join(OUT, "%s-%s" % (name, latest))
    os.makedirs(ex, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob)) as t:
        members = t.getnames()
        t.extractall(ex)
    print("\n=== 包内文件（%d 个）===" % len(members))
    for m in sorted(members):
        p = os.path.join(ex, m)
        sz = os.path.getsize(p) if os.path.isfile(p) else 0
        print("  %-58s %8d B" % (m, sz))

    # 关键文件
    pkg = os.path.join(ex, "package", "package.json")
    if os.path.exists(pkg):
        print("\n=== package.json ===")
        with open(pkg, encoding="utf-8") as f:
            print(json.dumps(json.load(f), ensure_ascii=False, indent=2)[:2600])

    # README
    for cand in ("README.md", "readme.md", "README.MD"):
        p = os.path.join(ex, "package", cand)
        if os.path.exists(p):
            print("\n=== README（前 3000 字）===")
            with open(p, encoding="utf-8", errors="replace") as f:
                print(f.read()[:3000])
            break
    print("\n解包目录：%s" % os.path.join(ex, "package"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
