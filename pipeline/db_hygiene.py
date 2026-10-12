# -*- coding: utf-8 -*-
r"""数据库卫生：删除父行前清理子行的悬挂引用。

## 为什么需要这个模块

实测踩过：`tools/_cleanup_v2.py` 删掉 12 个旧镜头时，**没管 `cost_ledger`
里指向它们的 2 条花费记录**，于是验收的「外键完整性」项失败：

    [数据库] 外键完整性 [('cost_ledger', 28, 'shot', 0), ('cost_ledger', 29, 'shot', 0)]

那两条是**真实花掉的钱**（首帧 S001/S003 各 ¥0.30），**不能删账**——
删了项目成本就少算，而"沉没成本"必须留在账上。

`cost_ledger.shot_id` 可空，所以正确处理是**把指针置空**并注明原镜头号：
花费保留、外键恢复、信息不丢。

## 用法

删父行**之后**调用（此时悬挂已经产生，正好能查出来）：

    from pipeline import db_hygiene as H
    con.execute("delete from shot where id=?", (sid,))
    H.null_orphan_cost_refs(con)        # 顺手清掉指向已删镜头的花费指针

**只有 cost_ledger 需要这样处理**：它是唯一「记录历史事实、指向的东西却会
被删掉」的表。其他子表（shot_entity / entity_ref / image_generation）
删父行时子行本身就作废，直接删更对。
"""
import sqlite3

# 被 cost_ledger 引用、且会被删除的父表
COST_PARENTS = {
    "shot": "shot_id",
    "episode": "episode_id",
    "entity": None,          # cost_ledger 目前不引用 entity
}


def null_orphan_cost_refs(con, verbose=False):
    r"""把 cost_ledger 里指向已不存在的父行的 id 置空，并注明原值。

    返回处理的条数。**保留 amount**——这是账，不是缓存。
    """
    total = 0
    for parent, col in COST_PARENTS.items():
        if not col:
            continue
        try:
            rows = list(con.execute(
                "SELECT id, %s AS ref, note FROM cost_ledger "
                "WHERE %s IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM %s p WHERE p.id = cost_ledger.%s)"
                % (col, col, parent, col)))
        except sqlite3.Error:
            # 表或列不存在（老库/裁剪库）——跳过，不阻断
            continue
        for r in rows:
            mark = "（原 %s=%s，该%s已删）" % (col, r["ref"], parent)
            note = (r["note"] or "")
            if mark not in note:
                note = (note + " " + mark).strip()
            con.execute("UPDATE cost_ledger SET %s=NULL, note=? WHERE id=?"
                        % col, (note, r["id"]))
            if verbose:
                print("    cost_ledger#%s %s 置空 -> %s"
                      % (r["id"], col, note[:60]))
            total += 1
    return total


def purge_stale_scripts(con, episode_id, verbose=False):
    r"""删掉某集除现行稿以外的剧本，**连同它们的场次**。返回 (剧本数, 场次数)。

    ## 为什么需要

    踩过两次「删数据没删干净」，根因相同：**删父行时没清依赖它的行**。

    1. 删旧镜头时没清 `cost_ledger` → 外键悬挂（见 `null_orphan_cost_refs`）
    2. 删旧剧本时漏了 `scene` → 库里**两版场次并存**，
       `select sum(duration_s) from scene` 得 160 秒（正确 80）。
       任何**按 episode 聚合、没按 script_id 过滤**的代码都会算错。

    因此凡是要"换掉剧本"，就用这个函数，别只改 `is_current`。

    ⚠️ 不删 `cost_ledger`——账要留；只删可重新生成的剧本与场次。
    """
    old = list(con.execute(
        "SELECT id, version FROM script WHERE episode_id = ? "
        "AND (is_current IS NULL OR is_current = 0)", (episode_id,)))
    n_scene = 0
    for r in old:
        # 先清场次上的引用（镜头若还在，它会指向将被删的场次）
        try:
            con.execute(
                "UPDATE shot SET scene_id = NULL WHERE scene_id IN "
                "(SELECT id FROM scene WHERE script_id = ?)", (r["id"],))
        except sqlite3.Error:
            pass
        cur = con.execute("DELETE FROM scene WHERE script_id = ?", (r["id"],))
        n_scene += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.execute("DELETE FROM script WHERE id = ?", (r["id"],))
        if verbose:
            print("    删 v%s（script_id=%s）" % (r["version"], r["id"]))
    # 顺手清悬挂的花费指针
    null_orphan_cost_refs(con, verbose=verbose)
    return len(old), n_scene


def find_orphans(con):
    r"""列出全部外键悬挂（体检用）。返回 [(表.列 -> 父表, 值, 条数)]。"""
    fks = [
        ("cost_ledger", "shot_id", "shot"),
        ("cost_ledger", "episode_id", "episode"),
        ("shot", "scene_id", "scene"),
        ("shot_entity", "shot_id", "shot"),
        ("shot_entity", "entity_id", "entity"),
        ("entity_ref", "entity_id", "entity"),
        ("image_generation", "entity_id", "entity"),
        ("scene", "script_id", "script"),
        ("dialogue", "scene_id", "scene"),
    ]
    out = []
    for tbl, col, ref in fks:
        try:
            rows = list(con.execute(
                "SELECT %s AS v, COUNT(*) AS n FROM %s "
                "WHERE %s IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM %s t WHERE t.id = %s.%s) GROUP BY %s"
                % (col, tbl, col, ref, tbl, col, col)))
        except sqlite3.Error:
            continue
        for r in rows:
            out.append(("%s.%s -> %s" % (tbl, col, ref), r["v"], r["n"]))
    return out
