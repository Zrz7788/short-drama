# -*- coding: utf-8 -*-
r"""清理废弃剧本及其场次（第二次"删数据没删干净"）。

## 问题

`script` 表里 v1 仍留着（`is_current=0`），而 `scene` 表里**两版的场次都在**：

    id=1  script_id=1(废弃v1)  殡仪馆三号化妆间  48.3 秒
    id=2  script_id=1(废弃v1)  殡仪馆后廊       31.7 秒
    id=3  script_id=2(现行v2)  殡仪馆整容室      47.9 秒
    id=4  script_id=2(现行v2)  殡仪馆后廊       32.1 秒

于是 `select sum(duration_s) from scene` = **160 秒**（正确应是 80）。
任何**按 episode 聚合、没按 script_id 过滤**的代码都会算错——
`tools/lint_cross_doc.py` 里我自己就踩了这个坑（差点报出
"分镜 74 秒 vs 目标 160 秒 差 86 秒"这种荒谬结论）。

## 这已经是第二次

第一次是删镜头没清 `cost_ledger`（外键悬挂，见 `pipeline/db_hygiene.py`）。
两次根因相同：**删父行时没清干净依赖它的行**。

所以本脚本把「清除某集除现行稿以外的全部剧本与场次」抽成可复用流程，
并在 `pipeline/db_hygiene.py` 里补上对应的通用函数。

⚠️ **不删** `cost_ledger`（那是账），只删剧本与场次这类**可重新生成**的产物。
"""
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import db_hygiene as H  # noqa: E402

DB = "data/drama.db"
EP = 1

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-scene-purge-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=" * 72)
print("清除废弃剧本与场次（保留现行稿）")
print("=" * 72)
old = list(con.execute(
    "select id, version, is_current from script "
    "where episode_id=? and (is_current is null or is_current=0)", (EP,)))
if not old:
    print("  没有废弃剧本，无需清理")
for r in old:
    n = con.execute("select count(*) from scene where script_id=?",
                    (r["id"],)).fetchone()[0]
    # 先看这些场次有没有被别的东西引用
    n_shot = 0
    try:
        n_shot = con.execute(
            "select count(*) from shot where scene_id in "
            "(select id from scene where script_id=?)", (r["id"],)).fetchone()[0]
    except sqlite3.Error:
        pass
    con.execute("delete from scene where script_id=?", (r["id"],))
    con.execute("delete from script where id=?", (r["id"],))
    print("  ✗ 删除 v%s（script_id=%s）：%d 个场次、关联 %d 个镜头"
          % (r["version"], r["id"], n, n_shot))

# 通用卫生：任何指向已删行的花费指针
n_fk = H.null_orphan_cost_refs(con, verbose=True)
if n_fk:
    print("  清掉 %d 条悬挂花费指针" % n_fk)
con.commit()

print("\n" + "=" * 72)
print("复核")
print("=" * 72)
print("  script 表剩余：")
for r in con.execute("select id,version,is_current from script order by id"):
    print("    id=%s v%s current=%s" % (r["id"], r["version"], r["is_current"]))
print("  scene 表剩余：")
for r in con.execute("select id,script_id,seq,slug,duration_s from scene order by id"):
    print("    id=%s script_id=%s 第%d场 %-28s %.1f 秒"
          % (r["id"], r["script_id"], r["seq"], r["slug"], r["duration_s"]))
cur = con.execute("select id from script where is_current=1").fetchone()["id"]
print()
print("  现行稿合计        : %.1f 秒" % con.execute(
    "select sum(duration_s) from scene where script_id=?", (cur,)).fetchone()[0])
print("  **全表**合计      : %.1f 秒  ← 两者现在应相等"
      % con.execute("select sum(duration_s) from scene").fetchone()[0])
print("  累计花费          : ¥%.2f" % con.execute(
    "select coalesce(sum(amount),0) from cost_ledger").fetchone()[0])
orph = H.find_orphans(con)
print("  外键悬挂          : %s" % ("无 ✅" if not orph else orph))
con.close()
