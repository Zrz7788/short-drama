# -*- coding: utf-8 -*-
r"""撤回「口部塑形器」的可见化描述——它被画成了刀片。

## 发生了什么

原实体描述只写「唇线被塑形器撑得平直」（只描述效果）。我在图里看不到器械，
就把描述改成「口中咬着一枚金属口部塑形器（薄金属边沿自唇间微露）」。
结果模型**照字面画了一根横穿嘴唇的金属刀片**，尖端朝外——
观众会读成"他嘴里被插了把刀"，**比不画更糟**。

## 教训

中文里「薄金属边沿自唇间微露」这种**方位+材质**的描述，
模型会落成**最显眼的几何形状**（一根横杆/刀片），
而不是我们想的"含蓄露一点"。

**塑形器主要在口腔内侧，它的可见表现就是唇形**——
嘴唇被撑得平直、略外翻、闭合不自然。这是真实的：
`mouth former` 是放进嘴里的，外面只看到嘴形变了。

所以改回**只描述唇形**，并加上"不画器械"的肯定式表述
（按项目的规矩：否定式无效，要写肯定式）。

⚠️ 顺带：`--keep-face` 这次用的是**已经带刀片的旧大头照**当参考，
所以**脸的参考要退回再往前一版**——但那一版已被覆盖。
处理办法：这次重出时**不挂面部参考**（`--keep-face` 关掉），
让模型**重新给一张脸**，然后以新全身照为准重出大头照。
代价是脸会变一点；好在只出过 2 次，还没进视频。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

OLD = ("口中咬着一枚金属口部塑形器（薄金属边沿自唇间微露，"
       "唇线被撑得平直而不自然）")
NEW = ("唇部被口部塑形器撑得平直、略外翻，闭合不自然"
       "（只在唇形上体现，画面中不出现器械本身）")

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-shaper-revert-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
r = con.execute("select id, description from entity where name='盛伯渊'").fetchone()
if not r:
    print("没有盛伯渊")
    sys.exit(1)
d = r["description"] or ""
if OLD not in d:
    print("⚠️ 找不到待替换片段，当前描述：")
    print("  %s" % d[:260])
    sys.exit(1)
new = d.replace(OLD, NEW, 1)
con.execute("update entity set description=? where id=?", (new, r["id"]))
con.commit()

print("=" * 74)
print("改后")
print("=" * 74)
print("  %s" % new)

print()
print("=" * 74)
print("接下来怎么重出（**不挂面部参考**，因为旧参考已带刀片）")
print("=" * 74)
print("  python pipeline/stage_refs.py --episode-id 1 --phase characters \\")
print("      --only 盛伯渊 --force")
print()
print("  ⚠️ 不加 --keep-face：那张参考已经带刀片了，挂着它会继续传染。")
print("     让模型重新给脸，之后以新全身照为准重出大头照（管线自动如此）。")
con.close()
