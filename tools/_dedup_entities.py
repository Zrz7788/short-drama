# -*- coding: utf-8 -*-
r"""合并第 2 阶段抽出的重复实体。

**为什么会有重复**：模型给同一个东西起了**新名字**，而库里的唯一约束是
`(project_id, kind, name)`，名字不同就不算同一条。

三组：
  主管            ↔ 中年男主管
  金属倒计时牌      ↔ 红色数字倒计时牌   ← 名字有问题，见下
  黄铜签牌         ↔ 黄铜身份签牌

**为什么保留我这边（左侧）的名字**：
  「红色数字倒计时牌」把"红色数字"写进了**实体名**。但取证结论是
  **精确数字不要跨镜显示**（模型做不到逐字一致，会漂移）。名字里带上它，
  等于在实体层面就把这个错误做法固定下来。所以用中性的「金属倒计时牌」。

合并 = 保留一条、把另一条的别名并进去、删掉多余那条。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"
# (保留的名字, [要合并掉的名字])
MERGE = [
    ("主管", ["中年男主管"]),
    ("金属倒计时牌", ["红色数字倒计时牌"]),
    ("黄铜签牌", ["黄铜身份签牌"]),
]

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-dedup-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731


def aliases(name):
    r = c.execute("select alias from entity where name=?", (name,)).fetchone()
    return (r["alias"] or "") if r else ""


print("=" * 72)
print("合并重复实体")
print("=" * 72)
merged = 0
for keep, drops in MERGE:
    row = c.execute("select id, kind, alias from entity where name=?",
                    (keep,)).fetchone()
    if not row:
        print("  ⚠️ 找不到要保留的「%s」，跳过" % keep)
        continue
    all_alias = [x.strip() for x in (row["alias"] or "").split(",") if x.strip()]
    for d in drops:
        dro = c.execute("select id, alias from entity where kind=? and name=?",
                        (row["kind"], d)).fetchone()
        if not dro:
            print("  ⚠️ 「%s」不存在，跳过" % d)
            continue
        # 被删的名字本身要变成别名，否则剧本里那个称呼就断了
        if d not in all_alias:
            all_alias.append(d)
        for x in (dro["alias"] or "").split(","):
            x = x.strip()
            if x and x not in all_alias and x != keep:
                all_alias.append(x)
        # 迁移参考图（若有）
        n_ref = q("select count(*) from entity_ref where entity_id=?", dro["id"])
        if n_ref:
            c.execute("update entity_ref set entity_id=? where entity_id=?",
                      (row["id"], dro["id"]))
        c.execute("delete from image_generation where entity_id=?", (dro["id"],))
        c.execute("delete from entity where id=?", (dro["id"],))
        print("  ✗ 「%s」并入「%s」（迁移 %d 张图记录）" % (d, keep, n_ref))
        merged += 1
    c.execute("update entity set alias=? where id=?",
              (", ".join(all_alias), row["id"]))
    if all_alias:
        print("     「%s」别名现在是: %s" % (keep, ", ".join(all_alias)[:70]))

c.commit()
print("\n  合并掉 %d 个" % merged)

print("\n" + "=" * 72)
print("合并后的实体清单")
print("=" * 72)
IMG = {"character": {"main": 2, "secondary": 1, "background": 0},
       "location": {"main": 3, "secondary": 2, "background": 0},
       "prop": {"main": 1, "secondary": 1, "background": 0}}
rows = list(c.execute("select kind, name, role, domain_fact from entity "
                      "order by kind, role, id"))
tot = 0
by_kind = {}
for r in rows:
    n = IMG[r["kind"]][r["role"]]
    tot += n
    by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print("  [%s/%-9s] %-22s %d 张  %s"
          % (r["kind"], r["role"], r["name"], n, r["domain_fact"] or "(空!)"))
print()
print("  共 %d 个实体（%s）" % (len(rows),
      "  ".join("%s=%d" % kv for kv in sorted(by_kind.items()))))
print("  出图合计 %d 张 ≈ ¥%.1f" % (tot, tot * 0.30))
missing = [r["name"] for r in rows
           if not r["domain_fact"]]
if missing:
    print("\n  ⚠️ 没填领域事实的: %s" % ", ".join(missing))
else:
    print("\n  ✅ 全部实体都有领域事实依据")
c.close()
