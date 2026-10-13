# -*- coding: utf-8 -*-
r"""修最后两条含内部术语 / 画面文字风险的 style_note。

## 死亡确认书

style_note 原本已经想到"空镜生成时无需显示具体姓名与签名，
不得出现真实可识别的个人信息"——**方向是对的**，但：
  · description 里却写着「设有死者姓名栏，背面设有见证人签名栏，
    栏内有**手写签名痕迹**」——**这会诱发画面文字**
  · 而 v1 就出过事：全身照胸前被印上真实商标与角色名。

**这是同一类风险。** 按项目既有结论（否定式对画面无效），
正确做法是**正向写成"占位、无字"**：
  姓名/签名处 -> **纯白空栏，无任何字符**

## 透明润肤膏

style_note 写着「不得描述为化妆品类唇釉/口红，**符合F18行业规范**」——
**「F18」是我们自己的领域事实编号，会被原样发给模型。**
改成纯视觉描述，把"不是唇釉/口红"这个约束改成正向的
（按 F-14：真实是膏体/润肤剂，不是化妆品）。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

FIX = {
    "死亡确认书": {
        "description":
            "A4幅面白色木浆打印纸，纸张陈旧发黄，边角向上卷起，"
            "有明显多次翻阅的折痕；正面为制式表格排版，"
            "设有死者姓名栏与编号栏，各栏内一律为纯白空白、无任何字符；"
            "背面设见证人签名栏，栏内同样为纯白空白、无笔迹",
        "style_note":
            "2000年代殡仪馆制式文件排版，表格线条清晰，字段区为纯色空白，"
            "整张纸表面无任何可辨认的文字与签名",
    },
    "透明润肤膏": {
        "style_note":
            "普通的无色透明护肤膏体，无香精色素，玻璃瓶身无标签",
    },
}

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-ip-terms-fix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("=" * 74)
print("修正")
print("=" * 74)
for name, fields in FIX.items():
    r = con.execute("select id, description, style_note from entity "
                    "where name=?", (name,)).fetchone()
    if not r:
        print("  ⚠️ 库里没有「%s」" % name)
        continue
    sets, vals = [], []
    for f, v in fields.items():
        if (r[f] or "") == v:
            continue
        sets.append("%s=?" % f)
        vals.append(v)
    if not sets:
        print("  -- 「%s」无需改动" % name)
        continue
    vals.append(r["id"])
    con.execute("update entity set %s where id=?" % ", ".join(sets), vals)
    print("  ✓ %s" % name)
    for f, v in fields.items():
        print("      %-12s %s" % (f, v[:96]))
con.commit()

print()
print("=" * 74)
print("复查：内部编号 / 元信息 / 否定式")
print("=" * 74)
BAD = ("领域事实缺失", "待补", "不得", "F1", "F2", "F9", "见《", "TODO")
bad = [(r["name"], k) for r in con.execute(
    "select name, style_note, description from entity")
    for k in BAD if k in (r["style_note"] or "") or k in (r["description"] or "")]
if bad:
    for name, k in bad:
        print("  ⚠️ %s 含「%s」" % (name, k))
else:
    print("  ✅ 实体描述与风格备注里没有内部编号 / 元信息 / 否定式")
con.close()
