# -*- coding: utf-8 -*-
"""把数据库导出为纯文本 SQL，便于进 git / 做备份。

用法： python db/export.py [输出路径]
默认输出到 data/export/drama.sql
"""
import datetime
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "drama.db")
DEFAULT_OUT = os.path.join(ROOT, "data", "export", "drama.sql")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    if not os.path.exists(DB_PATH):
        print("数据库不存在，先跑 python db/init_db.py")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("-- 仿真人短剧生产库导出\n")
        f.write("-- 导出时间: %s\n" % datetime.datetime.now().isoformat(timespec="seconds"))
        f.write("-- 重建: python db/init_db.py --rebuild 后执行本文件\n\n")
        for line in con.iterdump():
            f.write(line + "\n")
    con.close()

    print("已导出:", out, "(%.1f KB)" % (os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
