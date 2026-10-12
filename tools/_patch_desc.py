# -*- coding: utf-8 -*-
r"""把「视觉设定」里写了、但「实体描述」里漏掉的可见特征补进去。

## 为什么

生图提示词是从**实体描述的 description 字段**拼的（`pipeline/prompts/portrait.md`），
**不是从视觉设定拼的**。所以视觉设定里写了、实体描述里没写的东西，
**根本不会出现在图里**。

实测两处：

  盛伯渊：设定写「口中咬着金属塑形器」，实体描述写的是
          「唇线被塑形器撑得平直」——**只描述效果、没写物件**，
          于是模型把嘴唇画平了，**没画器械**。而塑形器是他在全集的
          物理限制（说话含混、台词时长），必须可见。
  沈九  ：设定两处都写了「一次性手术帽」，**实体描述里完全没提**，
          于是两张图都没帽子。**两条线各写一半。**

## 做法

**只补"可见特征"，不动故事设定。** 并把「效果描述」改成「物件描述」——
模型画的是物件，不是效果。

改完要重出这几张（¥0.30/张）。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

# 实体名 -> (旧片段, 新片段)，做**精确替换**，不动别的字
PATCH = {
    "盛伯渊": [
        ("皮肤苍白无血色、唇线被塑形器撑得平直",
         "皮肤苍白无血色、口中咬着一枚金属口部塑形器"
         "（薄金属边沿自唇间微露，唇线被撑得平直而不自然）"),
    ],
    "沈九": [
        ("黑色头发，低扎马尾、额前有少量碎发",
         "黑色头发低扎、戴一次性白色手术帽，"
         "几缕碎发压在帽沿外"),
    ],
}

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-desc-patch-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
changed = []
for name, pairs in PATCH.items():
    r = con.execute("select id, description from entity where name=?",
                    (name,)).fetchone()
    if not r:
        print("  ⚠️ 库里没有「%s」" % name)
        continue
    d = r["description"] or ""
    new = d
    for old, rep in pairs:
        if old not in new:
            print("  ⚠️ 「%s」里找不到待替换片段：%s" % (name, old[:26]))
            continue
        new = new.replace(old, rep, 1)
    if new == d:
        print("  -- 「%s」无需改动" % name)
        continue
    con.execute("update entity set description=? where id=?", (new, r["id"]))
    changed.append((name, d, new))
    print("  ✓ 「%s」已更新" % name)
con.commit()

print()
print("=" * 74)
print("改动对照")
print("=" * 74)
for name, old, new in changed:
    print("\n--- %s ---" % name)
    print("  旧: %s" % old[:150])
    print("  新: %s" % new[:150])

print()
print("=" * 74)
print("提醒：改完要重出这几张（¥0.30/张）")
print("=" * 74)
for name in PATCH:
    r = con.execute("select role from entity where name=?", (name,)).fetchone()
    n = 2 if (r and r["role"] == "main") else 1
    print("  %s：%d 张" % (name, n))
print("  合计约 ¥%.1f" % (sum(2 if (con.execute(
    "select role from entity where name=?", (n,)).fetchone() or {}) and
    con.execute("select role from entity where name=?",
                (n,)).fetchone()["role"] == "main" else 1
    for n in PATCH) * 0.30))
con.close()
