# -*- coding: utf-8 -*-
r"""成片机械体检：**不用眼睛也能查出来的那几类瑕疵**。

## 为什么要它

人工看帧能发现"遗体像站着"这类语义问题，但有两类问题**人眼容易看漏**、
而且**每个镜头都要看**（看 14 镜 × 4 帧很贵）：

| 可量化信号 | 能查出的瑕疵 | 怎么判 |
|---|---|---|
| 平均亮度 | 同一场戏里某镜明显偏暗/偏亮、接缝处跳光 | 与**同场/邻居**比，超 1.5 档标出来 |
| 色温倾向 R/B | 冷白荧光灯里混进暖黄镜（混光） | 偏离全片中位数 >15% |
| 清晰度（边缘方差） | 糊、失焦、升采样过度 | 低于全片中位数的 50% |
| 帧间运动量 | **静帧卡住**（模型没动）、抖动闪烁 | 低于中位数 25% / 高于 3 倍 |
| 首尾帧差 | 镜头内画面**跳变**（模型塞了两个镜头） | 首尾帧差远大于相邻帧差 |

**它不替代人眼**：姿态、解剖、道具穿模这些语义问题它看不出来。
两者是互补的——机械体检负责"每镜都过一遍便宜的筛子"，
人眼负责"只看筛出来的那几个"。

用法：
  python tools/qc_frames.py --episode-id 1
  python tools/qc_frames.py --episode-id 1 --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from pipeline import common as C  # noqa: E402

W = 320          # 取样帧宽（够算统计，且快）
FPS = 6          # 取样帧率：运动量要连续帧才算得准


def measure(video, tmp):
    """抽帧并返回 (亮度, 色温R/B, 清晰度, 运动量, 首尾帧差)。"""
    d = os.path.join(tmp, "m")
    if os.path.isdir(d):
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
    os.makedirs(d, exist_ok=True)
    r = C.run_ffmpeg(["-i", C.abs_path(video), "-vf", "fps=%d,scale=%d:-2" % (FPS, W),
                      "-frames:v", "600", os.path.join(d, "f%04d.png")], cwd=C.ROOT)
    files = sorted(os.listdir(d))
    if len(files) < 3:
        return None
    luma, warm, sharp, gray = [], [], [], []
    for f in files:
        im = Image.open(os.path.join(d, f)).convert("RGB")
        a = np.asarray(im, dtype=np.float32)
        g = np.asarray(im.convert("L"), dtype=np.float32)
        gray.append(g)
        luma.append(float(g.mean()))
        warm.append(float(a[:, :, 0].mean() / (a[:, :, 2].mean() + 1e-6)))
        e = np.asarray(im.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)
        sharp.append(float(e.var()))
    gray = np.stack(gray, 0)
    motion = np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))
    return {
        "luma": float(np.mean(luma)),
        "warm": float(np.mean(warm)),
        "sharp": float(np.mean(sharp)),
        "motion": float(np.mean(motion)),
        "motion_p90": float(np.percentile(motion, 90)),
        "first_last": float(np.abs(gray[-1] - gray[0]).mean()),
        "frames": len(files),
    }


def main():
    ap = argparse.ArgumentParser(description="成片机械体检（可量化瑕疵）")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    shots = [s for s in C.episode_shots(con, args.episode_id) if s["video_path"]]
    con.close()
    tmp = os.path.join(C.ROOT, ".tools", "tmp", "qc")
    os.makedirs(tmp, exist_ok=True)

    data = {}
    for s in shots:
        m = measure(s["video_path"], tmp)
        if m:
            data[s["shot_seq"]] = m
    if not data:
        print("没量到任何镜头")
        return 2

    med = {k: float(np.median([v[k] for v in data.values()]))
           for k in ("luma", "warm", "sharp", "motion")}
    flags = []

    print("=" * 96)
    print("成片机械体检 · EP%03d（%d 镜）  全片中位数：亮度 %.1f ｜R/B %.2f ｜清晰度 %.0f "
          "｜运动 %.2f" % (ep["seq"], len(data), med["luma"], med["warm"],
                        med["sharp"], med["motion"]))
    print("=" * 96)
    print("%-5s %8s %8s %10s %9s %9s %s"
          % ("镜", "亮度", "R/B", "清晰度", "运动量", "首尾帧差", "判定"))
    print("-" * 96)
    prev = None
    for seq in sorted(data):
        v = data[seq]
        notes = []
        if v["sharp"] < med["sharp"] * 0.5:
            notes.append("**糊**（清晰度仅中位 %.0f%%）" % (100 * v["sharp"] / med["sharp"]))
        if v["motion"] < med["motion"] * 0.25:
            notes.append("**近乎静帧**（运动仅中位 %.0f%%）" % (100 * v["motion"] / med["motion"]))
        if v["motion"] > med["motion"] * 3:
            notes.append("运动异常大（抖动/闪烁？）")
        if abs(v["warm"] - med["warm"]) > med["warm"] * 0.15:
            notes.append("色温偏离全片（%s）" % ("偏暖" if v["warm"] > med["warm"] else "偏冷"))
        if abs(v["luma"] - med["luma"]) > 18:
            notes.append("亮度偏离全片 %.0f 档" % (abs(v["luma"] - med["luma"]) / 10.0))
        if prev is not None:
            if abs(v["luma"] - prev["luma"]) > 22:
                notes.append("**与上一镜接缝跳光** %.0f" % abs(v["luma"] - prev["luma"]))
            if abs(v["warm"] - prev["warm"]) > 0.12:
                notes.append("**与上一镜接缝跳色温**")
        if v["first_last"] > v["motion"] * 6:
            notes.append("镜内疑似跳变（首尾差远大于帧间差）")
        print("S%02d  %8.1f %8.2f %10.0f %9.2f %9.2f  %s"
              % (seq, v["luma"], v["warm"], v["sharp"], v["motion"], v["first_last"],
                 "；".join(notes) or "—"))
        for n in notes:
            flags.append("S%02d %s" % (seq, n))
        prev = v

    print("-" * 96)
    print("标出的疑点 %d 处。**这是筛子，不是判决**：糊/静帧/跳光值得人眼看一眼，"
          "姿态、解剖、道具穿模它看不出来（那些归人眼）。" % len(flags))
    if args.json:
        print(json.dumps({"medians": med, "shots": data, "flags": flags},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
