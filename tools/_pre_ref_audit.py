# -*- coding: utf-8 -*-
r"""出图前：把道具/场景的实体描述与 v2 领域事实逐条对照。

## 为什么必须做

人物图那次，**描述写在 v2 领域事实之前**，结果 4 张全部不合设定、
白费 ¥1.20 才重出。道具描述写得更早（取证之前），风险更大。

本脚本做**交叉引用检查**：实体描述里的说法，能不能指回一条已确认事实？
以及有没有与「严禁」条相冲突？

对照表（`knowledge/03-项目档案/殡仪馆的阿九.md`）里对道具影响最大的几条：
  F-20  身份标识主流是 toe tag（脚趾牌），**很多地方因卫生已不再用**
  F-21  火化炉本体叫 retort，内衬耐火砖，现代炉电脑控制，**温度未到炉门不开**
  F-22  《死亡证明》签发单位为医疗卫生机构，由执业（助理）医师填写
  F-12  下颌闭合用 needle injector（金属针植入上颌/下颌）或结扎线
  F-14  唇部/面部整复常规是梳理头发 + 涂膏体/润肤剂；缺失用 tissue builder / wax filler
  F-13  无牙时用 mouth former 塑形口部
  F-16  作业面是 preparation room 内的专用作业台；转运推车只负责运进运出
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, ".")
from pipeline import stage_entities as SE  # noqa: E402

P = os.path.join("knowledge", "03-项目档案", "殡仪馆的阿九.md")
facts, ok, why = SE.parse_domain_facts(P)
print("领域事实：ok=%s  %s" % (ok, why))
print()

# 每条实体描述里出现的关键说法 -> 应当与哪些事实一致
CHECKS = [
    ("口部塑形器", ["F-13"],
     "F-13：无牙时用 mouth former 塑形口部"),
    ("遗体脚趾牌", ["F-20"],
     "F-20：主流是 toe tag，系在大脚趾"),
    ("颌部固定金属针", ["F-12"],
     "F-12：needle injector 把金属针植入上颌与下颌"),
    ("电脑控制焚化炉", ["F-21"],
     "F-21：retort，内衬耐火砖，电脑控制，温度未到炉门不开"),
    ("死亡确认书", ["F-22", "F-24"],
     "F-22/F-24：签发单位为医疗卫生机构；不得把整容师写成签字人"),
    ("不锈钢整容作业台", ["F-16"],
     "F-16：作业面是 preparation room 内的专用作业台"),
    ("透明润肤膏", ["F-14"],
     "F-14：常规是膏体/润肤剂，不是化妆品"),
    ("棉质遗体盖单", ["F-11"],
     "F-11：modesty cloth 覆盖"),
    ("整容工作记录板", ["F-01"],
     "F-01：独立 preparation room"),
    ("金属倒计时牌", ["F-03"],
     "F-03：殡仪馆通常有的空间构成（倒计时牌本身检索无命中，见 F-96）"),
]

con = sqlite3.connect("data/drama.db")
con.row_factory = sqlite3.Row

print("=" * 74)
print("逐条对照")
print("=" * 74)
warn = 0
for name, fids, note in CHECKS:
    r = con.execute("select description, style_note, domain_fact from entity "
                    "where name=?", (name,)).fetchone()
    if not r:
        print("  ⚠️ 库里没有「%s」" % name)
        warn += 1
        continue
    df = r["domain_fact"] or ""
    # 实体自己的 domain_fact 与建议的是否一致
    ok_df = any(f in df for f in fids)
    assumed = [f for f in fids if f in facts and facts[f]["assumed"]]
    mark = "✅" if ok_df and not assumed else ("⚠️" if not assumed else "🔸")
    if not ok_df:
        warn += 1
    print("  %s %-16s domain_fact=%-8s 建议 %s" % (mark, name, df, "/".join(fids)))
    print("       %s" % note)
    if assumed:
        print("       **依据的是假设·待验证事实：%s**" % "、".join(assumed))
    if not ok_df:
        print("       **它的 domain_fact 与建议不一致，请核对**")
    desc = r["description"] or ""
    # 与「严禁」条冲突的关键词
    for bad, whybad in (("口红", "F-18 严禁拿口红描唇"),
                        ("腕", "F-20 主流是脚趾牌，不是腕带"),
                        ("冷柜", "F-04 不许冷柜与化妆台同屋"),
                        ("推车", "F-16 推车只负责运进运出，不是作业面")):
        if bad in desc:
            print("       ⚠️ 描述含「%s」——%s" % (bad, whybad))
            warn += 1
    print()

print("=" * 74)
print("含「仍未证实」事实的实体（**不得作为出图依据**）")
print("=" * 74)
bad = [f for f, v in facts.items() if v["assumed"]]
if bad:
    print("  假设事实：%s" % "、".join(sorted(bad)))
    for r in con.execute("select name, domain_fact from entity"):
        hit = [f for f in bad if f in (r["domain_fact"] or "")]
        if hit:
            print("    ⚠️ %s 依据 %s" % (r["name"], "、".join(hit)))
else:
    print("  ✅ 没有")

print()
print("  提醒 %d 处" % warn)
con.close()
