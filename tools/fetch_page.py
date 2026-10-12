# -*- coding: utf-8 -*-
r"""取网页正文 —— 绕开不可用的 web_fetch。

## 为什么有这个

`web_fetch` 在本机报 `TypeError: fetch failed`，但实测**网络完全正常**：
DNS 通、TCP 443 通、`https://www.baidu.com` 与 `https://www.bing.com` 都返回 200。
所以断的是那个工具，不是网络。这个脚本直接用标准库取。

## 它做什么 / 不做什么

做：取 HTML → 去脚本样式 → 转纯文本 → 落盘（**可复核，不是只回一段摘要**）
不做：**不执行 JavaScript**。所以只能取服务端渲染的页面。
需要 JS 的站点（SPA、部分搜索结果页）取回来是空壳。

用法：
  python tools/fetch_page.py <url> [--out 文件] [--max 20000]
  python tools/fetch_page.py --search "殡仪馆 整容室"      # 抓 Bing 结果页
"""
import argparse
import html
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# 只保留可能有正文的标签；其余整块丢掉
_DROP = re.compile(r"<(script|style|noscript|svg|iframe|head)\b.*?</\1>",
                   re.S | re.I)
_BLOCK = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article|table)\b[^>]*>",
                    re.I)
_TAG = re.compile(r"<[^>]+>")


def to_text(raw):
    """HTML → 纯文本。粗暴但够用，且**不执行任何脚本**。"""
    s = _DROP.sub(" ", raw)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    lines = [ln.strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def find_chrome():
    """找一个可用的 Chromium 内核浏览器。"""
    cands = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        "/usr/bin/google-chrome", "/usr/bin/chromium",
    ]
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def render_with_chrome(url, timeout=60):
    """用无头 Chrome 渲染后取 DOM。

    为什么需要它：很多中文站点（搜索页、百科）是 JS 渲染 + 反爬，
    直接 urllib 取会拿到空壳或 403。无头浏览器执行 JS、带上真实指纹，
    所以能过。**这不是绕过登录或付费墙，只是用一个真浏览器正常访问。**

    返回 (status, ctype, html)；失败抛异常。
    """
    import subprocess
    import shutil
    import tempfile
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("没找到 Chrome/Edge，--render 不可用")
    # ⚠️ profile 目录要放在**工作区内**。放系统 temp 会被沙箱拒绝
    #    （实测：PermissionError WinError 5，Chrome 连 profile 都建不出来）。
    base = os.path.join(ROOT := os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), ".tools", "chrome-profile")
    os.makedirs(base, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="run-", dir=base)
    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-sync",
               "--user-data-dir=" + prof,
               "--virtual-time-budget=12000",     # 给 JS 留执行时间
               "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    if not out.strip():
        raise RuntimeError("无头渲染没拿到 DOM：%s" % (err.strip()[-200:] or "空输出"))
    return 200, "text/html", out


def get(url, timeout=25, render=False):
    if render:
        return render_with_chrome(url, timeout=max(timeout, 45))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
        enc = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        if m:
            enc = m.group(1)
        else:
            m2 = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
            if m2:
                enc = m2.group(1).decode("ascii", "ignore")
        try:
            body = raw.decode(enc, "replace")
        except LookupError:
            body = raw.decode("utf-8", "replace")
        return r.status, ctype, body


def search_url(q, engine="bing"):
    if engine == "bing":
        return "https://www.bing.com/search?q=" + urllib.parse.quote(q)
    if engine == "baidu":
        return "https://www.baidu.com/s?wd=" + urllib.parse.quote(q)
    raise ValueError(engine)


def extract_links(raw, base):
    """从搜索结果页里抽出标题+链接，便于逐条深挖。"""
    out = []
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                         raw, re.S | re.I):
        href, label = m.group(1), to_text(m.group(2)).strip()
        if not label or len(label) < 6:
            continue
        if any(x in href for x in ("bing.com", "microsoft.com", "msn.com",
                                   "go.microsoft", "baidu.com")):
            continue
        if (href, label) not in out:
            out.append((href, label))
    return out


def main():
    ap = argparse.ArgumentParser(description="取网页正文（不执行 JS）")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--search", default=None, help="改为抓搜索结果页")
    ap.add_argument("--engine", default="bing", choices=("bing", "baidu"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--max", type=int, default=20000)
    ap.add_argument("--links", action="store_true", help="只列结果页的链接")
    ap.add_argument("--render", action="store_true",
                    help="用无头 Chrome 渲染（能过 JS 页与反爬，慢一些）")
    args = ap.parse_args()

    url = args.url
    if args.search:
        url = search_url(args.search, args.engine)
    if not url:
        print("要么给 url，要么给 --search")
        return 2

    print("取：%s%s" % (url, "（无头渲染）" if args.render else ""))
    try:
        status, ctype, raw = get(url, render=args.render)
    except Exception as e:
        print("失败：%s: %s" % (type(e).__name__, str(e)[:160]))
        return 1
    print("HTTP %s  %s  %d bytes" % (status, ctype, len(raw)))

    if args.links:
        links = extract_links(raw, url)
        print("\n=== 结果页链接 %d 条 ===" % len(links))
        for h, t in links[:40]:
            print("  %s\n      %s" % (t[:78], h[:110]))
        return 0

    text = to_text(raw)
    print("正文 %d 字" % len(text))
    if len(text) < 120:
        print("\n⚠️ 正文极短——很可能是需要 JavaScript 的页面，本脚本取不到。")
    body = text[:args.max]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(body)
        print("已写：%s" % args.out)
    else:
        print("\n" + "-" * 70)
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
