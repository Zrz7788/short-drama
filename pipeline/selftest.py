# -*- coding: utf-8 -*-
"""管线工具链端到端自检。

用 ffmpeg 合成的假素材 + 临时数据库，真跑一遍 frames.py 与 assemble.py：
  场景 A：3 段参数一致的素材 -> 免重编码拼接 + 抽帧续接 + 中文字幕烧录
  场景 B：2 段参数不一致的素材 -> 自动退回重编码

全程不碰生产库与真实 assets/（分别用 --db 与 DSH_ASSETS_ROOT 隔离）。

用法： python pipeline/selftest.py [--keep]
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

TEST_ROOT = os.path.join(C.ROOT, ".tools", "pipeline-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")
TEST_ASSETS = os.path.join(TEST_ROOT, "assets")
TEST_OUT = os.path.join(TEST_ROOT, "out")

rep = C.Report("管线工具链自检")
PROD0 = C.prod_fingerprint()   # 开工前记生产库指纹（供隔离性检查比对）


def stage(script, args):
    env = dict(os.environ,
               DSH_ASSETS_ROOT=TEST_ASSETS,
               PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "pipeline", script)] + args,
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p


def make_clip(name, size, pattern, secs=2):
    rel = os.path.join(".tools", "pipeline-test", "assets", "videos", name)
    os.makedirs(os.path.dirname(C.abs_path(rel)), exist_ok=True)
    r = C.run_ffmpeg(["-f", "lavfi", "-i",
                      "%s=size=%s:rate=24:duration=%d" % (pattern, size, secs),
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", rel], cwd=C.ROOT)
    return rel, r.returncode == 0


def seed_episode(con, project_id, seq, clip_rels):
    eid = con.execute(
        "INSERT INTO episode (project_id, seq, title, target_seconds) VALUES (?,?,?,?)",
        (project_id, seq, "第%d集" % seq, 2 * len(clip_rels))).lastrowid
    for i, rel in enumerate(clip_rels, start=1):
        sid = con.execute(
            "INSERT INTO shot (episode_id, seq, duration_s, prompt) VALUES (?,?,2,?)",
            (eid, i, "近景，测试镜头 %d" % i)).lastrowid
        gid = con.execute(
            "INSERT INTO generation (shot_id, attempt_no, model_id, status,"
            " local_path, cost) VALUES (?,1,?, 'succeeded', ?, 4.0)",
            (sid, "doubao-seedance-2-0-260128", rel)).lastrowid
        con.execute("UPDATE shot SET selected_generation_id=? WHERE id=?", (gid, sid))
    con.commit()
    return eid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留测试产物便于查看")
    args = ap.parse_args()

    C.require_ffmpeg()
    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)

    # ---- 建临时库 ----
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep.add("临时库建立成功", p.returncode == 0 and os.path.exists(TEST_DB),
            os.path.relpath(TEST_DB, C.ROOT))

    # ---- 合成素材 ----
    clips_a, clips_b = [], []
    for i, pat in enumerate(["testsrc", "smptebars", "testsrc2"], start=1):
        rel, okc = make_clip("A%02d.mp4" % i, "270x480", pat)
        clips_a.append(rel)
        rep.add("合成 A%d（270x480）" % i, okc)
    for i, size in enumerate(["320x240", "480x270"], start=1):
        rel, okc = make_clip("B%02d.mp4" % i, size, "testsrc")
        clips_b.append(rel)
        rep.add("合成 B%d（%s）" % (i, size), okc)

    con = C.connect(TEST_DB)
    pid = con.execute("INSERT INTO project (name, logline) VALUES ('自检项目','管线自检')"
                      ).lastrowid
    ep1 = seed_episode(con, pid, 1, clips_a)
    ep2 = seed_episode(con, pid, 2, clips_b)
    rep.add("灌入 2 集共 %d 个镜头" % (len(clips_a) + len(clips_b)), True)
    con.close()

    # ---- 场景 A：抽帧（默认不串接）----
    # 默认行为被两个独立来源纠正过：110 分钟 Seedance 长片全片禁用起始帧
    # （上一镜最后一帧会拖带它自己的漂移），中文短剧 SOP 也把首尾帧标为可跳。
    # 所以这里**先测默认不串**，再单独测 --continuity 那条路还通。
    print("\n--- 场景 A：frames.py（默认只抽帧）---")
    p = stage("frames.py", ["--episode-id", str(ep1), "--db", TEST_DB])
    rep.add("frames.py 退出码为 0", p.returncode == 0,
            C.tail(p.stderr, 60))
    if p.returncode != 0:
        print(p.stdout[-1500:], p.stderr[-800:])

    con = C.connect(TEST_DB)
    shots = con.execute(
        "SELECT id, seq, first_frame_path, last_frame_path, continues_from "
        "FROM shot WHERE episode_id=? ORDER BY seq", (ep1,)).fetchall()
    rep.add("三个镜头都写了首尾帧路径",
            all(s["first_frame_path"] and s["last_frame_path"] for s in shots))
    for s in shots:
        for key in ("first_frame_path", "last_frame_path"):
            rep.add("S%02d %s 文件存在" % (s["seq"], key.split("_")[0]),
                    os.path.exists(C.abs_path(s[key])), s[key] or "-")
    rep.add("默认**不**建立续接关系（continues_from 全空）",
            all(s["continues_from"] is None for s in shots),
            " ".join("S%02d:%s" % (s["seq"], s["continues_from"]) for s in shots))

    # ---- 场景 A2：显式 --continuity 仍然可用 ----
    print("\n--- 场景 A2：frames.py --continuity（显式串接）---")
    p = stage("frames.py", ["--episode-id", str(ep1), "--db", TEST_DB, "--continuity"])
    rep.add("--continuity 退出码为 0", p.returncode == 0, C.tail(p.stderr, 60))
    shots = con.execute(
        "SELECT id, seq, first_frame_path, last_frame_path, continues_from "
        "FROM shot WHERE episode_id=? ORDER BY seq", (ep1,)).fetchall()
    rep.add("显式要求时才串接 S02→S01",
            shots[1]["continues_from"] == shots[0]["id"],
            "%s -> %s" % (shots[1]["continues_from"], shots[0]["id"]))
    rep.add("串接时 S02 首帧 == S01 尾帧",
            shots[1]["first_frame_path"] == shots[0]["last_frame_path"],
            shots[1]["first_frame_path"])
    # 复原：别把串接状态带进后面的拼接测试
    con.execute("UPDATE shot SET continues_from=NULL WHERE episode_id=?", (ep1,))
    con.commit()

    # ---- 场景 A：拼接 + 中文字幕 ----
    print("\n--- 场景 A：assemble.py（拼接 + 中文字幕）---")
    srt_rel = os.path.join(".tools", "pipeline-test", "assets", "subs", "E01.srt")
    os.makedirs(os.path.dirname(C.abs_path(srt_rel)), exist_ok=True)
    with open(C.abs_path(srt_rel), "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,500 --> 00:00:02,500\n你到底是谁？\n"
                "2\n00:00:03,000 --> 00:00:05,000\n我是你姐。\n")

    p = stage("assemble.py", ["--episode-id", str(ep1), "--db", TEST_DB,
                              "--out", TEST_OUT, "--srt", srt_rel])
    out1 = os.path.join(TEST_OUT, "E01.mp4")
    rep.add("assemble.py 退出码为 0", p.returncode == 0,
            C.tail(p.stderr, 60))
    if p.returncode != 0:
        print(p.stdout[-1500:], p.stderr[-800:])
    rep.add("走的是免重编码路径（参数一致）", "满足（各镜头参数一致）" in p.stdout)
    rep.add("成片文件存在", os.path.exists(out1),
            "%.2f MB" % (os.path.getsize(out1) / 1048576.0) if os.path.exists(out1) else "缺失")
    d1 = C.duration(out1, cwd=C.ROOT)
    rep.add("成片时长 ≈ 6 秒（3 段 × 2 秒）", abs(d1 - 6.0) < 0.3, "%.2f 秒" % d1)

    row = con.execute("SELECT kind, path, bytes FROM asset WHERE path LIKE '%E01%'").fetchone()
    rep.add("已写入 asset 表", row is not None, row["path"] if row else "无记录")
    st = con.execute("SELECT status FROM episode WHERE id=?", (ep1,)).fetchone()["status"]
    rep.add("episode.status 已置为 assembled", st == "assembled", st)

    # ---- 场景 B：参数不一致 -> 退回重编码 ----
    print("\n--- 场景 B：assemble.py（参数不一致，应退回重编码）---")
    p = stage("assemble.py", ["--episode-id", str(ep2), "--db", TEST_DB,
                              "--out", TEST_OUT])
    out2 = os.path.join(TEST_OUT, "E02.mp4")
    rep.add("assemble.py 退出码为 0", p.returncode == 0,
            C.tail(p.stderr, 60))
    if p.returncode != 0:
        print("  --- stdout(tail) ---")
        print("\n".join(p.stdout.strip().splitlines()[-18:]))
        print("  --- stderr(tail) ---")
        print("\n".join(p.stderr.strip().splitlines()[-8:]))
    rep.add("检出参数不一致并退回重编码", "将退回重编码" in p.stdout)
    d2 = C.duration(out2, cwd=C.ROOT) if os.path.exists(out2) else -1
    rep.add("重编码后时长仍 ≈ 4 秒", abs(d2 - 4.0) < 0.3, "%.2f 秒" % d2)

    # ---- 确认没碰生产库 ----
    print("\n--- 隔离性检查 ---")
    _unch, _why = C.prod_unchanged(PROD0)
    rep.add("生产库未被写入", _unch, _why)
    rep.add("真实 assets/frames 未被创建",
            not os.path.exists(os.path.join(C.ROOT, "assets", "frames")),
            os.path.join("assets", "frames"))
    con.close()

    code = rep.finish()
    if args.keep:
        print("\n产物保留在:", os.path.relpath(TEST_ROOT, C.ROOT))
    else:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print("已清理测试目录")
    return code


if __name__ == "__main__":
    sys.exit(main())
