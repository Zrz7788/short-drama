# -*- coding: utf-8 -*-
r"""核对截图里「必备 5 大插件」与本机已装情况的对应关系。

截图里的 5 个：
  1 DSH Web UI  —— Web UI 全家桶、任务看板、Git Graph、远程访问、皮肤
  2 ModLens     —— 图片理解、OCR、截图分析
  3 DSH Better Sidebar —— 侧边栏、文件管理、终端、Git、子 Agent
  4 dsh-TUI     —— 类 Claude Code 的终端 TUI
  5 Aegis       —— 工程规范、调试验证、任务检查、代码可靠性

本机已装 13 个包（其中 4 个是 workbuddy 的 peer 依赖）。

**注意**：截图是第三方自媒体的个人推荐（"作者声明：个人观点，仅供参考"），
不是官方清单。所以本脚本做的是**对应关系核对**，不是"该不该装"的判断。
"""
import json
import os
import re
import sqlite3
import urllib.request

PX = "http://127.0.0.1:7897"
try:
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PX, "https": PX}))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    d = json.loads(op.open("https://awesome-dsh-plugin.com/plugins.json",
                           timeout=45).read().decode("utf-8", "replace"))
except Exception as e:
    print("市场查询失败：%s" % str(e)[:60])
    d = []
items = d if isinstance(d, list) else (d.get("plugins") or d.get("items") or [])
print("市场条目 %d 个\n" % len(items))

# 本机已装（去掉 workbuddy 的 peer 依赖）
INSTALLED = ["dsh-better-sidebar", "dsh-context", "dsh-video-lens",
             "dsh-video-preview", "dsh-vision-router", "dsh-whale-widget",
             "dsh-workbuddy-connect", "dshmarket", "open-design-mcp"]
print("=" * 74)
print("本机已装（9 个业务插件）")
print("=" * 74)
for n in INSTALLED:
    print("  %s" % n)

WANT = [
    ("1 DSH Web UI", ["web-ui", "webui", "dsh-web-ui", "任务看板", "git graph",
                      "全家桶", "dsh-web"]),
    ("2 ModLens", ["modlens", "mod-lens", "视觉", "ocr", "截图分析", "图片理解"]),
    ("3 DSH Better Sidebar", ["better-sidebar", "sidebar", "侧边栏"]),
    ("4 dsh-TUI", ["dsh-tui", "-tui", "terminal ui", "cli 界面"]),
    ("5 Aegis", ["aegis", "工程规范", "任务检查", "代码可靠性"]),
]

print()
print("=" * 74)
print("逐个在市场上找")
print("=" * 74)
for label, keys in WANT:
    print("\n--- %s ---" % label)
    hits = []
    for it in items:
        s = json.dumps(it, ensure_ascii=False).lower()
        name = (it.get("name") or "").lower()
        if any(k.lower() in s for k in keys):
            # 名字直接命中的排前面
            score = 2 if any(k.lower() in name for k in keys) else 1
            hits.append((score, it))
    hits.sort(key=lambda x: -x[0])
    if not hits:
        print("   市场上没有明显对应的插件")
        continue
    for score, it in hits[:5]:
        mark = ""
        if it.get("name") in INSTALLED:
            mark = "  ← **已装**"
        de = it.get("description") or {}
        zh = de.get("zh") if isinstance(de, dict) else str(de)
        print("   %s%-30s %-14s%s" % ("★" if score == 2 else " ",
                                      it.get("name"), it.get("owner"), mark))
        print("      %s" % (zh or "")[:110])

print()
print("=" * 74)
print("结论")
print("=" * 74)
for label, keys in WANT:
    hit_installed = [n for n in INSTALLED
                     if any(k.lower() in n.lower() for k in keys
                            if not re.search(r"[\u4e00-\u9fff]", k))]
    print("  %-24s %s" % (label,
                          ("✅ 已装：%s" % "、".join(hit_installed))
                          if hit_installed else "❌ 未装"))
