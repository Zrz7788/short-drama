# -*- coding: utf-8 -*-
r"""修外键悬挂：删掉镜头后，cost_ledger 里指向它的记录还留着。

## 为什么不能删这些记录

那两条是**真实花掉的钱**（首帧 S001/S003，各 ¥0.30）。删掉账目就假了——
项目成本会少算 ¥0.60，而且"沉没成本"这件事必须留在账上（交接文件里
就靠它说明"外卖员旧版沉没 ¥4.80"）。

## 正确做法

`cost_ledger.shot_id` **可空**。把悬挂的指针置为 NULL：
  · 花费记录保留（钱花了就是花了）
  · 不再指向不存在的镜头（外键完整性恢复）
  · 在 note 里注明原镜头号，信息不丢

## 顺带修个真问题

`tools/_cleanup_v2.py` 删镜头时**没处理 cost_ledger**，这就是悬挂的来源。
本脚本同时把「置空悬挂指针」抽成可复用函数，以后删镜头前后都能调。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"


def null_orphan_cost_refs(con, verbose=True):
    """把 cost_ledger 里指向已删镜头的 shot_id 置空。返回处理条数。

    可在删镜头**之后**调用，用来恢复外键完整性。
    """
    rows = list(con.execute(
        "select id, shot_id, note from cost_ledger "
        "where shot_id is not null "
        "  and not exists (select 1 from shot s where s.id = cost_ledger.shot_id)"))
    for r in rows:
        note = r["note"] or ""
        mark = "（原 shot_id=%s，该镜头已删）" % r["shot_id"]
        if mark not in note:
            note = (note + " " + mark).strip()
        con.execute("update cost_ledger set shot_id=NULL, note=? where id=?",
                    (note, r["id"]))
        if verbose:
            print("  ✓ cost_ledger#%s 指针置空  %s" % (r["id"], note[:66]))
    return len(rows)


def main():
    if not os.path.exists(DB):
        print("找不到 %s" % DB)
        return 1
    bak = "%s.bak-before-fkfix-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(DB, bak)
    print("已备份 -> %s\n" % bak)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    print("=" * 72)
    print("置空悬挂的 cost_ledger 指针")
    print("=" * 72)
    n = null_orphan_cost_refs(con)
    con.commit()
    print("\n  处理 %d 条" % n)

    print("\n" + "=" * 72)
    print("复核：还有没有其它外键悬挂")
    print("=" * 72)
    fks = [("cost_ledger", "shot_id", "shot"),
           ("cost_ledger", "episode_id", "episode"),
           ("shot", "scene_id", "scene"),
           ("shot_entity", "shot_id", "shot"),
           ("shot_entity", "entity_id", "entity"),
           ("entity_ref", "entity_id", "entity"),
           ("image_generation", "entity_id", "entity"),
           ("scene", "script_id", "script"),
           ("scene", "episode_id", "episode")]
    bad = 0
    for tbl, col, ref in fks:
        try:
            rows = list(con.execute(
                "select %s as v, count(*) as n from %s where %s is not null "
                "and not exists (select 1 from %s t where t.id = %s.%s) "
                "group by %s" % (col, tbl, col, ref, tbl, col, col)))
        except sqlite3.Error as e:
            print("  ? %-34s 跳过（%s）" % ("%s.%s" % (tbl, col), str(e)[:36]))
            continue
        for r in rows:
            print("  ✗ %-34s 值=%s 共 %d 条" % ("%s.%s -> %s" % (tbl, col, ref),
                                               r["v"], r["n"]))
            bad += 1
    if not bad:
        print("  ✅ 全部外键完整")

    print("\n" + "=" * 72)
    print("成本账（确认钱没被删掉）")
    print("=" * 72)
    tot = con.execute("select coalesce(sum(amount),0) from cost_ledger").fetchone()[0]
    print("  累计花费 ¥%.2f（改之前也应是这个数）" % tot)
    for r in con.execute("select id, amount, note from cost_ledger "
                         "where shot_id is null order by id desc limit 5"):
        print("    #%s ¥%.2f  %s" % (r["id"], r["amount"], (r["note"] or "")[:60]))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
