# -*- coding: utf-8 -*-
r"""把视觉设定里用的名字补成数据库实体的**别名**，消掉纯命名漂移。

## 背景

两条产出线各用各的命名，已经漂移（`tools/lint_cross_doc.py` 抓出来的）：

    视觉设定.md        数据库实体
    硬纸牌脚签    vs   遗体脚趾牌
    塑形器       vs   口部塑形器
    签字板（主管） vs   行政文件签字板
    中年男主管    vs   主管
    焚化车间      vs   殡仪馆焚化车间

这些**不是内容分歧，只是叫法不同**。做法：把文档里的叫法补进
对应实体的 `alias`。这样：
  · 生图提示词用**库里的规范名**（下游一致）
  · 文档里的叫法也能被识别（不至于当成两个东西）

## 不做的事

- **不删**文档里的名字，也不改文档正文
- **不合并**有内容分歧的项（下节列出，交人判断）

⚠️ 「玫瑰色痕迹（白单）」**不在**本脚本处理范围：它是白单上的**痕迹**
（状态），而库里 `棉质遗体盖单` 是**物件**——两者是"物"与"物的状态"，
该不该拆成两个实体需要人来定。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

# 文档里的叫法 -> 库里的规范名（纯命名差异，可安全互为别名）
MAP = {
    "硬纸牌脚签": "遗体脚趾牌",
    "塑形器": "口部塑形器",
    "签字板（主管）": "行政文件签字板",
    "签字板": "行政文件签字板",
    "中年男主管": "主管",
    "焚化车间": "殡仪馆焚化车间",
    "殡仪馆整容室": "殡仪馆整容室",
}

# 需要人判断的（不自动处理，只报告）
NEEDS_HUMAN = [
    ("玫瑰色痕迹（白单）", "棉质遗体盖单",
     "一个是「物」（盖单），一个是「物上的痕迹」（状态）。"
     "拆成两个实体，还是作为盖单的一个状态字段？"),
    ("（文档未写）", "颌部固定金属针",
     "库里抽出来了、剧本也写了（『手指底下有金属针的硬度』），"
     "但视觉设定没提。是漏写，还是觉得不必单独出图？"),
    ("（文档未写）", "电脑控制焚化炉",
     "同上。焚化车间的核心设备，但视觉设定只在场景描述里带过。"),
]

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-alias-sync-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=" * 72)
print("补别名（消掉纯命名漂移）")
print("=" * 72)
n = 0
for doc_name, db_name in MAP.items():
    row = con.execute("select id, alias from entity where name=?",
                      (db_name,)).fetchone()
    if not row:
        print("  ⚠️ 库里没有「%s」，跳过（%s）" % (db_name, doc_name))
        continue
    if doc_name == db_name:
        continue
    al = [x.strip() for x in (row["alias"] or "").split(",") if x.strip()]
    if doc_name in al:
        print("  -- 「%s」已经是「%s」的别名" % (doc_name, db_name))
        continue
    al.append(doc_name)
    con.execute("update entity set alias=? where id=?",
                (", ".join(al), row["id"]))
    print("  ✓ 「%s」补为「%s」的别名" % (doc_name, db_name))
    n += 1
con.commit()
print("\n  补了 %d 条别名" % n)

print("\n" + "=" * 72)
print("需要人判断的（**没自动动**）")
print("=" * 72)
for doc_name, db_name, why in NEEDS_HUMAN:
    print("  ? %s  ⟷  %s" % (doc_name, db_name))
    print("      %s" % why)
    print()

print("=" * 72)
print("复查：现在还剩几处不一致")
print("=" * 72)
con.close()
os.system('"%s" tools\\lint_cross_doc.py --episode-id 1'
          % sys.executable)
