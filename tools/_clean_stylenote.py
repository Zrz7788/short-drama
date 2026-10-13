# -*- coding: utf-8 -*-
r"""清掉 style_note 里过时的元信息与无效的否定式。

## 两个问题（都会直接发给模型）

`stage_refs.py:125` / `stage_prompts.py:95` 会把 style_note 拼进提示词：
    body += "（%s）" % e["style_note"].strip()

### ① 过时的元信息
9 条 style_note 写着「领域事实缺失，待补」——那是**取证之前**的临时备注。
而现在每条都有 `domain_fact`（F-01/F-14/F-16/F-21…），领域事实早取完了。
于是它变成：**一句过时的话，被塞进生图提示词**。

### ② 无效的否定式
几条写着「不得标注为紧急呼叫按钮」「不得限定材质为医用乳胶」
「不得擅自添加殡葬专用装饰」。
而**项目自己的结论就是"否定约束对画面无效"**（`prompts/portrait.md`：
Higgs Field「没有任何模型带反向提示词参数」）。写在()里的"不得X"
既约束不住模型，又占用了本该放**正向描述**的位置。

## 做法

只删这两类，**保留真正描述外观的部分**（光源、年代、材质、磨损）。
删掉的"不得…"如果确实重要，应该改写成**正向的替代描述**——
本脚本在能改写的地方直接改写（如"不得标注为紧急呼叫按钮"
⇒"台面下侧的普通低压控制按钮"）。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

# 精确改写：整条 style_note 换成干净版
FIX = {
    "殡仪馆后廊":
        "主光源为冷白色嵌入式荧光灯，光线昏暗，空间空旷狭长，墙面无装饰",
    "一次性蓝色手套":
        "普通清洁防护用的一次性蓝色手套，2000年代常用款式，材质轻薄贴合",
    "整容台控制按钮":
        "台面下侧的普通低压控制按钮，圆形塑料外壳，暗灰色",
    "整容工作记录板":
        "2000年代殡仪馆一线操作通用记录文具，金属夹板配白纸，边角有轻微磨损",
    "行政文件签字板":
        "2000年代通用办公文具，塑料夹板配纸张，边角有轻微磨损",
    "塑料调色板":
        "普通美术用塑料调色板，表面为若干圆形颜料凹槽，无多余装饰",
    "方形海绵块":
        "普通清洁用方形海绵，米白色，表面为均匀细孔",
    "脚踏式医疗垃圾桶":
        "普通医用脚踏式带盖垃圾桶，黄色桶身，2000年代常用款式",
    "黑色圆珠笔":
        "2000年代通用办公圆珠笔，黑色笔杆，有轻微使用痕迹",
}

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-stylenote-clean-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("=" * 74)
print("清理 style_note")
print("=" * 74)
n = 0
for name, new in FIX.items():
    r = con.execute("select id, style_note from entity where name=?",
                    (name,)).fetchone()
    if not r:
        print("  ⚠️ 库里没有「%s」" % name)
        continue
    old = r["style_note"] or ""
    if old == new:
        print("  -- 「%s」已是干净版" % name)
        continue
    con.execute("update entity set style_note=? where id=?", (new, r["id"]))
    n += 1
    print("  ✓ %s" % name)
    print("      旧: %s" % old[:76])
    print("      新: %s" % new[:76])
con.commit()
print("\n  清理 %d 条" % n)

print()
print("=" * 74)
print("复查：还有没有元信息 / 否定式")
print("=" * 74)
BAD = ("领域事实缺失", "待补", "不得", "TODO", "见《", "见视觉设定")
bad = [(r["name"], k) for r in con.execute(
    "select name, style_note from entity") for k in BAD
    if k in (r["style_note"] or "")]
if bad:
    for name, k in bad:
        print("  ⚠️ %s 含「%s」" % (name, k))
else:
    print("  ✅ 实体 style_note 里没有元信息与否定式")
con.close()
