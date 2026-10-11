# -*- coding: utf-8 -*-
"""第 1 集切换：导入新剧本 + 作废外卖员版的下游产物。

一次性脚本（不是管线阶段）。为什么需要它：`--from-md` 只会写 script/scene，
而旧的第 1 集已经在库里有 9 场、20 镜、13 个实体、16 张参考图。那批实体的
人物、地点、道具（林野／假林野／302 室／外卖面单…）新剧本一个都不用，
留在库里会让第 2 阶段抽实体、第 5 阶段拆镜都读到外卖员那一版。

**先备份**：data/drama.db 被 gitignore，没有回滚。
运行前请确认同目录有 drama.db.bak-* 备份。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

DB = "data/drama.db"
EPISODE_ID = 1
NEW_LOGLINE = ("殡仪馆遗体整容师沈九给一具遗体上唇色时，遗体睁开眼叫她别签字——"
               "死者是十八年前把她父亲送进火化炉的人，而死者的名字，写在她父亲的死亡确认书上。")
PROJECT_NAME = "殡仪馆的阿九"

if not [f for f in os.listdir(os.path.dirname(DB)) if f.startswith("drama.db.bak-")]:
    print("!! data/ 下没有 drama.db.bak-* 备份。先备份再跑这个脚本。")
    sys.exit(2)

con = C.connect(DB)
con.execute("PRAGMA foreign_keys = ON")
q = lambda s, *a: con.execute(s, a).fetchone()[0]  # noqa: E731

print("=== 作废前的库状态 ===")
print("  script %d 版 / scene %d 行 / shot %d 行 / entity %d 个 / entity_ref %d 张"
      % (q("SELECT COUNT(*) FROM script"),
         q("SELECT COUNT(*) FROM scene"),
         q("SELECT COUNT(*) FROM shot"),
         q("SELECT COUNT(*) FROM entity"),
         q("SELECT COUNT(*) FROM entity_ref")))

# ---- 1. 删掉旧剧本（scene 随 script 级联删除）----
old_scripts = [r["id"] for r in con.execute(
    "SELECT id FROM script WHERE episode_id=?", (EPISODE_ID,))]
print("\n=== 1. 删除旧剧本 ===")
print("  旧 script id: %s" % old_scripts)
con.execute("DELETE FROM scene WHERE script_id IN (SELECT id FROM script WHERE episode_id=?)",
            (EPISODE_ID,))
con.execute("DELETE FROM script WHERE episode_id=?", (EPISODE_ID,))
print("  剩余 script %d 行 / scene %d 行"
      % (q("SELECT COUNT(*) FROM script"), q("SELECT COUNT(*) FROM scene")))

# ---- 2. 删除旧分镜（shot_entity / generation 级联）----
print("\n=== 2. 删除旧分镜（20 镜）===")
n_shot = q("SELECT COUNT(*) FROM shot WHERE episode_id=?", EPISODE_ID)
n_gen = q("SELECT COUNT(*) FROM generation g JOIN shot s ON s.id=g.shot_id "
          "WHERE s.episode_id=?", EPISODE_ID)
con.execute("DELETE FROM shot_entity WHERE shot_id IN "
            "(SELECT id FROM shot WHERE episode_id=?)", (EPISODE_ID,))
con.execute("DELETE FROM shot WHERE episode_id=?", (EPISODE_ID,))
print("  删除 %d 个镜头、%d 条生成记录；剩余 shot %d 行"
      % (n_shot, n_gen, q("SELECT COUNT(*) FROM shot")))

# ---- 3. 作废旧实体与参考图（外卖员版全不可复用）----
print("\n=== 3. 作废旧实体与参考图 ===")
rows = [dict(r) for r in con.execute(
    "SELECT e.id, e.kind, e.name, er.path FROM entity e "
    "LEFT JOIN entity_ref er ON er.entity_id = e.id "
    "WHERE e.project_id = (SELECT project_id FROM episode WHERE id=?)", (EPISODE_ID,))]
paths = sorted({r["path"] for r in rows if r["path"]})
names = sorted({(r["kind"], r["name"]) for r in rows})
for k, n in names:
    print("  - [%s] %s" % (k, n))
con.execute("DELETE FROM entity_ref WHERE entity_id IN "
            "(SELECT id FROM entity WHERE project_id="
            "(SELECT project_id FROM episode WHERE id=?))", (EPISODE_ID,))
con.execute("DELETE FROM entity WHERE project_id="
            "(SELECT project_id FROM episode WHERE id=?)", (EPISODE_ID,))
print("  剩余 entity %d 个 / entity_ref %d 张"
      % (q("SELECT COUNT(*) FROM entity"), q("SELECT COUNT(*) FROM entity_ref")))

# ---- 4. 项目元信息改成新剧本的事实 ----
print("\n=== 4. 更新 project ===")
con.execute("UPDATE project SET name=?, logline=? WHERE id="
            "(SELECT project_id FROM episode WHERE id=?)",
            (PROJECT_NAME, NEW_LOGLINE, EPISODE_ID))
print("  name=%s" % PROJECT_NAME)

con.commit()

# ---- 5. 删磁盘上的 16 张图（已作废），但留一份清单 ----
print("\n=== 5. 磁盘参考图 ===")
print("  以下 %d 个文件属于已作废资产，**未删除**（要删请手工确认）：" % len(paths))
for p in paths:
    print("    %s  %s" % ("存在" if os.path.exists(p) else "缺失", p))
con.close()
print("\n完成。接着跑：python pipeline\\stage_script.py --episode-id 1 "
      "--from-md 剧集\\EP001\\剧本.md")
