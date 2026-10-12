# -*- coding: utf-8 -*-
r"""按来源取资料，落盘到 .tools/research/ 供逐条引用。

策略：多来源并取，**能取到就留、取不到就记下来**，不假装。
所有抓取都走私有 Clash 代理（jsdelivr/维基这类直连不通）。
"""
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fetch_page import to_text  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tools", "research")
PROXY = "http://127.0.0.1:7897"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SOURCES = [
    ("en_embalming", "https://en.wikipedia.org/wiki/Embalming"),
    ("en_mortuary", "https://en.wikipedia.org/wiki/Mortuary"),
    ("en_funeral_home", "https://en.wikipedia.org/wiki/Funeral_home"),
    ("en_mortician", "https://en.wikipedia.org/wiki/Mortician"),
    ("en_restorative", "https://en.wikipedia.org/wiki/Restorative_art"),
    ("zh_yiti", "https://zh.wikipedia.org/wiki/%E9%81%97%E4%BD%93"),
    ("zh_binyizang", "https://zh.wikipedia.org/wiki/%E6%AE%A1%E8%91%AC"),
    ("zh_fangfu", "https://zh.wikipedia.org/wiki/%E9%81%97%E4%BD%93%E9%98%B2%E8%85%90"),
    ("zh_zangli", "https://zh.wikipedia.org/wiki/%E8%91%AC%E7%A4%BC"),
    ("zh_huozang", "https://zh.wikipedia.org/wiki/%E7%81%AB%E8%91%AC"),
    ("zh_rulian", "https://zh.wikipedia.org/wiki/%E5%85%A5%E6%AE%9B"),
    # 中文其他来源
    ("mca", "https://www.mca.gov.cn/"),
    # 补：设备与操作细节（百科条目偏历史，缺"台面长什么样"这一层）
    ("en_embalming2", "https://en.wikipedia.org/wiki/Embalming_room"),
    ("en_undertaker", "https://en.wikipedia.org/wiki/Undertaker"),
    ("en_crematorium", "https://en.wikipedia.org/wiki/Crematorium"),
    ("en_cremation", "https://en.wikipedia.org/wiki/Cremation"),
    ("en_death_cert", "https://en.wikipedia.org/wiki/Death_certificate"),
    ("en_autopsy", "https://en.wikipedia.org/wiki/Autopsy"),
    ("en_operating_table", "https://en.wikipedia.org/wiki/Operating_table"),
    ("en_tag", "https://en.wikipedia.org/wiki/Toe_tag"),
    ("zh_crematorium", "https://zh.wikipedia.org/wiki/%E7%81%AB%E8%91%AC%E5%A0%B4"),
    ("zh_binyiguan", "https://zh.wikipedia.org/wiki/%E6%AE%A1%E4%BB%AA%E9%A6%86"),
    ("zh_siwang_zhengming",
     "https://zh.wikipedia.org/wiki/%E6%AD%BB%E4%BA%A1%E8%AF%81%E6%98%8E"),
]


def get(url, timeout=30):
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    op.addheaders = [("User-Agent", UA),
                     ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")]
    with op.open(url, timeout=timeout) as r:
        raw = r.read()
        return r.status, raw.decode("utf-8", "replace")


def main():
    os.makedirs(OUT, exist_ok=True)
    ok, bad = [], []
    for name, url in SOURCES:
        path = os.path.join(OUT, name + ".txt")
        if os.path.exists(path) and os.path.getsize(path) > 2000:
            print("  跳过（已有）%s" % name)
            ok.append(name)
            continue
        try:
            st, html = get(url)
            text = to_text(html)
            if len(text) < 500:
                raise RuntimeError("正文只有 %d 字，可能是 JS 壳" % len(text))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print("  OK   %-16s HTTP %s  正文 %6d 字" % (name, st, len(text)))
            ok.append(name)
        except Exception as e:
            print("  ERR  %-16s %s: %s" % (name, type(e).__name__, str(e)[:60]))
            bad.append((name, url, "%s: %s" % (type(e).__name__, str(e)[:80])))
    print("\n取到 %d 个来源，失败 %d 个" % (len(ok), len(bad)))
    if bad:
        print("失败的（**不要假装有出处**）：")
        for n, u, e in bad:
            print("  %s  %s  (%s)" % (n, u[:70], e))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
