# -*- coding: utf-8 -*-
r"""清掉 prompt_review 报出的真提醒（跳过 1 条误报）。

## 报出的 9 条，逐条判定

真问题（本脚本修）：
  1 焚化车间 视觉设定「一台」/「待机红灯」/「条子」没进实体描述
    —— 实体描述才是生图输入，没写就不画
  2 黄铜签牌 缺「掌心大小」「带刻字」
  3 签字板 / 黄铜签牌 / 润肤膏 提到标签类载体却没写「纯色空白」
  4 整容室 冷白+暖白 没写明谁主谁辅
  5 建筑元素「天花」只有整容室写了 —— 这条是**体系问题**，
    已由 stage_refs 的「子空间先行」+ 后续「建筑设定」处理，此处只补描述

误报（不修，但要给规则加例外）：
  · 盛伯渊「右手力气反常地大」——**力道拍不出来**，不是外观。
    已把它从"外观项"的词性判据里排除。

## 关于「纯色空白」

依项目结论：**需要遮挡的地方不能被"删掉"，只能被"替换成纯色空白"**。
踩过：沈九左胸工号标识被印上真实商标。
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
bak = "%s.bak-before-warnfix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("数据库已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
FIX = []


def patch(entity, field, old, new, why):
    r = con.execute("select id, %s as v from entity where name=?" % field,
                    (entity,)).fetchone()
    if not r:
        print("  ⚠️ 没有「%s」" % entity)
        return
    v = r["v"] or ""
    if old not in v:
        print("  ⚠️ 「%s」.%s 找不到片段：%s" % (entity, field, old[:30]))
        return
    nv = v.replace(old, new, 1)
    con.execute("update entity set %s=? where id=?" % field, (nv, r["id"]))
    FIX.append((entity, field, why))
    print("  ✓ %s.%s  %s" % (entity, field, why))


print("=" * 74)
print("1) 焚化车间：把「一台」「待机红灯」「条子」写进描述")
print("=" * 74)
patch("殡仪馆焚化车间", "description",
      "房间内部放置大型金属焚化炉",
      "房间内部放置**一台**大型金属焚化炉（全室仅此一台，不是成排）",
      "明确炉子数量（剧本：一台炉子亮着待机红灯）")
patch("殡仪馆焚化车间", "description",
      "炉边配有电脑控制面板",
      "炉边配有电脑控制面板（面板上温度读数可见、**仍在往上爬**），"
      "炉体带**待机红色指示灯**",
      "补待机红灯与温度读数")
patch("殡仪馆焚化车间", "description",
      "炉门旁贴有边角翘起的白色纸质便签",
      "炉门旁贴**一张**刚打印的白色纸质便签、边角翘起、表面为纯色空白无字",
      "补条子 + 纯色空白（防模型自己填字）")

print()
print("=" * 74)
print("2) 黄铜签牌：补尺寸与刻字，并写明无字")
print("=" * 74)
patch("黄铜签牌", "description", "黄铜材质", "黄铜材质、掌心里大小", "补尺寸")
r = con.execute("select id, description from entity where name='黄铜签牌'"
                ).fetchone()
d = r["description"] or ""
if "刻字" not in d and "无字" not in d:
    patch("黄铜签牌", "description", d,
          d.rstrip("。；;") + "；牌面为**纯色空白、表面平整无字**（不刻画任何字符）",
          "牌面改纯色空白（防止填上真实文字/商标）")

print()
print("=" * 74)
print("3) 签字板 / 润肤膏：标签类载体写明纯色空白")
print("=" * 74)
patch("行政文件签字板", "description", "夹着待签文件",
      "夹着**表面为纯色空白的纸张**（无任何字符）", "文件改纯色空白")
patch("透明润肤膏", "description", "盛装在小型透明玻璃瓶内",
      "盛装在小型透明玻璃瓶内，**瓶身无标签、表面为纯色空白**",
      "瓶身无标签")

print()
print("=" * 74)
print("4) 整容室：写明主光/辅光的主次")
print("=" * 74)
patch("殡仪馆整容室", "style_note",
      "主光源为冷白色无影灯，辅助光源为镜前暖白光",
      "**主光**为冷白色无影灯（主），**辅光**为镜前暖白光（次，仅提亮面部）",
      "写明谁主谁辅（混光检查要求）")

con.commit()
con.close()

print()
print("=" * 74)
print("5) 顺带给审查器加例外：不看「力道」类项")
print("=" * 74)
PR = os.path.join(ROOT, "tools", "_anchor_rules.py")
t = open(PR, encoding="utf-8").read()
old = '''ACTIONS = ("说", "问", "答", "走", "抬头", "递给", "抽出", "塞进", "取下",'''
new = '''# 力道类：**拍不出来**，不是外观项。踩过：盛伯渊「右手力气反常地大」
# 被报成"像外观但描述里找不到"——这种描述本来就无法进画面。
NOT_VISIBLE = ("力气", "力量", "力大", "力气大", "体温", "冰凉", "温度感",
               "心跳", "呼吸", "气味", "声音", "语气", "性格", "心理")

ACTIONS = ("说", "问", "答", "走", "抬头", "递给", "抽出", "塞进", "取下",'''
if old in t and "NOT_VISIBLE" not in t:
    t = t.replace(old, new, 1)
    old2 = '''    if any(a in item for a in ACTIONS):
        return False
    return any(k in item for k in LOOKS)'''
    new2 = '''    if any(v in item for v in NOT_VISIBLE):
        return False
    if any(a in item for a in ACTIONS):
        return False
    return any(k in item for k in LOOKS)'''
    if old2 in t:
        t = t.replace(old2, new2, 1)
    open(PR, "w", encoding="utf-8").write(t)
    print("  ✓ 已加 NOT_VISIBLE 例外（力道/体温/声音等不可拍项不再报）")
else:
    print("  -- 无需改动或已加过")

print()
print("已修 %d 处" % len(FIX))
