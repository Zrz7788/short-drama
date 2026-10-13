# -*- coding: utf-8 -*-
r"""定案：人物参考描述**不含道具**；并修掉「主管」两处设定互相矛盾。

## 一、规矩（本项目从此按此执行）

`pipeline/stage_refs.py` 的素材体系写着：
  fullbody 全身照——**妆造与体态参考**
  headshot 大头照——**锁死面部细节**

所以**人物参考描述里只放相貌与妆造**，不放：
  · 手里/身上拿的道具（签字板、笔、记录板）
  · 穿戴在**遗体**身上的场景道具（塑形器、白单、脚趾牌）
  · 剧情动作（"不抬头""递给沈九"）

**道具与动作归镜头提示词（`stage_prompts`）管。**

依据：今天把「塑形器」写进盛伯渊的人物描述，模型就把它画成了
**横穿嘴唇的刀片**——描述什么就画什么，写错了就画错。

## 二、「主管」这条的具体问题（两处）

### 1. 描述里混了道具
实体描述里没有签字板，但 `style_note` 结尾写着「常夹持深灰色文件夹签字板」
——`style_note` **同样会进提示词**（`stage_refs.py:125`）。
⇒ 删掉这个道具句。

### 2. 两个设定写的是两个不同的人
  视觉设定（识别锚点）：短袖衬衫外搭深色夹克；戴金属细框眼镜（剧本）
  实体描述（生图输入）：深灰色平驳领西装外套 + 白衬衫 + 藏青斜条纹领带 +
                        黑色垂感西裤 + 黑色亮面牛皮鞋；黑框塑料眼镜

⇒ **以实体描述为准**（它是生图输入，且"西装+领带"更符合"行政主管"
这一身份，剧本里他夹着签字板走来走去）。把**视觉设定改成与之一致**，
并把眼镜改为与剧本一致的**金属细框**。
"""
import os
import re
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DB = "data/drama.db"
VIS = os.path.join("剧集", "EP001", "视觉设定.md")

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-guanzhu-fix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("数据库已备份 -> %s" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print()
print("=" * 74)
print("1) 清掉实体描述里的道具句")
print("=" * 74)
r = con.execute("select id, description, style_note from entity "
                "where name='主管'").fetchone()
d, note = r["description"] or "", r["style_note"] or ""
# 删掉 style_note 末尾的道具句
note2 = re.sub(r"[；;]?\s*常夹持[^；;]*签字板\s*$", "", note).strip()
# 眼镜统一为剧本里的金属细框
d2 = d.replace("黑框塑料眼镜", "金属细框眼镜")
con.execute("update entity set description=?, style_note=? where id=?",
            (d2, note2, r["id"]))
print("  description: %s" % d2[:120])
print("  style_note : %s" % note2)

print()
print("=" * 74)
print("2) 视觉设定改成与实体描述一致")
print("=" * 74)
if os.path.exists(VIS):
    shutil.copy2(VIS, VIS + ".bak-before-guanzhu-"
                 + time.strftime("%Y%m%d-%H%M%S"))
    t = open(VIS, encoding="utf-8").read()
    old = ("- 识别锚点：中年男性，短袖衬衫外搭深色夹克，胳膊下夹塑料写字夹板，"
           "手边常年一支黑框笔。")
    new = ("- 识别锚点：中年男性，发胶固定的整齐偏分短发，金属细框眼镜，"
           "深灰色平驳领西装外套＋白色翻领棉衬衫＋藏青色斜条纹领带，"
           "黑色垂感西裤、黑色亮面皮鞋。\n"
           "- 道具（**属镜头提示词，不进人物参考**）：深灰色文件夹签字板、"
           "黑色圆珠笔。手里夹不夹、怎么夹，由分镜与镜头提示词决定。")
    if old in t:
        t = t.replace(old, new, 1)
        open(VIS, "w", encoding="utf-8").write(t)
        print("  ✓ 识别锚点已与实体描述对齐")
    else:
        print("  ⚠️ 找不到待改的识别锚点行")
con.commit()
con.close()

print()
print("=" * 74)
print("复查")
print("=" * 74)
os.system('"%s" "%s" --episode-id 1' % (sys.executable,
                                        os.path.join(ROOT, "tools",
                                                     "prompt_review.py")))
