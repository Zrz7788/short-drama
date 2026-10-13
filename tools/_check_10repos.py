# -*- coding: utf-8 -*-
r"""核对用户列出的 10 个仓库/插件，逐个弄清「是什么、在不在 DSH 市场」。

清单：VoiceStudio / ppt-master / gods-eye-view / REA / diagram-design /
      paperclip / skills / ECC / andrej-karpathy-skills / claude-mem

**REA 已查过**（`morluto/rea`，14.3 万 star，MCP 逆向工具，DSH 接不了），
本脚本仍会带出来，但标注已知结论。

做法：先在 DSH 市场（4508 条）里按名字找，找不到的再去 GitHub 搜。
"""
import json
import os
import re
import urllib.parse
import urllib.request

PX = "http://127.0.0.1:7897"
op = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PX, "https": PX}))
op.addheaders = [("User-Agent", "Mozilla/5.0")]

NAMES = ["VoiceStudio", "ppt-master", "gods-eye-view", "REA",
         "diagram-design", "paperclip", "skills", "ECC",
         "andrej-karpathy-skills", "claude-mem"]

print("=" * 74)
print("① DSH 市场里找（4508 条）")
print("=" * 74)
try:
    d = json.loads(op.open("https://awesome-dsh-plugin.com/plugins.json",
                           timeout=50).read().decode("utf-8", "replace"))
    items = d if isinstance(d, list) else (d.get("plugins")
                                          or d.get("items") or [])
except Exception as e:
    print("  市场查询失败：%s" % str(e)[:60])
    items = []
print("  市场条目 %d 个\n" % len(items))

for want in NAMES:
    key = want.lower().replace("-", "").replace("_", "")
    hits = []
    for it in items:
        n = (it.get("name") or "").lower().replace("-", "").replace("_", "")
        s = json.dumps(it, ensure_ascii=False).lower()
        if key in n:
            hits.append((2, it))
        elif key in s:
            hits.append((1, it))
    hits.sort(key=lambda x: -x[0])
    print("--- %s ---" % want)
    if not hits:
        print("    ❌ 市场里没有")
    else:
        for score, it in hits[:4]:
            de = it.get("description") or {}
            zh = de.get("zh") if isinstance(de, dict) else str(de)
            print("    %s %-30s %-14s %s"
                  % ("★" if score == 2 else "·", it.get("name"),
                     it.get("owner"), it.get("category")))
            print("        %s" % (zh or "")[:116])
    print()

print("=" * 74)
print("② 市场里没找到的，去 GitHub 搜")
print("=" * 74)
for want in NAMES:
    key = want.lower().replace("-", "").replace("_", "")
    in_market = any(key in (it.get("name") or "").lower()
                    .replace("-", "").replace("_", "") for it in items)
    if in_market:
        continue
    u = ("https://api.github.com/search/repositories?q="
         + urllib.parse.quote(want) + "&per_page=4")
    print("--- %s ---" % want)
    try:
        r = json.loads(op.open(u, timeout=40).read())
        print("    GitHub 命中 %d 个仓库" % r.get("total_count", 0))
        for it in r.get("items", [])[:4]:
            print("      %-42s ⭐%-7s %s"
                  % (it["full_name"][:42], it["stargazers_count"],
                     (it.get("description") or "")[:62]))
    except Exception as e:
        print("      查询失败：%s" % str(e)[:50])
    print()
