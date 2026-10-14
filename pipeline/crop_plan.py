# -*- coding: utf-8 -*-
r"""算每镜的 9:16 裁剪方案 —— 「横屏生成、竖屏交付」缺的那一环。

## 为什么要有这个

用户 2026-10-14 拍板：**横屏生成、拼片阶段裁 9:16**
（依据见 `stage_video.py` 文件头）。但「居中裁」对推轨/双人镜是会出事的：

实测 S12（主管从走廊拐角走出、停在沈九面前）——
0.5 秒时主管在画面中间偏右，4.5 秒时**两个人都跑到画面右侧**；
按居中裁，后半段裁出来是一堵空墙。**不是画质问题，是"人没了"。**

所以裁切位置必须**逐镜算**，而不是全局居中。

## 怎么算（不猜，量出来）

对每一镜取样帧（默认 4 fps、宽 320），用两个可量化的信号：

1. **前景量** `fg[t][x] = Σ_y |F[t] - 全片时间中位帧|`（主信号）。
   量的不是"动没动"，而是"**这一列不是背景**"。
   ⚠️ 这一点踩过：先用**相邻帧差**当信号，**站住不动的角色差值为 0**
   （S12 后半段沈九站着不动），于是被当成背景，算出来的窗口落在**空墙上**。
   时间中位帧里没有只出现半程的人，换成它做基准才对。
2. **边缘量** `edge[x]`：该列的边缘能量（FIND_EDGES），**只做平局裁决**。
   整镜静止的空镜前景量处处接近 0，靠它把"画面里有东西的位置"挑出来。

每列合成一个分数，再用**滑窗**求「窗口内分数和」：

- 静态方案：取 `argmax_x min_t 窗口分[t][x]`——**取"最差时刻也不丢内容"的那个位置**（maximin）。
  只按平均分取会挑出"大部分时间对、关键那一秒人出画"的位置。
- 平移方案：`x(t)` 从首段最优位置线性走到末段最优位置；
  只有当**平移的最差分明显优于静态**（默认 1.10 倍）才采用，否则宁可不摇。

结论写进 `剧集/EP0NN/crop_plan.json`，由 `assemble.py` 消费。
**测出来的东西就落成文件**，别每次拼片重算一遍（也便于人工复核某一镜）。

用法：
  python pipeline/crop_plan.py --episode-id 1                 # 算并落盘
  python pipeline/crop_plan.py --episode-id 1 --show          # 只打印表
  python pipeline/crop_plan.py --episode-id 1 --shot 12 --sheet   # 单镜复核图
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from pipeline import common as C  # noqa: E402

SAMPLE_W = 320          # 取样帧宽（够用且快；裁切比例与分辨率无关）
SAMPLE_FPS = 4
MOTION_W = 1.0          # 运动量权重
EDGE_W = 0.5            # 边缘量权重
PAN_GAIN = 1.10         # 平移方案要比静态好这么多才用
PAN_MIN_TRAVEL = 0.03   # 平移距离小于画面宽 3% 就不摇（看着像抖动）


def ratio_wh(ratio):
    a, b = ratio.replace("×", "x").replace(":", "x").split("x")[:2]
    return float(a), float(b)


def sample_frames(video, out_dir, tag):
    """抽低分辨率取样帧。返回灰度数组列表（每帧 h×w）。"""
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "f%03d.png")
    r = C.run_ffmpeg(["-i", C.abs_path(video), "-vf", "fps=%d,scale=%d:-2" % (SAMPLE_FPS, SAMPLE_W),
                      "-frames:v", "240", pattern], cwd=C.ROOT)
    files = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
    if not files:
        return [], r.stderr
    frames = []
    for f in files:
        im = Image.open(os.path.join(out_dir, f)).convert("L")
        frames.append(np.asarray(im, dtype=np.float32))
    return frames, ""


def frame_scores(frames):
    """逐帧逐列分数。返回 (fg, edge)，都是长度 W 的数组（fg 是 T×W）。

    **主信号是「与全片时间中位帧的差」**，不是相邻帧差：
    `|F[t] - median|` 量的是「**这一列不是背景**」。
    差在哪儿很关键——**站住不动的角色，相邻帧差是 0，会被当成背景丢掉**；
    而时间中位帧里没有他（他只在半程出现），差值照样很大。

    边缘量只做**平局裁决**：整镜都静止、没有主体（空镜）时，
    前景量处处接近 0，靠边缘量把"画面里有东西的位置"挑出来。
    """
    h = min(f.shape[0] for f in frames)
    w = min(f.shape[1] for f in frames)
    stack = np.stack([f[:h, :w] for f in frames], 0)

    med = np.median(stack, 0)
    fg = np.abs(stack - med).sum(axis=1)            # T×W 前景量
    scale = np.percentile(fg, 95) or 1.0            # 抗单列爆点
    fg = np.clip(fg / scale, 0.0, 1.0)

    edges = np.asarray(
        Image.fromarray(med.astype(np.uint8)).filter(ImageFilter.FIND_EDGES),
        dtype=np.float32).sum(axis=0)
    edges = edges / (edges.max() or 1.0)
    return fg, edges


def window_sum(score, cw):
    """滑动窗口列分数和。返回长度 (W-cw+1)。"""
    csum = np.concatenate([[0.0], np.cumsum(score)])
    return csum[cw:] - csum[:-cw]


def plan_shot(video, ratio):
    frames, err = sample_frames(video, os.path.join(C.ROOT, ".tools", "tmp", "cropsample"),
                               "x")
    if len(frames) < 3:
        return None, err or "取样帧不足"
    fg, edges = frame_scores(frames)              # T×W, W
    T, W = fg.shape
    aw, ah = ratio_wh(ratio)
    cw = int(round(W * aw / ah))                  # 目标画幅下的裁切宽（列数）
    cw = max(2, min(cw, W - 1))
    span = W - cw                                 # 可移动范围（列数）

    fg_win = np.stack([window_sum(fg[t], cw) for t in range(T)], 0)   # T×(span+1)
    eg_win = window_sum(edges, cw)
    fg_n = fg_win / (fg_win.max() or 1.0)
    eg_n = eg_win / (eg_win.max() or 1.0)
    combined = fg_n + EDGE_W * eg_n[None, :]

    # 静态：**最差时刻也不丢主体**的位置（maximin）
    static_x = int(np.argmax(combined.min(axis=0)))
    static_min = float(combined[:, static_x].min())

    # 单帧最优位置 → 看主体会不会横向跑
    per_frame_best = combined.argmax(axis=1)
    k = max(1, T // 4)
    x_start = int(np.median(per_frame_best[:k]))
    x_end = int(np.median(per_frame_best[-k:]))
    travel = abs(x_end - x_start) / float(max(1, span))

    best = {"mode": "static", "x": round(static_x / float(max(1, span)), 4),
            "worst": round(static_min, 4), "travel": round(travel, 4),
            "best_range": [int(per_frame_best.min()), int(per_frame_best.max())],
            "frames": int(T)}

    if travel >= PAN_MIN_TRAVEL:
        idx = np.clip(np.round(np.linspace(x_start, x_end, T)).astype(int), 0, span)
        pan_min = float(combined[np.arange(T), idx].min())
        if pan_min > static_min * PAN_GAIN:
            best = {"mode": "pan",
                    "x0": round(x_start / float(max(1, span)), 4),
                    "x1": round(x_end / float(max(1, span)), 4),
                    "worst": round(pan_min, 4), "travel": round(travel, 4),
                    "best_range": [int(per_frame_best.min()), int(per_frame_best.max())],
                    "frames": int(T)}
    return best, ""


def main():
    ap = argparse.ArgumentParser(description="算每镜 9:16 裁剪方案")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--ratio", default=None, help="交付画幅（默认取项目 aspect_ratio）")
    ap.add_argument("--out", default=None, help="方案输出路径（默认 剧集/EP0NN/crop_plan.json）")
    ap.add_argument("--shot", default=None, help="只算这些镜号，如 12")
    ap.add_argument("--set", action="append", default=None,
                    help="手工指定某镜位置，可重复：--set 11=0.25（静态）或 "
                         "--set 12=0.20,0.80（平移）。写进方案并标 manual，"
                         "以后重算不会被覆盖")
    ap.add_argument("--show", action="store_true", help="只打印，不落盘")
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    ratio = args.ratio or ep.get("aspect_ratio") or "9:16"
    shots = C.episode_shots(con, args.episode_id)
    con.close()

    only = None
    if args.shot:
        only = {int(x) for x in args.shot.split(",") if x.strip().isdigit()}

    out = args.out or os.path.join(C.ROOT, "剧集", "EP%03d" % ep["seq"], "crop_plan.json")
    # 已算过的方案里，**标了 manual 的条目原样保留**——手工判断比自动算的可信，
    # 不能被下一次重算悄悄覆盖（"重跑一次结果就变了"是最难查的一类问题）。
    plan = {"episode": ep["seq"], "ratio": ratio, "method": "salience(maximin/pan)",
            "shots": {}}
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as f:
                old = json.load(f)
            for k, v in (old.get("shots") or {}).items():
                if v.get("manual"):
                    plan["shots"][k] = v
        except (ValueError, OSError):
            pass

    # 手工指定（--set 11=0.25 / --set 12=0.20,0.80）
    for item in (args.set or []):
        seq, _, val = item.partition("=")
        parts = [p for p in val.replace("，", ",").split(",") if p.strip()]
        try:
            seq_i = int(seq.strip())
            if len(parts) == 1:
                plan["shots"][str(seq_i)] = {"mode": "static",
                                             "x": float(parts[0]), "manual": True}
            elif len(parts) == 2:
                plan["shots"][str(seq_i)] = {"mode": "pan", "x0": float(parts[0]),
                                             "x1": float(parts[1]), "manual": True}
            else:
                raise ValueError("参数个数不对")
            print("手工指定 S%02d → %s" % (seq_i, plan["shots"][str(seq_i)]))
        except ValueError as ex:
            print("--set 参数看不懂：%s（%s）" % (item, ex))
            return 2

    print("第 %s 集交付画幅 %s —— 逐镜裁剪方案\n" % (ep["seq"], ratio))
    print("%-6s %-8s %-22s %s" % ("镜", "方式", "位置(占可移动范围)", "最差窗口分 / 横向位移"))
    print("-" * 78)
    for s in shots:
        seq = s["shot_seq"]
        man = plan["shots"].get(str(seq))
        if man and man.get("manual"):
            tag = "pan" if man["mode"] == "pan" else "static"
            pos = ("x %.2f→%.2f（手工平移）" % (man["x0"], man["x1"]) if man["mode"] == "pan"
                   else "x %.2f（手工静态）" % man["x"])
            print("S%02d    %-8s %-22s 手工指定，不重算" % (seq, tag, pos))
            continue
        if only and seq not in only:
            continue
        if not s["video_path"] or not os.path.exists(C.abs_path(s["video_path"])):
            print("S%02d    跳过（无成片）" % seq)
            continue
        p, why = plan_shot(s["video_path"], ratio)
        if not p:
            print("S%02d    算不出：%s" % (seq, why))
            continue
        plan["shots"][str(seq)] = p
        if p["mode"] == "pan":
            pos = "x %.2f→%.2f（平移）" % (p["x0"], p["x1"])
        else:
            pos = "x %.2f（静态）" % p["x"]
        print("S%02d    %-8s %-22s %.3f / %.1f%%"
              % (seq, p["mode"], pos, p["worst"], p["travel"] * 100))

    if args.show:
        return 0
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("\n方案已写入 %s" % os.path.relpath(out, C.ROOT))
    print("（x 是占「可移动范围」的比例：0=最左，1=最右，0.5=居中）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
