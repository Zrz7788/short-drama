# -*- coding: utf-8 -*-
"""第 9 阶段：抽帧与视频衔接。

对一集里每个已出片的镜头抽首帧与尾帧，并建立镜头之间的续接关系：
第 N+1 镜的首帧 = 第 N 镜的尾帧，continues_from 指向第 N 镜。

抽出的帧有两个用途：
  1. 作为下一镜图生视频的输入（保证跨镜头连续）
  2. 人工检查衔接是否成立——这是成片前最后一个便宜的关卡

用法：
  python pipeline/frames.py --episode-id 1
  python pipeline/frames.py --episode-id 1 --dry-run
  python pipeline/frames.py --episode-id 1 --no-continuity    # 只抽帧，不串接
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402


def extract(video_rel, out_rel, last, dry):
    """抽一帧。last=True 取尾帧，否则取首帧。"""
    if dry:
        return True, "计划"
    args = (["-sseof", "-0.1"] if last else []) + \
           ["-i", video_rel, "-vframes", "1", "-q:v", "2", out_rel]
    r = C.run_ffmpeg(args, cwd=C.ROOT)
    if r.returncode != 0:
        return False, C.tail(r.stderr) or "失败"
    if not os.path.exists(C.abs_path(out_rel)):
        return False, "未产出文件"
    return True, "%.1f KB" % (os.path.getsize(C.abs_path(out_rel)) / 1024.0)


def main():
    ap = argparse.ArgumentParser(description="抽帧与视频衔接")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--no-continuity", action="store_true",
                    help="只抽帧，不建立镜头续接关系")
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    shots = C.episode_shots(con, args.episode_id)
    rep = C.Report("抽帧与衔接")
    print("集：第 %s 集《%s》 项目：%s" % (ep["seq"], ep["title"] or "-", ep["project"]))
    print("镜头 %d 个，模式：%s%s\n"
          % (len(shots), "只抽帧" if args.no_continuity else "抽帧 + 衔接",
             "（dry-run）" if args.dry_run else ""))

    if not args.dry_run:
        # 用实际帧路径的父目录建目录，这样 DSH_ASSETS_ROOT 覆盖也生效
        if shots:
            f0, _ = C.frame_paths(ep["seq"], shots[0]["shot_seq"])
            os.makedirs(os.path.dirname(C.abs_path(f0)), exist_ok=True)

    ready, missing = [], []
    for s in shots:
        if not s["video_path"]:
            missing.append("S%02d" % s["shot_seq"])
            continue
        ready.append(s)

    rep.add("有可用成片的镜头数", True, "%d / %d" % (len(ready), len(shots)))
    if missing:
        rep.info("无成片、已跳过：%s" % ", ".join(missing))

    # --- 逐个镜头抽首尾帧 ---
    done = {}
    for s in ready:
        first_rel, last_rel = C.frame_paths(ep["seq"], s["shot_seq"])
        okf, df = extract(s["video_path"], first_rel, False, args.dry_run)
        okl, dl = extract(s["video_path"], last_rel, True, args.dry_run)
        rep.add("S%02d 抽首帧" % s["shot_seq"], okf, df)
        rep.add("S%02d 抽尾帧" % s["shot_seq"], okl, dl)
        if okf and okl:
            done[s["shot_id"]] = (first_rel, last_rel)
            if not args.dry_run:
                con.execute("UPDATE shot SET first_frame_path=?, last_frame_path=? WHERE id=?",
                            (first_rel, last_rel, s["shot_id"]))

    # --- 建立续接：后一镜接前一镜的尾帧 ---
    if not args.no_continuity:
        chained = 0
        ok_list = [s for s in ready if s["shot_id"] in done]
        for prev, cur in zip(ok_list, ok_list[1:]):
            prev_last = done[prev["shot_id"]][1]
            if not args.dry_run:
                con.execute(
                    "UPDATE shot SET first_frame_path=?, continues_from=? WHERE id=?",
                    (prev_last, prev["shot_id"], cur["shot_id"]))
            chained += 1
        rep.add("建立续接关系数", True, "%d 组（S%02d→S%02d …）"
                % (chained, ok_list[0]["shot_seq"], ok_list[-1]["shot_seq"])
                if chained else "0 组")
        if chained:
            rep.info("注意：默认把相邻镜头全部串接。真实短剧有硬切，"
                     "需要断开时请手工把该镜的 continues_from 置空并重跑。")
    else:
        rep.info("已按 --no-continuity 跳过续接关系")

    if not args.dry_run:
        con.commit()
    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
