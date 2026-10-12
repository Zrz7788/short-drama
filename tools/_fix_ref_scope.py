# -*- coding: utf-8 -*-
r"""按「人物参考只放妆造」这条线，修正两个人物的实体描述。

## 依据（pipeline/stage_refs.py 的素材体系注释）

  fullbody 全身照：完整妆造，部件级。用途是**妆造与体态参考**。
  headshot 大头照：仅面部。用途是**锁死面部细节**。

所以**人物参考里只该有妆造与相貌**，场景道具（嘴里的塑形器、白单、
脚趾牌）属于**镜头提示词**——它们只在少数镜头出现，放进人物参考
只会让模型在"妆造参考图"里去画道具，一路画歪。

## 本次两处修正

### 盛伯渊：把「唇」整段拿掉，还原面部骨骼
原 v1 有一条**具体**的面部描述「面部消瘦颧骨突出」，
后来被换成了抽象效果「唇线被塑形器撑得平直」——而抽象效果无法执行：
先被画成**横穿嘴唇的刀片**，改成"略外翻"后又变成**夸张嘟嘴**。
（两版描述见 image_generation #37~#40 的提示词差异。）

改法：**还原面部骨骼结构，删掉全部唇部描述**。
塑形器在口腔里，外面本来就不该看得见器械——**嘴唇自然闭合就对**。

### 沈九：补「胸前为纯色空白工牌」
v1 那版描述里有「左胸绣白色殡仪馆工号标识」，**结果被印上了真实商标**。
当时的修法是**把这个位置删掉**，并在模板里加了"一律纯色空白"的通用锁定。
但**实体描述里没有这一句**，而模型对通用段的执行远弱于实体描述——
删空之后胸口就没东西了。

**关键认识：需要遮挡的地方不能被"删掉"，只能被"替换成纯色空白"。**
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

PATCH = {
    # 盛伯渊：整段唇部描述 -> 面部骨骼结构
    "盛伯渊": [
        ("面部消瘦颧骨突出", None),   # 标记用：确认 v1 那层信息还在不在
    ],
}

# 直接用「改前 -> 改后」的完整片段，避免片段匹配歧义
REPLACE = [
    ("盛伯渊",
     "深棕色眼睛，左脸颊有一道长约3厘米的浅旧刀疤，"
     "皮肤苍白无血色、唇形平直、不自然的僵硬，唇色偏淡；",
     "面部消瘦、颧骨突出，深棕色眼睛，左脸颊有一道长约3厘米的浅旧刀疤，"
     "皮肤苍白无血色、唇色偏淡；"),
    ("沈九",
     "外穿同色系帆布工作围裙（正面有大容量贴袋）",
     "外穿同色系帆布工作围裙（正面有大容量贴袋，"
     "左胸位置为纯色空白工牌、表面平整无字）"),
]

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-ref-scope-fix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("=" * 74)
print("修正")
print("=" * 74)
for name, old, new in REPLACE:
    r = con.execute("select id, description from entity where name=?",
                    (name,)).fetchone()
    if not r:
        print("  ⚠️ 库里没有「%s」" % name)
        continue
    d = r["description"] or ""
    if old not in d:
        print("  ⚠️ 「%s」找不到片段：%s" % (name, old[:34]))
        continue
    d2 = d.replace(old, new, 1)
    con.execute("update entity set description=? where id=?", (d2, r["id"]))
    print("  ✓ 「%s」" % name)
con.commit()

print()
print("=" * 74)
print("改后全文")
print("=" * 74)
for name, _o, _n in REPLACE:
    r = con.execute("select description from entity where name=?",
                    (name,)).fetchone()
    if r:
        print("\n--- %s ---" % name)
        print("  %s" % r["description"])
con.close()
