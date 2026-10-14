# -*- coding: utf-8 -*-
r"""生成字幕 SRT —— **时间轴不是猜的，是量出来的**。

## 为什么可以不用 ASR

短剧字幕要"贴嘴"，正常做法是 ASR 出词级时间轴。本机没有 ASR（见
`tools/audio_check.py` 文件头）。但我们**不需要知道说了哪个字**——
台词文本库里就有（`shot.dialogue`），缺的只是"**这句话什么时候在说**"。
而"什么时候在说"恰恰是 `audio_check` 已经能量出来的东西：
**浊音段（有声带振动的人声段）的起止**。

所以流程是：台词文本来自库 + 说话窗口来自音频实测 → 按字数把整句切块。

## 切块的规矩

- 按标点断句（，。！？；：、），再按 `--max-chars` 折行（中文短剧 12–16 字/行是通例）。
- 时长按**字数比例**分给每一块：同一句里字多的块占的时间长。
- 每块最短 `--min-dur`（默认 0.8 秒）——太短的闪一下就没了；
  抬到最短时长后如果超出说话窗口，整体顺延，不回头压缩。
- **没检测到人声的镜头**（模型没念）用推定窗口并**明确标注**：
  推定就是推定，不许混进"实测"里（`--report` 会分开列）。

用法：
  python tools/subtitle.py --episode-id 1                 # 出 剧集/EP001/E0N.srt
  python tools/subtitle.py --episode-id 1 --report        # 顺带打印逐镜窗口
  python tools/subtitle.py --episode-id 1 --max-chars 12
"""
import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import numpy as np  # noqa: E402

from pipeline import common as C  # noqa: E402
from audio_check import load_wav, frames_of, WIN, HOP, SR, FPS, pitch_track  # noqa: E402

PUNCT = "，。！？；：、,.!?;:"
# 台词里的表演提示：`（极轻画外音）` `（气音）` 之类，字幕不显示
PAREN = re.compile(r"[（(][^）)]*[）)]")


def speech_window(x_seg):
    """返回 (起点秒, 终点秒, True)。**取最集中的那一簇浊音段**，不是首尾包住。

    ⚠️ 第一版取"第一个浊音帧到最后一个浊音帧"，结果 7 秒的镜头给出
    0.15–6.95 的窗口——**中间夹着一个孤立的误判帧，整句字幕就铺满整镜**。
    字幕要贴嘴，就不能让一个噪点把窗口撑开：先丢掉 <4 帧（64ms）的碎段，
    再按 1.2 秒内的间隔聚簇，最后**取浊音帧最多的那一簇**。
    """
    fr = frames_of(x_seg)
    voiced, f0, rms = pitch_track(fr)
    runs, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(voiced)))
    runs = [r for r in runs if r[1] - r[0] >= 4]        # 丢掉 <64ms 的碎段
    if not runs:
        return None
    gap = int(round(1.2 * FPS))                          # 1.2 秒内的段算同一簇
    clusters, cur = [], [runs[0]]
    for r in runs[1:]:
        if r[0] - cur[-1][1] <= gap:
            cur.append(r)
        else:
            clusters.append(cur)
            cur = [r]
    clusters.append(cur)
    best = max(clusters, key=lambda c: sum(b - a for a, b in c))
    return best[0][0] / FPS, best[-1][1] / FPS, True


def split_line(line, max_chars):
    """先按标点断，再按字数折行。标点留在前一块末尾（中文短剧习惯）。"""
    line = PAREN.sub("", line).strip()
    if not line:
        return []
    parts, cur = [], ""
    for ch in line:
        cur += ch
        if ch in PUNCT and len(cur) >= 4:
            parts.append(cur.strip())
            cur = ""
        elif len(cur) >= max_chars:
            parts.append(cur.strip())
            cur = ""
    if cur.strip():
        parts.append(cur.strip())
    # 合并过短的尾巴（<3 字且上一块能放下）
    out = []
    for p in parts:
        if out and len(p) <= 3 and len(out[-1]) + len(p) <= max_chars + 3:
            out[-1] += p
        else:
            out.append(p)
    return [p for p in out if p]


def n_cjk(s):
    return len(re.sub(r"\s", "", s))


def ts(t):
    """SRT 时间码 HH:MM:SS,mmm"""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return "%02d:%02d:%02d,%03d" % (h, m, int(s), int(round((s - int(s)) * 1000)))


def main():
    ap = argparse.ArgumentParser(description="按实测说话窗口生成字幕")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--audio", default=None, help="成片文件（默认取 asset 表最新一条）")
    ap.add_argument("--out", default=None, help="SRT 输出（默认 剧集/EP0NN/E0N.srt）")
    ap.add_argument("--max-chars", type=int, default=14, help="每行最多字数")
    ap.add_argument("--min-dur", type=float, default=0.8, help="每块最短秒数")
    ap.add_argument("--gap", type=float, default=0.08, help="块间空隙秒数")
    ap.add_argument("--report", action="store_true", help="打印逐镜窗口")
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    shots = C.episode_shots(con, args.episode_id)
    dlg = {r[0]: r[1] for r in con.execute(
        "select seq, dialogue from shot where episode_id=?", (args.episode_id,))}
    src = args.audio
    if not src:
        row = con.execute("SELECT path FROM asset WHERE kind='final' "
                          "ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            print("asset 表里没有成片，先跑第 10 阶段拼片")
            return 2
        src = row["path"]
    con.close()

    tmp = os.path.join(C.ROOT, ".tools", "tmp")
    os.makedirs(tmp, exist_ok=True)
    wav = os.path.join(tmp, "subtitle.wav")
    r = C.run_ffmpeg(["-i", C.abs_path(src), "-vn", "-ac", "1", "-ar", str(SR),
                      "-f", "wav", wav], cwd=C.ROOT)
    if r.returncode != 0:
        print("提音频失败:", C.tail(r.stderr, 200))
        return 2
    x = load_wav(wav)

    if args.report:
        print("%-5s %-14s %-14s %-6s %s"
              % ("镜", "实测说话窗口", "字幕占用", "来源", "台词"))
        print("-" * 90)

    cues, pos, n_meas, n_guess = [], 0.0, 0, 0
    for s in shots:
        vp = s["video_path"]
        dur = C.duration(vp, cwd=C.ROOT) if vp else -1
        if dur <= 0:
            dur = s["shot_seconds"]
        a, b = int(round(pos * SR)), int(round((pos + dur) * SR))
        seg = x[a:b]
        line = (dlg.get(s["shot_seq"]) or "").strip()
        if not line or len(seg) < WIN * 2:
            pos += dur
            continue
        win = speech_window(seg)
        if win:
            ws, we, measured = win
            n_meas += 1
            tag = "实测"
        else:
            ws = min(0.4, dur * 0.1)
            we = min(dur - 0.2, ws + max(1.5, 0.6 * dur))
            measured = False
            n_guess += 1
            tag = "推定（未检测到人声）"
        # 别贴到镜头首尾（转场时不舒服）
        ws = max(ws, 0.15)
        we = min(we, dur - 0.12)
        if we - ws < args.min_dur:
            we = min(dur - 0.05, ws + args.min_dur)

        chunks = split_line(line, args.max_chars)
        if not chunks:
            pos += dur
            continue
        total_n = sum(n_cjk(c) for c in chunks) or len(chunks)
        span = (we - ws) - args.gap * (len(chunks) - 1)
        span = max(span, args.min_dur * len(chunks) * 0.5)
        cur = ws
        for i, c in enumerate(chunks):
            share = span * (n_cjk(c) / float(total_n))
            d = max(args.min_dur, share)
            cs, ce = pos + cur, pos + cur + d
            cues.append((cs, ce, c))
            cur += d + args.gap
        if args.report:
            print("S%02d   %5.2f–%-6.2f  %5.2f–%-6.2f %-6s %s"
                  % (s["shot_seq"], ws, we, cues[-len(chunks)][0] - pos,
                     cues[-1][1] - pos, tag, line[:26]))
        pos += dur

    out = args.out or os.path.join(C.ROOT, "剧集", "EP%03d" % ep["seq"],
                                   "E%02d.srt" % ep["seq"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for i, (cs, ce, text) in enumerate(cues, start=1):
            f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(cs), ts(ce), text))
    print("\n字幕块 %d 条　（实测窗口 %d 镜 / 推定窗口 %d 镜）"
          % (len(cues), n_meas, n_guess))
    print("已写入 %s" % os.path.relpath(out, C.ROOT))
    if n_guess:
        print("⚠️ 有 %d 镜没检测到人声，用的是**推定**窗口——这些块可能不贴嘴，"
              "上线前请人耳过一遍（脚本只能量到「有没有人在说」，"
              "量不到「说到第几个字」）。" % n_guess)
    print("\n烧进成片：python pipeline/assemble.py --episode-id %d --srt %s --out <目录>"
          % (ep["seq"], os.path.relpath(out, C.ROOT).replace("\\", "/")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
