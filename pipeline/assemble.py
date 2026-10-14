# -*- coding: utf-8 -*-
r"""第 10 阶段：拼接成集，可选烧字幕。

按镜号顺序把每个镜头的选定成片拼成一集。

## 两条路

1. **免重编码路**（各镜参数一致、且已经是交付画幅）：`-f concat -c copy`，秒级完成。
2. **归一化路**：逐镜裁到交付画幅 → 统一编码 → concat -c copy。
   一切"素材之间不一样"都走这条路：画幅不同、编码不同、像素格式不同。

## 为什么必须逐镜裁（而不是一句 crop 居中）

用户 2026-10-14 拍板**横屏生成、竖屏交付**（理由见 `stage_video.py` 文件头）。
但**居中裁对推轨/双人镜是会把人裁没的**：实测 S12 后半段两个人都在画面右侧，
居中裁出来是一堵空墙。裁切位置由 `pipeline/crop_plan.py` 逐镜量出来，
存在 `剧集/EP0NN/crop_plan.json`；本脚本只**消费**方案，不自己猜。

方案里 x 是「占可移动范围的比例」（0=最左，1=最右，0.5=居中）：
  {"mode": "static", "x": 0.79}            → 整镜固定一个位置
  {"mode": "pan", "x0": 0.2, "x1": 0.8}    → 镜头内线性平移（跟人走）

用法：
  python pipeline/assemble.py --episode-id 1
  python pipeline/assemble.py --episode-id 1 --srt assets/subs/E01.srt
  python pipeline/assemble.py --episode-id 1 --allow-missing --dry-run
  python pipeline/assemble.py --episode-id 1 --deliver-ratio source   # 不裁，原样 16:9
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

# 交付尺寸：竖屏 1080×1920、横屏 1920×1080、方 1080×1080
DELIVER_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


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


def ratio_wh(ratio):
    a, b = ratio.replace("×", "x").replace(":", "x").split("x")[:2]
    return float(a), float(b)


def load_crop_plan(ep, path):
    """读裁剪方案。默认找 剧集/EP0NN/crop_plan.json；没有就返回 None（全居中）。"""
    if path is None:
        cand = os.path.join(C.ROOT, "剧集", "EP%03d" % ep["seq"], "crop_plan.json")
        path = cand if os.path.exists(cand) else None
    if not path:
        return None, None
    if not os.path.exists(path):
        return None, path
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def crop_filter(src_w, src_h, ratio, dur, shot_plan):
    """构造「裁到交付画幅 + 升到交付尺寸」的滤镜串。

    只处理**裁一边**的情形（源更宽就裁左右，源更高就裁上下）——
    这是「横屏生成竖屏交付」的全部情形；源画幅已等于交付画幅时不该走到这里。
    位置表达式统一用 `ow`/`oh`（裁剪后的实际尺寸），
    这样宽高被 chroma 取整时 x 仍然自动对得上，不用自己算偶数。
    """
    tw, th = ratio_wh(ratio)
    out_w, out_h = DELIVER_SIZES[ratio]
    if src_w / float(src_h) > tw / th:          # 源更宽 → 裁左右
        pos = "x"
        if shot_plan and shot_plan.get("mode") == "pan":
            x0, x1 = float(shot_plan["x0"]), float(shot_plan["x1"])
            loc = "(iw-ow)*(%.4f+(%.4f-%.4f)*t/%.3f)" % (x0, x1, x0, max(dur, 0.01))
        else:
            xf = float((shot_plan or {}).get("x", 0.5))
            loc = "(iw-ow)*%.4f" % xf
        crop = "crop=w=ih*%g/%g:h=ih:x=%s:y=0" % (tw, th, loc)
    else:                                        # 源更高 → 裁上下
        loc = "(ih-oh)*%.4f" % float((shot_plan or {}).get("x", 0.5))
        crop = "crop=w=iw:h=iw*%g/%g:x=0:y=%s" % (th, tw, loc)
    return "%s,scale=%d:%d:flags=lanczos,setsar=1" % (crop, out_w, out_h)


def main():
    ap = argparse.ArgumentParser(description="拼接成集")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--out", default=os.path.join("out"),
                    help="输出目录（默认 out/）")
    ap.add_argument("--srt", default=None, help="要烧录的字幕文件（相对仓库根）")
    ap.add_argument("--allow-missing", action="store_true",
                    help="有镜头缺成片时仍继续（默认中止）")
    ap.add_argument("--deliver-ratio", default=None,
                    help="交付画幅：9:16 / 16:9 / 1:1 / source（不裁）。"
                         "默认取项目 aspect_ratio；已就位不裁")
    ap.add_argument("--crop-plan", default=None,
                    help="裁剪方案 JSON（默认 剧集/EP0NN/crop_plan.json）")
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

    ratio = args.deliver_ratio or ep.get("aspect_ratio") or "9:16"
    if ratio in ("source", "none", "原样"):
        ratio = None
    elif ratio not in DELIVER_SIZES:
        rep.add("交付画幅可识别", False, "%s（支持 %s / source）"
                % (ratio, "、".join(sorted(DELIVER_SIZES))))
        con.close()
        return rep.finish()

    plan, plan_path = load_crop_plan(ep, args.crop_plan)
    if plan_path:
        rep.info("裁剪方案：%s%s" % (os.path.relpath(plan_path, C.ROOT),
                                 "" if plan else "（读不出来，全居中）"))
    elif ratio:
        rep.info("没有裁剪方案文件，按**居中**裁（逐镜方案：python pipeline/crop_plan.py）")

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

    sigs = {s for s in (C.video_signature(c["video_path"], cwd=C.ROOT) for c in clips)
            if s}
    audios = {has_audio(c["video_path"]) for c in clips}

    # --- 画幅：源画幅 == 交付画幅 就不裁；否则逐镜归一化 ---
    src_ars = {round(s[0] / float(s[1]), 5) for s in sigs if s[0] and s[1]}
    need_crop = False
    if ratio:
        tgt = ratio_wh(ratio)
        tgt_ar = tgt[0] / tgt[1]
        need_crop = any(abs(a - tgt_ar) > C.SPEC_ASPECT_TOL for a in src_ars)
        rep.info("交付画幅 %s（%d×%d）" % (ratio, DELIVER_SIZES[ratio][0],
                                       DELIVER_SIZES[ratio][1]))
        if need_crop:
            tgt_ar = ratio_wh(ratio)[0] / ratio_wh(ratio)[1]
            cws, shs = [], []
            for c in clips:
                sg = C.video_signature(c["video_path"], cwd=C.ROOT)
                if not sg or not sg[1]:
                    continue
                shs.append(int(sg[1]))
                cws.append(int(int(sg[1]) * tgt_ar) // 2 * 2)
            if cws:
                rep.info("裁切后有效分辨率约 %d×%d（升采样到 %d×%d；"
                         "横屏生成竖屏交付的固有代价）"
                         % (min(cws), max(shs),
                            DELIVER_SIZES[ratio][0], DELIVER_SIZES[ratio][1]))
        else:
            rep.info("各镜已是交付画幅，不裁")
    else:
        rep.info("交付画幅：原样（不裁）")

    can_copy = len(sigs) == 1 and len(audios) == 1 and not need_crop
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
    final_rel = os.path.join(args.out, name + ".mp4")     # 交付目标（可能是 D: 绝对路径）
    # ⚠️ **ffmpeg 只能写工作区**（实测）：写工作区外（D:\dsh-film\...）一律
    # `Error opening output files: Permission denied`——**按进程名拦的，提权也没用**
    # （见 HANDOFF「视频阶段踩的新坑」）。所以 ffmpeg 先在工作区里出片，
    # 再由 **python 跨盘 copyfile** 搬到交付目录（frames.py 已用同一个两步法）。
    stage_rel = os.path.join(tmp_dir, "%s_stage.mp4" % name)
    burn_rel = os.path.join(tmp_dir, "%s_final.mp4" % name)

    if args.dry_run:
        if need_crop:
            rep.info("dry-run：逐镜裁 %s 并归一化到 %d×%d，再拼接"
                     % (ratio, DELIVER_SIZES[ratio][0], DELIVER_SIZES[ratio][1]))
            for c in clips[:8]:
                p = (plan or {}).get("shots", {}).get(str(c["shot_seq"]))
                if p and p.get("mode") == "pan":
                    where = "平移 x %.2f→%.2f" % (p["x0"], p["x1"])
                elif p:
                    where = "静态 x %.2f" % p["x"]
                else:
                    where = "居中 x 0.50"
                rep.info("  S%02d %s" % (c["shot_seq"], where))
            if len(clips) > 8:
                rep.info("  …… 其余 %d 段略" % (len(clips) - 8))
        rep.info("dry-run：将拼接 %d 段 -> %s%s"
                 % (len(clips), final_rel, "，再烧字幕 -> " + final_rel if args.srt else ""))
        con.close()
        return rep.finish()

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    list_file = os.path.join(tmp_dir, "concat_%s.txt" % name)
    write_concat_list([s["video_path"] for s in clips], list_file)

    keep_audio = audios == {True}

    # ⚠️ `-movflags +faststart` 不是可有可无的：ffmpeg 默认把 moov 原子写在文件**末尾**，
    # 浏览器/网页预览要拿到整段才能起播，实测表现是"文件好好的，界面里打不开"。
    # 拼片产物要给人**在界面里点开看**，所以一律 faststart。
    FASTSTART = ["-movflags", "+faststart"]

    if can_copy:
        # --- 快路：免重编码 ---
        r = C.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file,
                          "-c", "copy"] + FASTSTART + [stage_rel], cwd=C.ROOT)
        if r.returncode != 0:
            rep.info("copy 失败，转重编码：" + C.tail(r.stderr, 60))
            can_copy = False
    if not can_copy:
        # --- 归一化路：逐镜裁齐 + 统一编码，再 concat -c copy ---
        seg_dir = os.path.join(tmp_dir, "assemble_%s" % name)
        if os.path.isdir(seg_dir):
            shutil.rmtree(seg_dir, ignore_errors=True)
        os.makedirs(seg_dir, exist_ok=True)
        segs, bad = [], None
        # 不裁时也要把各段尺寸统一（否则 concat 拼不上）：按第一段归一 + 补边
        ref = next((s for s in sigs if s and s[0] and s[1]), None)
        rw = int(ref[0]) if ref else 1080
        rh = int(ref[1]) if ref else 1920
        if not need_crop:
            rep.info("归一化到 %dx%d（等比缩放 + 补边，不改构图）" % (rw, rh))
        for c in clips:
            seg = os.path.join(seg_dir, "S%03d.mp4" % c["shot_seq"])
            cmd = ["-i", c["video_path"]]
            if ratio and need_crop:
                sg = C.video_signature(c["video_path"], cwd=C.ROOT)
                vf = crop_filter(int(sg[0]), int(sg[1]), ratio,
                                 C.duration(c["video_path"], cwd=C.ROOT),
                                 (plan or {}).get("shots", {}).get(str(c["shot_seq"])))
            else:
                vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                      "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1" % (rw, rh, rw, rh))
            cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-pix_fmt", "yuv420p"]
            cmd += (["-c:a", "aac", "-ar", "48000", "-ac", "2"] if keep_audio else ["-an"])
            cmd += [seg]
            rr = C.run_ffmpeg(cmd, cwd=C.ROOT)
            if rr.returncode != 0:
                bad = "S%02d %s" % (c["shot_seq"], C.tail(rr.stderr, 80))
                break
            segs.append(seg)
        rep.add("逐镜归一化（%s）" % ("裁 " + ratio if (ratio and need_crop) else "只统一规格"),
                bad is None, bad or "%d 段" % len(segs))
        if bad is None:
            seg_list = os.path.join(tmp_dir, "concat_%s_seg.txt" % name)
            write_concat_list(segs, seg_list)
            r = C.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", seg_list,
                              "-c", "copy"] + FASTSTART + [stage_rel], cwd=C.ROOT)
            if r.returncode != 0:
                rep.info("归一化后 copy 拼接失败，转逐段转码拼接：" + C.tail(r.stderr, 60))
                r = C.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", seg_list,
                                  "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
                                 + (["-c:a", "aac", "-ar", "48000"] if keep_audio else ["-an"])
                                 + FASTSTART + [stage_rel], cwd=C.ROOT)
        else:
            r = rr
    rep.add("拼接输出", r.returncode == 0,
            C.tail(r.stderr) if r.returncode else stage_rel)

    if r.returncode != 0:
        con.close()
        return rep.finish()

    got = C.duration(stage_rel, cwd=C.ROOT)
    rep.add("成片时长与素材之和一致", abs(got - total_expect) < 0.5,
            "%.2f 秒（预期 %.2f）" % (got, total_expect))
    sg = C.video_signature(stage_rel, cwd=C.ROOT)
    if sg and sg[0]:
        rep.add("成片画幅符合交付要求",
                (not ratio) or abs(sg[0] / float(sg[1]) - ratio_wh(ratio)[0] / ratio_wh(ratio)[1])
                <= C.SPEC_ASPECT_TOL,
                "%sx%s" % (sg[0], sg[1]))

    # --- 烧字幕 ---
    delivered = stage_rel
    if args.srt:
        srt_abs = C.abs_path(args.srt)
        if not os.path.exists(srt_abs):
            rep.add("字幕文件存在", False, args.srt)
        else:
            # 用相对路径 + cwd 调 ffmpeg，绕开 Windows 盘符冒号在滤镜里的转义坑
            srt_rel = os.path.relpath(srt_abs, C.ROOT).replace("\\", "/")
            r2 = C.run_ffmpeg(["-i", stage_rel, "-vf", "subtitles=%s" % srt_rel,
                               "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
                              + (["-c:a", "copy"] if keep_audio else ["-an"])
                              + ["-movflags", "+faststart", burn_rel], cwd=C.ROOT)
            rep.add("字幕烧录", r2.returncode == 0,
                    C.tail(r2.stderr) if r2.returncode else burn_rel)
            if r2.returncode == 0:
                delivered = burn_rel

    # --- 交付：ffmpeg 只写工作区，跨盘交给 python（见上文 Permission denied 说明）---
    dest = C.abs_path(final_rel)
    if os.path.abspath(delivered) != os.path.abspath(dest):
        os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(delivered, dest)
        rep.info("落盘：%s -> %s（python 跨盘复制）" % (delivered, final_rel))

    # --- 落库 ---
    fp = C.abs_path(final_rel)
    if os.path.exists(fp):
        size = os.path.getsize(fp)
        # 同一个交付路径只留一条记录：拼片会反复重跑（改裁切、补镜头、烧字幕），
        # 每跑一次 INSERT 一条，asset 表就会堆出同路径的 Old/新 两条，
        # **后来的人按 asset 取素材就可能取到旧版本**（sha256 不一样）。
        # 记录的是"这个交付物长什么样"，所以按路径覆盖，不是追加。
        con.execute("DELETE FROM asset WHERE kind='final' AND path=?",
                    (C.asset_rel(fp),))
        con.execute("INSERT INTO asset (kind, path, bytes, sha256) VALUES ('final',?,?,?)",
                    (C.asset_rel(fp), size, sha256(fp)))
        con.execute("UPDATE episode SET status='assembled' WHERE id=?", (args.episode_id,))
        con.commit()
        rep.add("已记录到 asset 表", True, "%.2f MB" % (size / 1048576.0))
        rep.info("输出：%s" % final_rel)

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
