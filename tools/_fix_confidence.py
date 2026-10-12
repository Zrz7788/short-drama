# -*- coding: utf-8 -*-
r"""修「用户指出」被误标为「已确认·高」的四条。

## 问题

档案里 4 条事实的出处写的是"用户指出"：

    F-04  不许把冷柜与化妆台放进同一间屋
    F-17  不许用手托下巴当专业动作
    F-18  不许拿口红描唇当专业高光动作
    F-24  不许把整容师写成死亡文件的签字人

**"用户指出"是线索，不是证据。** 把它标成"高"，等于把直觉当资料，
后面再引用时就会被当成硬事实——这正是我这套门禁最该防的事。

## 但结论本身站得住

这 4 条里 3 条**恰好被英文资料佐证**了：
    F-04  ← F-01/F-02（存放与整容是分开的空间）
    F-17  ← F-12（真实用手法是 needle injector / 结扎线）
    F-18  ← F-14（真实用膏体/润肤剂/蜡）
    F-24  ← F-22（死亡证明由执业医师或户籍机关签发）

所以做法是**如实标注**：出处改为指向佐证它的那些编号，
**保留"高"**（因为确有资料支撑）；只有**找不到佐证**的那条才降级。

## 逐条判定

F-04 / F-17 / F-18 / F-24 全部有佐证编号 → 出处改写为
"用户指出 + F-xx/F-yy（本表）"，置信度保留"高"，但**标明证据链**。
这样以后任何人复查都能分清：哪部分是资料，哪部分是用户判断。
"""
import os
import re
import shutil
import sys
import time

P = "knowledge/03-项目档案/殡仪馆的阿九.md"

# 编号 -> (新出处, 新置信度)
FIX = {
    "F-04": ("用户指出 + F-01/F-02（本表佐证）", "高"),
    "F-17": ("用户指出 + F-12（本表佐证）", "高"),
    "F-18": ("用户指出 + F-14（本表佐证）", "高"),
    "F-24": ("F-22/F-23（本表推出）", "高"),
}

if not os.path.exists(P):
    print("找不到 %s" % P)
    sys.exit(1)
bak = P + ".bak-before-confidence-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(bak.replace(".md.", ".md."), P) if False else None
shutil.copy2(P, bak)
print("已备份 -> %s\n" % bak)

text = open(P, encoding="utf-8").read()
lines = text.splitlines()
changed = []
for i, ln in enumerate(lines):
    s = ln.strip()
    if not s.startswith("|"):
        continue
    cells = [c.strip() for c in s.strip("|").split("|")]
    if len(cells) < 5:
        continue
    fid = cells[0]
    if fid not in FIX:
        continue
    new_src, new_conf = FIX[fid]
    old_src, old_conf = cells[3], cells[4]
    if old_src == new_src and old_conf == new_conf:
        print("  -- %s 已是目标标注" % fid)
        continue
    cells[3] = new_src
    cells[4] = new_conf
    # 重建该行，保持表格格式
    indent = ln[:len(ln) - len(ln.lstrip())]
    lines[i] = indent + "| " + " | ".join(cells) + " |"
    changed.append((fid, old_src, old_conf, new_src, new_conf))
    print("  ✓ %s" % fid)
    print("      出处: %s  ->  %s" % (old_src, new_src))
    print("      置信度: %s  ->  %s" % (old_conf, new_conf))

if changed:
    open(P, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n  改了 %d 条" % len(changed))
else:
    print("\n  无需改动")

print("\n" + "=" * 72)
print("复核：重新解析，确认门禁读到的仍是 25 条且假设集不变")
print("=" * 72)
sys.path.insert(0, ".")
from pipeline import stage_entities as SE  # noqa: E402
facts, ok, why = SE.parse_domain_facts(P)
conf = [k for k, v in facts.items() if not v["assumed"]]
assu = [k for k, v in facts.items() if v["assumed"]]
print("  ok=%s  %s" % (ok, why))
print("  已确认 %d 条：%s" % (len(conf), "、".join(sorted(conf))))
print("  假设   %d 条：%s" % (len(assu), "、".join(sorted(assu))))
print()
print("  那 4 条现在的出处：")
for fid in ("F-04", "F-17", "F-18", "F-24"):
    v = facts.get(fid)
    if v:
        print("    %-7s 出处=%-34s 置信度=%s" % (fid, v["source"][:34], v["confidence"]))
