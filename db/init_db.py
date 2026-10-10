# -*- coding: utf-8 -*-
"""建立/重建仿真人短剧生产库。

用法： python db/init_db.py            # 不存在则建，存在则校验
       python db/init_db.py --rebuild  # 删库重建（会丢数据）
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "drama.db")
SCHEMA = os.path.join(ROOT, "db", "schema.sql")

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
    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())

    cur = con.execute("SELECT COUNT(*) FROM price_book")
    if cur.fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO price_book "
            "(model_id, variant, resolution, unit, unit_price, source, confidence) "
            "VALUES (?,?,?,?,?,?,?)",
            PRICES,
        )
        con.commit()
        print("已写入 %d 条价目" % len(PRICES))

    print("\n库文件 :", DB_PATH, "(%.1f KB)" % (os.path.getsize(DB_PATH) / 1024.0))
    print("新建   :", "是" if fresh else "否（已存在，仅校验）")

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    views = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name")]

    print("\n表 (%d)  : %s" % (len(tables), ", ".join(tables)))
    print("视图 (%d): %s" % (len(views), ", ".join(views)))
    print("索引 (%d): %s" % (len(idx), ", ".join(idx)))

    print("\n--- 价目表 v_price_book ---")
    for row in con.execute("SELECT * FROM v_price_book"):
        print("  %-28s %-10s %-6s %-7s %8.2f %-4s %-7s %s" % row)

    # 引用完整性自检
    bad = list(con.execute("PRAGMA foreign_key_check"))
    print("\n外键自检:", "通过" if not bad else "**异常** %s" % bad)

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
