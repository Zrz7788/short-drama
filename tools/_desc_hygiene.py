# -*- coding: utf-8 -*-
r"""实体描述里不能有 Markdown 标记——它会原样发给模型。

## 为什么

`description` 与 `style_note` 会被拼进生图提示词（见 `pipeline/prompts/portrait.md`）。
我刚刚往整容台描述里写了 `**电动**`——**星号会原样进入提示词**，
模型可能把它当内容画出来，或至少干扰理解。

## 真问题：描述里的"元信息"不止 Markdown

顺带扫一遍**所有**实体描述，看还有哪些不该出现在提示词里的东西：
  · Markdown 标记（`**` `__` 反引号 行首 `#`）
  · 元信息口吻（"领域事实缺失，待补""不得擅自添加…"）
    —— 这是给**人看**的备注，不是给模型的指令
  · 指向别的文档的话（"见视觉设定"）
"""
import os
import re
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

# Markdown 标记
MD = re.compile(r"\*\*|__|`|^#{1,6}\s", re.M)
# 元信息口吻（给人看的备注）
META = ("领域事实缺失", "待补", "不得擅自添加", "见视觉设定", "见《", "TODO",
        "FIXME", "注意：", "说明：", "备注：")

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-desc-hygiene-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=" * 74)
print("扫描：实体描述 / 风格备注里的非提示词内容")
print("=" * 74)
n_md = n_meta = 0
for r in con.execute("select id,name,kind,description,style_note from entity"):
    for field in ("description", "style_note"):
        v = r[field] or ""
        if not v:
            continue
        md = MD.findall(v)
        meta = [k for k in META if k in v]
        if md:
            n_md += 1
            print("  ⚠️ [%s] %-16s %-12s Markdown: %s"
                  % (r["kind"], r["name"], field, sorted(set(md))))
        if meta:
            n_meta += 1
            print("  ⚠️ [%s] %-16s %-12s 元信息: %s"
                  % (r["kind"], r["name"], field, meta))
print()
print("  含 Markdown %d 处，含元信息 %d 处" % (n_md, n_meta))

print()
print("=" * 74)
print("清理 Markdown 标记（元信息留给人判断，不自动删）")
print("=" * 74)
fixed = 0
for r in con.execute("select id,name,description,style_note from entity"):
    for field in ("description", "style_note"):
        v = r[field] or ""
        if not v:
            continue
        nv = v.replace("**", "").replace("__", "").replace("`", "")
        nv = re.sub(r"^#{1,6}\s*", "", nv, flags=re.M)
        if nv != v:
            con.execute("update entity set %s=? where id=?" % field,
                        (nv, r["id"]))
            fixed += 1
            print("  ✓ %s.%s" % (r["name"], field))
con.commit()
print("\n  清理 %d 处" % fixed)

print()
print("=" * 74)
print("抽查")
print("=" * 74)
r = con.execute("select description from entity where name='不锈钢整容作业台'"
                ).fetchone()
print("  整容台: %s" % (r["description"] or "")[:120])
con.close()
