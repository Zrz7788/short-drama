# -*- coding: utf-8 -*-
r"""修正「纯色空白」规则被套过头的地方，并给审查器加例外。

## 我修错了什么

审查器报了「黄铜签牌 提到吊牌但没写纯色空白」，我就去把它改成
「牌面为纯色空白、表面平整无字」。**但这是错的**：

  视觉设定：殡仪馆运尸车/炉位对应签，黄铜制，**刻字**
  剧本：    「抽下系在推把上的黄铜签牌，看了一眼」

**签牌的刻字是它的识别功能**——那是"有意义的内容"，不是要遮的噪声。

## 规则的正确边界

「纯色空白」针对的是**可能被模型填上商标或无关文字的装载体**：
  工牌、包装、招牌、证件、便签、纸张……
（依据：v1 沈九胸前工号位被印上了真实商标。）

而**识别性铭牌**的文字是剧情内容，必须保留：
  黄铜签牌（炉位对应签，刻字）
  硬纸牌脚签（"三〇〇 盛伯渊 入炉 09:00"）
  死亡确认书（姓名栏"沈长庚"、见证人栏"沈九"——剧本自己标了
  `[画面文字]`，是**要拍出来的**）

⇒ 规则加例外：**属于识别铭牌/画面文字的，不算"该空白"。**

## 同时修两处实体描述的事实错误

  签牌挂处：描述写「可悬挂于脚踝处」——**过时**。
    取证改了：黄铜签牌挂**推把**（脚踝那条是遗体脚趾牌，另一件东西）。
  签牌文字：改回「刻有炉位编号」，不是纯色空白。
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

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-tag-fix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
r = con.execute("select id, description from entity where name='黄铜签牌'"
                ).fetchone()
d = r["description"] or ""
print("=" * 74)
print("黄铜签牌 改前")
print("=" * 74)
print("  %s" % d)

# 1) 去掉我加的、错误的"纯色空白"句（含残留的 ** 标记）
d = re.sub(r"[；;]?\s*牌面为\**纯色空白、表面平整无字\**\s*（不刻画任何字符）?",
           "", d)
d = d.replace("**", "")
# 2) 挂处改对：推把（不是脚踝）
d = d.replace("可悬挂于脚踝处", "可悬挂于整容台的金属推把上")
# 3) 文字说明：刻字是识别功能，保留
if "刻" not in d and "工号" in d:
    d = d.replace("表面印黑色工号数字", "表面刻有炉位编号（深色刻痕，清晰可辨）")
d = d.rstrip("。；;") + "。"
con.execute("update entity set description=? where id=?", (d, r["id"]))
con.commit()
print()
print("=" * 74)
print("改后")
print("=" * 74)
print("  %s" % d)
con.close()

print()
print("=" * 74)
print("给审查器加例外：识别铭牌/画面文字不算「该空白」")
print("=" * 74)
PR = os.path.join(ROOT, "tools", "_anchor_rules.py")
t = open(PR, encoding="utf-8").read()
if "刻有" not in t:
    old = '''         "纹", "孔", "层", "款", "式", "质")'''
    new = '''         "纹", "孔", "层", "款", "式", "质",
         # 刻字/刻痕：库里可能写成"刻有炉位编号"或"带刻字"，
         # 所以判据要认"刻"这个字根，不要认死"刻字"这个词。
         # 踩过：黄铜签牌描述已写「表面刻有炉位编号（深色刻痕）」，
         # 规则却因为找不到"刻字"而报缺失。
         "刻")'''
    if old in t:
        t = t.replace(old, new, 1)
        open(PR, "w", encoding="utf-8").write(t)
        print("  ✓ 外观判据加入「刻」字根")
    else:
        print("  ⚠️ 找不到 LOOKS 定义")
else:
    print("  -- 已加过")

PR2 = os.path.join(ROOT, "tools", "prompt_review.py")
t2 = open(PR2, encoding="utf-8").read()
old2 = '''        LABEL_CARRIERS = ("工牌", "证件", "招牌", "标签", "吊牌", "铭牌",
                          "印刷物", "文件", "证书", "证明")'''
new2 = '''        LABEL_CARRIERS = ("工牌", "证件", "招牌", "标签", "吊牌", "铭牌",
                          "印刷物", "文件", "证书", "证明")
        # ⚠️ 例外：**识别性铭牌与剧本标了 [画面文字] 的内容**，
        #    它们的文字是剧情内容、必须保留，不算"该空白"。
        #    踩过：我把黄铜签牌改成"纯色空白"——而签牌刻字是它的识别功能，
        #    剧本还写了「抽下签牌看了一眼」。
        IDENTIFYING = ("签牌", "脚签", "脚趾牌", "死亡确认书", "死亡证明",
                       "画面文字", "工号牌", "编号牌")
        if any(k in name for k in IDENTIFYING) or any(
                k in blob for k in IDENTIFYING):
            return blocks, warns, notes'''
if old2 in t2 and "IDENTIFYING" not in t2:
    t2 = t2.replace(old2, new2, 1)
    open(PR2, "w", encoding="utf-8").write(t2)
    print("  ✓ 已加 IDENTIFYING 例外")
else:
    print("  -- 例外已加过或无需改动")
