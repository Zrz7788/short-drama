# -*- coding: utf-8 -*-
"""第 10 阶段：拼接成集，可选烧字幕。

按镜号顺序把每个镜头的选定成片拼成一集。
优先用 -c copy（不重编码，秒级完成）；参数不一致时自动退回重编码。

用法：
  python pipeline/assemble.py --episode-id 1
  python pipeline/assemble.py --episode-id 1 --srt assets/subs/E01.srt
  python pipeline/assemble.py --episode-id 1 --allow-missing --dry-run
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def has_audio(path):
    p = C.probe(path, "stream=codec_type", cwd=C.ROOT)
    return "audio" in p


def write_concat_list(paths, dest):
    """concat 解复用器按清单文件所在目录解析相对路径，
    所以统一写绝对路径 + 正斜杠，避免一切歧义。"""
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        for p in paths:
            abs_p = C.abs_path(p).replace("\\", "/")
            f.write("file '%s'\n" % abs_p)


def main():
    ap = argparse.ArgumentParser(description="拼接成集")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--out", default=os.path.join("out"),
                    help="输出目录（默认 out/）")
    ap.add_argument("--srt", default=None, help="要烧录的字幕文件（相对仓库根）")
    ap.add_argument("--allow-missing", action="store_true",
                    help="有镜头缺成片时仍继续（默认中止）")
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    shots = C.episode_shots(con, args.episode_id)
    rep = C.Report("拼接成集")
    print("集：第 %s 集《%s》\n" % (ep["seq"], ep["title"] or "-"))

    clips, missing = [], []
    for s in shots:
        if s["video_path"] and os.path.exists(C.abs_path(s["video_path"])):
            clips.append(s)
        else:
            missing.append("S%02d" % s["shot_seq"])

    rep.add("有可用成片的镜头", not missing or args.allow_missing,
            "%d / %d%s" % (len(clips), len(shots),
                           "" if not missing else "  缺: " + ", ".join(missing)))
    if not clips:
        rep.add("至少有一个可用镜头", False, "无可拼接素材")
        con.close()
        return rep.finish()
    if missing and not args.allow_missing:
        rep.info("有镜头缺成片，已中止。确认要出残缺版请加 --allow-missing")
        con.close()
        return rep.finish()

    # --- 判断能否 -c copy ---
    sigs = {s for s in (C.video_signature(c["video_path"], cwd=C.ROOT) for c in clips)
            if s}
    audios = {has_audio(c["video_path"]) for c in clips}
    can_copy = len(sigs) == 1 and len(audios) == 1
    # 注意：这里用 info 而不是 add。参数不一致是**预期内的分支**（触发重编码回退），
    # 不是失败项。用 add(False) 会让整次运行退出码非零，把正常回退报成错误。
    rep.info("免重编码条件：" + ("满足（各镜头参数一致）" if can_copy
                              else "不满足，将退回重编码"))

    total_expect = 0.0
    for s in clips:
        d = C.duration(s["video_path"], cwd=C.ROOT)
        if d > 0:
            total_expect += d
    rep.info("按素材实测，预期成片时长 %.2f 秒（%d 段）" % (total_expect, len(clips)))

    out_dir = C.abs_path(args.out)
    tmp_dir = os.path.join(C.ROOT, ".tools", "tmp")
    name = "E%02d" % ep["seq"]
    final_rel = os.path.join(args.out, name + ".mp4")
    stage_rel = final_rel if not args.srt else os.path.join(args.out, name + "_raw.mp4")

    if args.dry_run:
        rep.info("dry-run：将拼接 %d 段 -> %s%s"
                 % (len(clips), final_rel, "，再烧字幕 -> " + final_rel if args.srt else ""))
        con.close()
        return rep.finish()

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    list_file = os.path.join(tmp_dir, "concat_%s.txt" % name)
    write_concat_list([s["video_path"] for s in clips], list_file)

    # --- 拼接 ---
    if can_copy:
        r = C.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file,
                          "-c", "copy", stage_rel], cwd=C.ROOT)
        if r.returncode != 0:
            rep.info("copy 失败，转重编码：" + C.tail(r.stderr, 60))
            can_copy = False
    if not can_copy:
        ref = next(iter(sigs), None)
        w = (ref[0] if ref and ref[0] else 1080)
        h = (ref[1] if ref and ref[1] else 1920)
        vf = ("scale=%s:%s:force_original_aspect_ratio=decrease,"
              "pad=%s:%s:(ow-iw)/2:(oh-ih)/2,setsar=1" % (w, h, w, h))
        rep.info("归一化到 %sx%s" % (w, h))
        cmd = ["-f", "concat", "-safe", "0", "-i", list_file,
               "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-vf", vf]
        if audios == {True}:
            cmd += ["-c:a", "aac", "-ar", "48000"]
        else:
            cmd += ["-an"]
            if audios == {True, False}:
                rep.info("警告：部分镜头有音轨、部分没有，已全部丢弃音轨（-an）")
        cmd += [stage_rel]
        r = C.run_ffmpeg(cmd, cwd=C.ROOT)
    rep.add("拼接输出", r.returncode == 0,
            C.tail(r.stderr) if r.returncode else stage_rel)

    if r.returncode != 0:
        con.close()
        return rep.finish()

    got = C.duration(stage_rel, cwd=C.ROOT)
    rep.add("成片时长与素材之和一致", abs(got - total_expect) < 0.5,
            "%.2f 秒（预期 %.2f）" % (got, total_expect))

    # --- 烧字幕 ---
    if args.srt:
        srt_abs = C.abs_path(args.srt)
        if not os.path.exists(srt_abs):
            rep.add("字幕文件存在", False, args.srt)
        else:
            # 用相对路径 + cwd 调 ffmpeg，绕开 Windows 盘符冒号在滤镜里的转义坑
            srt_rel = os.path.relpath(srt_abs, C.ROOT).replace("\\", "/")
            r2 = C.run_ffmpeg(["-i", stage_rel, "-vf", "subtitles=%s" % srt_rel,
                               "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                               "-c:a", "copy" if audios == {True} else "aac",
                               final_rel], cwd=C.ROOT)
            rep.add("字幕烧录", r2.returncode == 0,
                    C.tail(r2.stderr) if r2.returncode else final_rel)
    else:
        if stage_rel != final_rel:
            os.replace(C.abs_path(stage_rel), C.abs_path(final_rel))

    # --- 落库 ---
    fp = C.abs_path(final_rel)
    if os.path.exists(fp):
        size = os.path.getsize(fp)
        con.execute("INSERT INTO asset (kind, path, bytes, sha256) VALUES ('final',?,?,?)",
                    (final_rel, size, sha256(fp)))
        con.execute("UPDATE episode SET status='assembled' WHERE id=?", (args.episode_id,))
        con.commit()
        rep.add("已记录到 asset 表", True, "%.2f MB" % (size / 1048576.0))
        rep.info("输出：%s" % final_rel)

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
