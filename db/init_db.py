# -*- coding: utf-8 -*-
"""建立/迁移仿真人短剧生产库。

用法：
    python db/init_db.py            不存在则建，存在则做增量迁移与校验
    python db/init_db.py --rebuild  删库重建（会丢数据）

三段式执行顺序（不能颠倒）：
    1. db/schema.sql  建表，全部 IF NOT EXISTS —— 纯加法
    2. migrate()      给**已存在**的表补新列
    3. db/views.sql   建索引与视图 —— 会引用第 2 步新加的列
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "drama.db")
SCHEMA = os.path.join(ROOT, "db", "schema.sql")
VIEWS = os.path.join(ROOT, "db", "views.sql")

# 本次会话核实过的价目。置信度含义见 knowledge/02-模型与成本/
PRICES = [
    ("doubao-seedance-2-5-260628", "旗舰", "1080p", "second", 1.45,
     "用户提供（火山方舟控制台）", "high"),
    ("doubao-seedance-2-0-260128", "标准", "1080p", "second", 1.00,
     "用户提供（火山方舟控制台）", "high"),
    ("doubao-seedance-2-5-260628", "Draft 样片", "480p", "second", 0.26,
     "由官方省费口径反推：5s/1080P、4抽命中省57%，20抽1省77%", "medium"),
    ("doubao-seedream-4-0-250828", "图片", None, "image", 0.20,
     "估算，需在控制台核对", "low"),
]

# schema 版本。加表改 schema.sql 即可；**加列必须同时登记到 COLUMN_MIGRATIONS**。
SCHEMA_VERSION = 3

# 给已存在的表补列。CREATE TABLE IF NOT EXISTS 对已存在的表完全不动，
# 所以每个新增列都要在这里显式登记，否则老库升不上来。
# 格式：(表, 列, ALTER 语句)
COLUMN_MIGRATIONS = [
    ("shot", "scene_id",
     "ALTER TABLE shot ADD COLUMN scene_id INTEGER REFERENCES scene(id)"),
    # 视频衔接所需：抽帧路径与续接关系
    ("shot", "first_frame_path",
     "ALTER TABLE shot ADD COLUMN first_frame_path TEXT"),
    ("shot", "last_frame_path",
     "ALTER TABLE shot ADD COLUMN last_frame_path TEXT"),
    ("shot", "continues_from",
     "ALTER TABLE shot ADD COLUMN continues_from INTEGER"),
]

# 目标要求覆盖的数据域 -> 承载它们的表
DOMAINS = {
    "剧本":     ["script", "scene"],
    "集":       ["episode"],
    "镜头":     ["shot", "shot_entity"],
    "素材":     ["asset"],
    "生成任务": ["generation", "image_generation"],
    "成本":     ["cost_ledger", "price_book"],
    "实体":     ["entity", "entity_ref"],
}


def migrate(con):
    """幂等地把已知的列变更补上，返回本次实际应用的列表。"""
    applied = []
    for table, column, ddl in COLUMN_MIGRATIONS:
        cols = {r[1] for r in con.execute('PRAGMA table_info("%s")' % table)}
        if not cols:
            continue          # 表不存在：全新库，schema.sql 已是目标状态
        if column not in cols:
            con.execute(ddl)
            applied.append("%s.%s" % (table, column))
    if applied:
        con.commit()
    return applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="删库重建")
    args = ap.parse_args()

    os.makedirs(DB_DIR, exist_ok=True)

    if args.rebuild and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("已删除旧库:", DB_PATH)

    fresh = not os.path.exists(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")

    # 第 1 段：建表
    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())

    # 第 2 段：补列
    applied = migrate(con)
    if applied:
        print("已迁移列:", ", ".join(applied))

    # 第 3 段：索引与视图（依赖第 2 段）
    with open(VIEWS, encoding="utf-8") as f:
        con.executescript(f.read())

    con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),))
    con.commit()

    if con.execute("SELECT COUNT(*) FROM price_book").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO price_book "
            "(model_id, variant, resolution, unit, unit_price, source, confidence) "
            "VALUES (?,?,?,?,?,?,?)",
            PRICES,
        )
        con.commit()
        print("已写入 %d 条价目" % len(PRICES))

    print("\n库文件 :", DB_PATH, "(%.1f KB)" % (os.path.getsize(DB_PATH) / 1024.0))
    print("新建   :", "是" if fresh else "否（已存在，做增量迁移）")

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    views = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_%' ORDER BY name")]

    print("\n表 (%d)  : %s" % (len(tables), ", ".join(tables)))
    print("视图 (%d): %s" % (len(views), ", ".join(views)))
    print("索引 (%d): %s" % (len(idx), ", ".join(idx)))

    print("\n--- 价目表 v_price_book ---")
    for row in con.execute("SELECT * FROM v_price_book"):
        print("  %-28s %-10s %-6s %-7s %8.2f %-4s %-7s %s" % row)

    print("\n--- 数据域覆盖检查 ---")
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = 0
    for domain, tabs in DOMAINS.items():
        hit = [t for t in tabs if t in have]
        gone = [t for t in tabs if t not in have]
        missing += len(gone)
        print("  %-10s %-22s%s" % (domain, ", ".join(hit) or "(无)",
                                   "  缺: " + ", ".join(gone) if gone else ""))
    print("  schema 版本:", con.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])

    bad = list(con.execute("PRAGMA foreign_key_check"))
    print("\n外键自检:", "通过" if not bad else "**异常** %s" % bad)
    print("数据域缺失项:", missing)

    con.close()
    return 0 if missing == 0 and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
