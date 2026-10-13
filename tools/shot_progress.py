# -*- coding: utf-8 -*-
r"""出图进度盘点：哪些视角已经出了、还差哪些。

## 为什么重写

前两版都算错了：
  · 一版拿 `entity_ref.path`（存的是 `.jpg`）去比，却报出 `.png` 的样子，
    于是"全部缺失"——**它根本没查对字段**；
  · 另一版把集合推导写错，导致**已出图的角色也被列成"0/2"**。

这类盘点脚本**必须与"需要几个视角"的规则对齐**，而规则在
`pipeline/stage_refs.py` 里（`CHARACTER_VIEWS` / `SCENE_VIEWS` / `PROP_VIEW`）。
本脚本直接从那里导入，**不另抄一份**——抄就会分叉。
"""
import importlib.util
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SR = load(os.path.join(ROOT, "pipeline", "stage_refs.py"), "stage_refs")

# 直接用管线自己的规则，不另写一份
WANT = {
    "character": {t: [v[0] for v in vs] for t, vs in SR.CHARACTER_VIEWS.items()},
    "location": {t: [v[0] for v in vs] for t, vs in SR.SCENE_VIEWS.items()},
    "prop": {"main": [SR.PROP_VIEW[0]], "secondary": [SR.PROP_VIEW[0]],
             "background": []},
}
PHASE = {"character": "characters", "location": "scenes", "prop": "props"}

con = sqlite3.connect(os.path.join(ROOT, "data", "drama.db"))
con.row_factory = sqlite3.Row
have = {(r["entity_id"], r["view"]): r["path"]
        for r in con.execute("SELECT entity_id, view, path FROM entity_ref")}

print("=" * 74)
print("每个实体的出图进度")
print("=" * 74)
todo = {}
missing_file = []
for e in con.execute("SELECT id, kind, name, role FROM entity "
                     "ORDER BY kind, role DESC, name"):
    want = WANT.get(e["kind"], {}).get(e["role"], [])
    if not want:
        continue
    got = [v for v in want if (e["id"], v) in have]
    mark = "✅" if len(got) == len(want) else "⬜"
    print("  %s [%-9s/%-9s] %-16s %d/%d  %s"
          % (mark, e["kind"], e["role"], e["name"], len(got), len(want),
             "、".join(v for v in want if v not in got) or ""))
    if len(got) < len(want):
        todo.setdefault(PHASE[e["kind"]], []).append(e["name"])
    # 已记录但文件不在
    for v in got:
        p = have[(e["id"], v)]
        if not os.path.exists(p):
            # 容忍扩展名差异（管线写 .png，下载可能落 .jpg）
            alt = os.path.splitext(p)[0]
            found = [alt + x for x in (".png", ".jpg", ".jpeg")
                     if os.path.exists(alt + x)]
            if not found:
                missing_file.append((e["name"], v, p))

print()
print("=" * 74)
print("还差什么")
print("=" * 74)
total = 0
PRICE = {"character": 2, "location": 3, "prop": 1}
for ph in ("characters", "scenes", "props"):
    names = todo.get(ph, [])
    if not names:
        print("  %-11s ✅ 已完成" % ph)
        continue
    # 估算张数
    n = 0
    for name in names:
        r = con.execute("SELECT kind, role FROM entity WHERE name=?",
                        (name,)).fetchone()
        want = WANT[r["kind"]].get(r["role"], [])
        got = sum(1 for v in want
                  if (con.execute("SELECT id FROM entity WHERE name=?",
                                  (name,)).fetchone()["id"], v) in have)
        n += len(want) - got
    total += n
    print("  %-11s %2d 张  %s" % (ph, n, "、".join(names)))

print()
print("  合计 **%d 张 ≈ ¥%.1f**" % (total, total * 0.30))
print("  累计已花 ¥%.2f" % con.execute(
    "SELECT coalesce(sum(amount),0) FROM cost_ledger").fetchone()[0])

if missing_file:
    print()
    print("  ⚠️ 库里有记录但磁盘找不到：")
    for name, v, p in missing_file:
        print("     %s %s -> %s" % (name, v, p))
else:
    print("  ✅ 已记录的参考图，文件全部在位")
con.close()
