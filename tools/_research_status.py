# -*- coding: utf-8 -*-
r"""核对取证成果有没有真正落地。

用户问："那个事实的问题解决了没？"
**不凭记忆答**——逐项核对：问题答了没有、事实有没有出处、有没有落进
剧本与视觉设计、还有哪些是假设。
"""
import re
import sys

sys.path.insert(0, ".")
from pipeline import stage_entities as SE  # noqa: E402

P = "knowledge/03-项目档案/殡仪馆的阿九.md"
t = open(P, encoding="utf-8").read()
print("档案 %s（%d 字）\n" % (P, len(t)))

# ---------- 领域问题 ----------
m = re.search(r"^##\s*领域问题\s*$(.*?)(?=^##\s|\Z)", t, re.M | re.S)
print("=" * 72)
print("① 领域问题（取证清单）")
print("=" * 72)
if m:
    qs = [l.strip() for l in m.group(1).splitlines()
          if l.strip().startswith("| Q-")]
    ans = 0
    for l in qs:
        cells = [x.strip() for x in l.strip("|").split("|")]
        qid = cells[0]
        concl = " ".join(cells[2:4])
        ok = bool(concl.strip()) and "待查" not in concl
        ans += 1 if ok else 0
        mark = "已答" if ok else "**待查**"
        print("  %-8s %s" % (qid, mark))
    print("\n  %d 条，已答 %d 条" % (len(qs), ans))
else:
    print("  找不到「领域问题」章节")

# ---------- 领域事实（用管线自己的解析器，确保与门禁一致）----------
print()
print("=" * 72)
print("② 领域事实（门禁实际读到的）")
print("=" * 72)
facts = SE.parse_domain_facts(P)
print("  解析到 %d 条" % len(facts))
conf = [k for k, v in facts.items() if not v["assumed"]]
assu = [k for k, v in facts.items() if v["assumed"]]
print("  已确认 %d 条，假设·待验证 %d 条" % (len(conf), len(assu)))
print()
print("  已确认：")
for k in sorted(conf):
    print("    %-7s %s" % (k, facts[k]["sentence"][:56]))
print()
print("  假设·待验证（**门禁会拦**）：")
for k in sorted(assu):
    print("    %-7s %s" % (k, facts[k]["sentence"][:56]))

# ---------- 出处来源 ----------
print()
print("=" * 72)
print("③ 出处来源（有没有可靠依据）")
print("=" * 72)
srcs = re.findall(r"(?:出处|来源)[^\n|]*?[：:]\s*([^\n|]{0,70})", t)
seen = {}
for s in srcs:
    s = s.strip()
    if s:
        seen[s] = seen.get(s, 0) + 1
for s, n in sorted(seen.items(), key=lambda x: -x[1])[:15]:
    print("  %-64s ×%d" % (s[:64], n))
if not seen:
    print("  （未在表格里抽到出处字段）")

# ---------- 落地情况 ----------
print()
print("=" * 72)
print("④ 取证结论有没有落进剧本与视觉设计")
print("=" * 72)
checks = [
    ("F-13 口部塑形器", "塑形器", "剧集/EP001/剧本.md"),
    ("F-12 颌部金属针", "金属针", "剧集/EP001/剧本.md"),
    ("F-14 油彩/唇油", "油彩", "剧集/EP001/剧本.md"),
    ("F-16 整容台", "整容台", "剧集/EP001/剧本.md"),
    ("F-20 脚趾牌", "大脚趾", "剧集/EP001/剧本.md"),
    ("F-22 签字权", "没有资格签", "剧集/EP001/剧本.md"),
    ("F-21 炉温未达标不开门", "温度读数", "剧集/EP001/剧本.md"),
    ("F-01 空间分离", "整容室", "剧集/EP001/剧本.md"),
]
for label, needle, path in checks:
    try:
        body = open(path, encoding="utf-8").read()
        hit = needle in body
    except OSError:
        hit = False
    print("  %-28s %s  在 %s" % (label, "✅" if hit else "❌ 未落地", path))

vis = open("剧集/EP001/视觉设定.md", encoding="utf-8").read()
vchecks = [("塑形器", "塑形器"), ("脚牌", "脚"), ("金属针", "金属针"),
           ("焚化炉", "焚化炉"), ("无影灯/整容台", "整容台")]
print()
for label, needle in vchecks:
    print("  %-28s %s  在 视觉设定.md" % (label, "✅" if needle in vis else "❌ 未落地"))
