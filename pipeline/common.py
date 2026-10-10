# -*- coding: utf-8 -*-
"""管线脚本的共用层：路径、数据库连接、ffmpeg 调用。

所有阶段脚本都从这里取 ffmpeg 路径与数据库连接，避免各写一份。
数据库路径可用 --db 覆盖（自检用它指向临时库，不碰生产数据）。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(ROOT, ".tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE = os.path.join(ROOT, ".tools", "ffmpeg", "bin", "ffprobe.exe")
DEFAULT_DB = os.path.join(ROOT, "data", "drama.db")
ASSETS = os.path.join(ROOT, "assets")


def add_common_args(parser):
    parser.add_argument("--db", default=os.environ.get("DSH_DRAMA_DB", DEFAULT_DB),
                        help="数据库路径（默认 data/drama.db）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    return parser


def require_ffmpeg():
    missing = [p for p in (FFMPEG, FFPROBE) if not os.path.exists(p)]
    if missing:
        print("缺少 ffmpeg 组件：")
        for m in missing:
            print("  ", m)
        print("先运行： python tools/setup_ffmpeg.py")
        sys.exit(2)


def connect(db_path):
    if not os.path.exists(db_path):
        print("数据库不存在:", db_path)
        print("先运行： python db/init_db.py")
        sys.exit(2)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def run_ffmpeg(args, cwd=None, timeout=1800):
    return subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, timeout=timeout)


def probe(path, entries, cwd=None):
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", entries,
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    return (p.stdout or "").strip()


def duration(path, cwd=None):
    try:
        return float(probe(path, "format=duration", cwd=cwd))
    except ValueError:
        return -1.0


def video_signature(path, cwd=None):
    """返回 (宽, 高, 编码, 像素格式) 元组，用于判断能否 -c copy 直接拼接。

    ⚠️ 必须用 -of json 按字段名取，不要用 csv=p=0 按位置切。
    ffprobe 的 CSV 输出遵循**它自己的内部字段顺序**（codec_name,width,height,pix_fmt），
    不遵循 -show_entries 里的书写顺序。按位置切会把编码名当成宽度，
    生成 scale=h264:320 这类非法滤镜——实测踩过，表现为
    "Error opening output files: Invalid argument"，且很难看出因果。
    """
    p = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,codec_name,pix_fmt",
                        "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    try:
        st = json.loads(p.stdout or "")["streams"][0]
    except (ValueError, KeyError, IndexError):
        return None
    return (st.get("width"), st.get("height"),
            st.get("codec_name"), st.get("pix_fmt"))


def episode_shots(con, episode_id):
    """按镜号返回镜头及其选定成片。

    选定规则：优先 shot.selected_generation_id；
    没指定时退回该镜头第一条 succeeded 的记录。
    """
    rows = con.execute(
        """
        SELECT s.id            AS shot_id,
               s.seq           AS shot_seq,
               s.duration_s    AS shot_seconds,
               s.status        AS shot_status,
               s.first_frame_path, s.last_frame_path, s.continues_from,
               s.selected_generation_id,
               g.id            AS gen_id,
               g.local_path    AS video_path,
               g.status        AS gen_status
        FROM shot s
        LEFT JOIN generation g
               ON g.id = COALESCE(s.selected_generation_id,
                                  (SELECT id FROM generation
                                    WHERE shot_id = s.id AND status = 'succeeded'
                                    ORDER BY attempt_no LIMIT 1))
        WHERE s.episode_id = ?
        ORDER BY s.seq
        """, (episode_id,)).fetchall()
    return [dict(r) for r in rows]


def episode_info(con, episode_id):
    r = con.execute(
        "SELECT e.id, e.seq, e.title, p.name AS project, p.id AS project_id "
        "FROM episode e JOIN project p ON p.id = e.project_id WHERE e.id = ?",
        (episode_id,)).fetchone()
    return dict(r) if r else None


def assets_root():
    """素材根目录。可用 DSH_ASSETS_ROOT 覆盖——自检借此写到临时目录，
    不污染真实的 assets/。"""
    return os.environ.get("DSH_ASSETS_ROOT") or ASSETS


def frame_paths(episode_seq, shot_seq):
    """抽帧产物路径。返回相对仓库根的路径，便于存库与迁移。"""
    d = os.path.join(assets_root(), "frames")
    first = os.path.join(d, "E%02dS%03d_first.png" % (episode_seq, shot_seq))
    last = os.path.join(d, "E%02dS%03d_last.png" % (episode_seq, shot_seq))
    return (os.path.relpath(first, ROOT).replace("\\", "/"),
            os.path.relpath(last, ROOT).replace("\\", "/"))


def abs_path(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def tail(text, limit=70):
    """取最后一行非空输出，用作错误摘要。

    不要直接写 (text or '').splitlines()[-1]——当输出为空串时
    splitlines() 返回 []，会抛 IndexError 把真正的失败原因盖掉。实测踩过。
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return lines[-1].strip()[:limit] if lines else ""


class Report(object):
    """统一的结果汇总与退出码。"""

    def __init__(self, title):
        self.title = title
        self.rows = []

    def add(self, name, passed, detail=""):
        self.rows.append((name, passed, detail))
        print("  [%s] %-44s %s" % ("OK" if passed else "!!", name, detail))

    def info(self, msg):
        print("  --   %s" % msg)

    def finish(self):
        bad = [r for r in self.rows if not r[1]]
        print("\n" + "=" * 64)
        print("%s：共 %d 项，通过 %d，失败 %d -> %s"
              % (self.title, len(self.rows), len(self.rows) - len(bad), len(bad),
                 "全部通过" if not bad else "**有失败**"))
        for n, _, d in bad:
            print("   失败: %s %s" % (n, d))
        return 0 if not bad else 1
