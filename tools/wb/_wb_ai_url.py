# -*- coding: utf-8 -*-
r"""从国内版 WorkBuddy 程序里挖国际版（WorkBuddy AI）的域名/下载线索。

**为什么在本地挖而不是猜**：`web_search` 无 API key、`web_fetch` 坏，
我无法核实"官方网址"。**凭印象给网址可能把你导向仿冒站**，所以只在
程序自身字节里找证据 —— 那才是权威来源。
"""
import re
import os

ASAR = r"G:\WorkBuddy\resources\app.asar"
UNP = r"G:\WorkBuddy\resources\app.asar.unpacked"

# 国际版可能的域名样式
PATTERNS = [
    (rb"https?://[a-z0-9.\-]*workbuddy[a-z0-9.\-]*(?:/[A-Za-z0-9._\-/]*)?",
     "含 workbuddy 的 URL"),
    (rb"https?://[a-z0-9.\-]*codebuddy[a-z0-9.\-]*(?:/[A-Za-z0-9._\-/]*)?",
     "含 codebuddy 的 URL"),
    (rb"\bworkbuddy[a-z0-9\-]*\.(?:ai|com|cn|io|app|dev)\b", "域名（无协议）"),
    (rb"\b(?:ai\.workbuddy|workbuddy-ai|workbuddyai)\b", "国际版标识"),
]

print("扫描 %s（只读）" % ASAR)
print("=" * 74)

found = {label: {} for _p, label in PATTERNS}
size = 0
CHUNK = 8 << 20
with open(ASAR, "rb") as f:
    while True:
        buf = f.read(CHUNK)
        if not buf:
            break
        size += len(buf)
        for pat, label in PATTERNS:
            for m in re.finditer(pat, buf):
                s = m.group(0).decode("ascii", "ignore")
                found[label][s] = found[label].get(s, 0) + 1

print("扫描 %.0f MB\n" % (size / 1048576.0))
for _p, label in PATTERNS:
    d = found[label]
    print("=== %s（%d 种）===" % (label, len(d)))
    for s, n in sorted(d.items(), key=lambda x: -x[1])[:25]:
        if len(s) < 4:
            continue
        print("  %-62s ×%d" % (s[:62], n))
    print()

print("=" * 74)
print("解包目录里的线索文件")
print("=" * 74)
if os.path.isdir(UNP):
    for root, dirs, files in os.walk(UNP):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "vendor", "locales")]
        for f in files:
            if any(k in f.lower() for k in ("product", "app-update", "version", "readme")):
                fp = os.path.join(root, f)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                if sz > 400000:
                    continue
                print("  %s (%d B)" % (fp.replace(UNP, "…"), sz))
