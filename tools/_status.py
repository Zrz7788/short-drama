# -*- coding: utf-8 -*-
r"""短剧项目当前进度快照。"""
import os
import sqlite3

c = sqlite3.connect("data/drama.db")
c.row_factory = sqlite3.Row
q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731

print("=" * 70)
print("项目")
print("=" * 70)
p = c.execute("select name, logline from project").fetchone()
print("  剧名: %s" % p["name"])
print("  %s" % (p["logline"] or "")[:78])

print("\n" + "=" * 70)
print("十四个阶段的进度")
print("=" * 70)
print("  已花: ¥%.2f" % q("select coalesce(sum(amount),0) from cost_ledger"))
print()
print("  %-22s %-10s %s" % ("阶段", "状态", "实际产出"))
print("  " + "-" * 62)

rows = [
    ("0 spec 规格", "project 表 + 项目档案"),
    ("1 script 剧本", "%d 版现行 / %d 场" % (
        q("select count(*) from script where is_current=1"), q("select count(*) from scene"))),
    ("1.5 领域问题", None),
    ("2 cast 实体", "%d 个实体" % q("select count(*) from entity")),
    ("3/4 art 参考图", "%d 张" % q("select count(*) from entity_ref")),
    ("4.5 布局图", None),
    ("5 storyboard 分镜", "%d 个镜头" % q("select count(*) from shot")),
    ("6 prompts 提示词", "%d 条" % q("select count(*) from shot where prompt is not null and prompt<>''")),
    ("7 keyframes 关键帧", "%d 张" % q("select count(*) from shot where keyframe_path is not null")),
    ("8/9/10 视频→成片", "%d 条生成记录 / %d 个成片" % (
        q("select count(*) from generation"), q("select count(*) from asset"))),
]
# 领域问题
md = "knowledge/03-项目档案/殡仪馆的阿九.md"
nq = nans = 0
if os.path.exists(md):
    import re
    t = open(md, encoding="utf-8").read()
    m = re.search(r"^##\s*领域问题\s*$(.*?)(?=^##\s|\Z)", t, re.M | re.S)
    if m:
        for ln in m.group(1).splitlines():
            if ln.strip().startswith("| Q-"):
                nq += 1
                cells = [x.strip() for x in ln.strip("|").split("|")]
                if len(cells) > 2 and cells[2] and cells[2] != "待查":
                    nans += 1
    rows[2] = ("1.5 领域问题", "%d 条，%d 条已答" % (nq, nans))
# 布局图
lay = q("select count(*) from entity_ref where view='layout'")
rows[5] = ("4.5 布局图", "%d 张" % lay)

for name, detail in rows:
    print("  %-22s %s" % (name, detail or "-"))

print("\n" + "=" * 70)
print("分镜 / 场景明细")
print("=" * 70)
for r in c.execute("select seq, slug, beat, duration_s from scene order by seq"):
    print("  第%d场  %-28s %-4s %5.1f 秒" % (r["seq"], r["slug"], r["beat"], r["duration_s"]))
print("  " + "-" * 60)
print("  合计 %.1f 秒" % q("select coalesce(sum(duration_s),0) from scene"))
print("  镜头 %d 个，时长合计 %.1f 秒" % (
    q("select count(*) from shot"), q("select coalesce(sum(duration_s),0) from shot")))

print("\n" + "=" * 70)
print("下一位阻塞点")
print("=" * 70)
print("  剧本要改（取证推翻了 3 处设定）→ 改完要重跑 2 阶段实体、")
print("  3/4 阶段出图、5 阶段分镜、6 提示词、7 关键帧，才轮到视频。")
