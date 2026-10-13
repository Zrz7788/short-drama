# -*- coding: utf-8 -*-
r"""按 v2 剧本修正《视觉设定.md》里三处过时的场景描述。

## 依据（以剧本正文为准）

剧本是 v2（23:06 定稿），视觉设定（23:08）里有两处是从 v1 抄过来的，
与正文冲突：

  炉子数量：视觉设定写「**多台**火化炉排成一线」，
            剧本写「**一台**炉子亮着待机红灯」。⇒ 改视觉设定。
  推车    ：视觉设定写「车间内**停运尸推车**」，
            剧本写「她刚才停在门边的那辆车不在门边」——
            那「辆车」是**她推来的整容台**（"抓住整容台侧面的推把"），
            不是独立的运尸推车；而且场内那台已不在，属**失踪**。⇒ 改视觉设定。
  整容台  ：实体描述只写「不锈钢专用整容作业台」，少了"**电动**"，
            而视觉设定与剧本都指向可操控的电动台（"台面下方嵌按钮"、
            "抓住整容台侧面的推把"）。⇒ 两条都补上"电动"。

**实体描述改的是补全，视觉设定改的是纠错**，两类都做。
"""
import os
import shutil
import sqlite3
import sys
import time

VIS = os.path.join("剧集", "EP001", "视觉设定.md")
DB = "data/drama.db"

EDITS = [
    ("### 殡仪馆后廊",
     "墙上挂红字数码倒计时牌（显示距入炉的剩余时间，能跳秒）；地面有水渍反光。",
     "墙上挂金属外壳的**红字数码倒计时牌**（显示距入炉的剩余时间，能跳秒）；"
     "地面为浅灰水泥、残留少量水渍反光。"),
    ("### 焚化车间",
     "多台火化炉排成一线，待机红灯亮；数码温度面板读数爬升；"
     "炉门边贴刚打印的条子（纸角翘起）；车间内停运尸推车。",
     "**一台**大型金属火化炉（剧本正文明写「一台炉子亮着待机红灯」，"
     "不是多台排队）；炉边配**电脑控制面板**，待机红灯亮、温度读数仍在爬升；"
     "炉门边贴一张刚打印的条子，纸角翘起。"
     "**车间入口是空的**——三〇〇号整容台本停在门口，此刻不在（失踪，见 EP001-SC002）。"
     "**不画独立的运尸推车**：「她刚才停在门边的那辆车」就是她推来的整容台。"),
    ("### 殡仪馆整容室",
     "中央电动整容台，台下右侧有红色塑料外壳按钮（呼救/警报）；"
     "黄色医疗垃圾桶；金属记录板。",
     "中央**电动**整容台（台面下方侧面嵌塑料外壳按钮，剧本「整容台下面摸到一个"
     "塑料外壳的按钮」）；**侧面有金属推把**（剧本「抓住整容台侧面的推把」）；"
     "墙角黄色脚踏式带盖医疗垃圾桶；金属记录板。"
     "**室内没有任何遗体冷藏设备。**"),
]


def main():
    if not os.path.exists(VIS):
        print("找不到 %s" % VIS)
        return 1
    bak = VIS + ".bak-before-scene-fix-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(VIS, bak)
    print("已备份 -> %s\n" % bak)

    t = open(VIS, encoding="utf-8").read()
    print("=" * 74)
    print("修《视觉设定.md》")
    print("=" * 74)
    for _head, old, new in EDITS:
        if old not in t:
            print("  ⚠️ 找不到片段：%s" % old[:40])
            continue
        t = t.replace(old, new, 1)
        print("  ✓ 已替换：%s…" % old[:34])
    open(VIS, "w", encoding="utf-8").write(t)

    print()
    print("=" * 74)
    print("补实体描述「电动」")
    print("=" * 74)
    if os.path.exists(DB):
        shutil.copy2(DB, DB + ".bak-before-table-fix-"
                     + time.strftime("%Y%m%d-%H%M%S"))
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        r = con.execute("select id, description from entity where name=?",
                        ("不锈钢整容作业台",)).fetchone()
        if r:
            d = r["description"] or ""
            if "电动" not in d:
                d2 = d.replace("不锈钢专用整容作业台",
                               "不锈钢电动专用整容作业台（液压升降、台面下侧嵌按钮、"
                               "侧面有金属推把）", 1)
                con.execute("update entity set description=? where id=?",
                            (d2, r["id"]))
                con.commit()
                print("  ✓ 已补：%s" % d2[:110])
            else:
                print("  -- 已含「电动」")
        else:
            print("  ⚠️ 库里没有「不锈钢整容作业台」")
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
