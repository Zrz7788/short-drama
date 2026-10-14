# -*- coding: utf-8 -*-
r"""听不了，就**量**：成片音轨里到底有没有人声？

## 为什么需要它

配音前必须先知道"模型自己生成的音轨里有没有对白"——
有，就得决定保留还是压过；没有，配音就是往干净的环境音上加。
但**本机没有 ASR**（`dsh-video-lens` 要 `VIDEO_LENS_API_KEY`；方舟账号
`GET /models` 135 个模型里**没有任何音频模型**，实测 0 个）。
所以不靠转写，靠**四个可量化的特征**一起判：

| 特征 | 直觉 | 人说话 | 稳态嗡鸣 | 脚步/水声等拟音 |
|---|---|---|---|---|
| **调制能量** 2–8 Hz ÷ 全带 | 话是**一串音节**，每秒 2–8 个 | 高 | 极低 | 高（也会响） |
| **浊音帧占比**（自相关基频 70–350 Hz） | 元音有**周期性基频** | 高 | 高 | **低**（多为噪声） |
| **基频起伏**（半音标准差） | 说话有**声调起伏** | 中高 | ≈0（定音嗡鸣） | — |
| **谱平坦度** 几何/算术均值 | 浊音（谐波）+ 清音（噪声）交替 | 0.1–0.5 | 接近 0 | 接近 1 |

**关键一条是"浊音帧占比"**：只用调制能量会把**脚步、水声、纸张**全判成语音
（它们同样是脉冲式的、同样落在 2–8 Hz）——所以必须再要求"有稳定基频"。
反过来，只看基频会把**荧光灯嗡鸣/机器定音**判成语音，所以还要叠调制与声调起伏。

⚠️ 它只能判"**有没有人在说话**"，判不了"**说的是哪句台词**"。
别拿"检测到语音"冒充"台词对得上"——那是两件事。

用法：
  python tools/audio_check.py --episode-id 1
  python tools/audio_check.py --episode-id 1 --zoom 12        # 单镜高清语谱图
  python tools/audio_check.py --self-test                     # 跑控制信号（负向/正向）
"""
import argparse
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from pipeline import common as C  # noqa: E402

SR = 16000
WIN = 1024                      # 64 ms
HOP = 256                       # 16 ms → 62.5 帧/秒
FPS = SR / float(HOP)
SPEECH_BAND = (300.0, 3400.0)
MOD_BAND = (2.0, 8.0)
PITCH_RANGE = (70.0, 350.0)
VOICED_MIN = 0.35               # 自相关归一化峰值阈值


# ---------------------------------------------------------------- 读音频
def load_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit("只支持 16bit PCM wav，实际 %d 字节/样本" % sw)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def frames_of(x):
    if len(x) < WIN:
        x = np.pad(x, (0, WIN - len(x)))
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def pitch_track(fr):
    """自相关基频。返回 (是否有浊音, 基频Hz, 每帧RMS)。"""
    seg = fr - fr.mean(axis=1, keepdims=True)
    n = seg.shape[1]
    pad = np.zeros_like(seg)
    full = np.concatenate([seg, pad], axis=1)
    spec = np.fft.rfft(full, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :n]
    r0 = ac[:, 0:1] + 1e-12
    lo = int(SR / PITCH_RANGE[1])
    hi = int(SR / PITCH_RANGE[0])
    win = ac[:, lo:hi]
    lag = np.argmax(win, axis=1) + lo
    peak = win[np.arange(len(win)), lag - lo] / r0[:, 0]
    rms = np.sqrt((seg ** 2).mean(axis=1))
    voiced = (peak > VOICED_MIN) & (rms > max(1e-4, rms.max() * 0.08))
    f0 = np.where(voiced, SR / np.maximum(lag, 1), np.nan)
    return voiced, f0, rms


def bandpass(sig, lo, hi, sr=SR):
    """简易 FFT 带通（只用于自检的控制信号，不碰真实音频）。"""
    s = np.fft.rfft(sig)
    f = np.fft.rfftfreq(len(sig), 1.0 / sr)
    s[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(s, n=len(sig))


def shot_metrics(x_seg):
    """一段音频的四个特征 + dBFS。"""
    fr = frames_of(x_seg)
    spec = np.abs(np.fft.rfft(fr * np.hanning(WIN)[None, :], axis=1))
    freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
    band = (freqs >= SPEECH_BAND[0]) & (freqs <= SPEECH_BAND[1])

    band_ratio = float(spec[:, band].sum() / (spec.sum() + 1e-9))
    env = spec[:, band].sum(axis=1)
    e = env - env.mean()
    # ⚠️ 先要求**包络本身真的在起伏**，再谈 2–8 Hz 占多少。
    # 少了这一步会踩"分母趋零"：定音嗡鸣的包络几乎是常数，AC 能量≈0，
    # 两个极小数相除会算出**调制 0.99**这种鬼值（实测：220Hz 纯音被判 0.989）。
    env_ac = float(np.sqrt((e ** 2).mean()) / (env.mean() + 1e-9))
    if len(e) > 8 and env_ac > 0.05:
        m = np.abs(np.fft.rfft(e * np.hanning(len(e))))
        mf = np.fft.rfftfreq(len(e), HOP / float(SR))
        sel = (mf >= MOD_BAND[0]) & (mf <= MOD_BAND[1])
        mod = float(m[sel].sum() / (m[1:].sum() + 1e-9))
    else:
        mod = 0.0

    s = spec + 1e-9
    live = s[env > (env.max() * 0.1 + 1e-9)] if env.max() > 0 else s
    flat = float(np.exp(np.log(live).mean(axis=1)).mean()
                 / (live.mean(axis=1).mean() + 1e-9))

    voiced, f0, _rms = pitch_track(fr)
    v_rate = float(voiced.mean())
    # 浊音段计数 + 音节率：**用来跟台词字数对**。
    # 实测（EP001）：DB 里没有台词的 S01/S02/S04 是 **0 段**，
    # 有台词的镜头是 9–24 段、6–9 个/秒 —— 同一套房间底噪里只有"有词的镜头"
    # 出这个数，所以它比单看占比更能说明"这里真的在念词"。
    runs, run = [], 0
    for v in voiced:
        if v:
            run += 1
        else:
            if run >= 3:
                runs.append(run)
            run = 0
    if run >= 3:
        runs.append(run)
    packets = len(runs)
    syl = sum(max(1, int(round(n / (FPS * 0.18)))) for n in runs)   # ~180ms/音节
    syl_rate = (syl / (voiced.sum() / FPS)) if voiced.sum() > FPS * 0.1 else 0.0
    good = f0[~np.isnan(f0)]
    if len(good) > 3:
        st = 12.0 * np.log2(good / np.median(good))
        f0_var = float(np.std(st))
    else:
        f0_var = 0.0

    dbfs = float(20 * np.log10(np.sqrt((x_seg ** 2).mean()) + 1e-12))
    # 判定：三条**同时**满足才算"有人声迹象"。
    # ⚠️ 谱平坦度**不参与判定**，只作为诊断数字报出来——
    # 实测它太脆：白噪/脚步已经被"浊音占比"挡掉了（它们的浊音占比≈0），
    # 再加一道平坦度阈值，反而会把**带清音的合成语音**（0.83）误杀。
    # 一个会误杀正向样本的阈值，比没有这个阈值更糟。
    speech = (mod > 0.22) and (v_rate > 0.25) and (band_ratio > 0.30)
    return {"mod": mod, "voiced": v_rate, "f0_var": f0_var, "flat": flat,
            "band_ratio": band_ratio, "dbfs": dbfs, "speech": speech,
            "packets": packets, "syl_rate": syl_rate,
            "seconds": len(x_seg) / float(SR)}


# ---------------------------------------------------------------- 画语谱图
def spectro_rows(spec, freqs, lo_hz=80.0, hi_hz=8000.0, n_out=300):
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    db = 20 * np.log10(spec[:, band] + 1e-6)
    fr = freqs[band]
    edges = np.logspace(np.log10(lo_hz), np.log10(hi_hz), n_out + 1)
    rows = []
    for i in range(n_out):
        sel = (fr >= edges[i]) & (fr < edges[i + 1])
        rows.append(db[:, sel].mean(axis=1) if sel.any() else np.zeros(db.shape[0]))
    return np.stack(rows, 0)


def colorize(db, floor_db=-75.0):
    t = np.clip((db - floor_db) / (-floor_db), 0.0, 1.0)
    r = np.clip(1.9 * t - 0.65, 0, 1)
    g = np.clip(1.7 * t - 0.28, 0, 1)
    b = np.clip(2.3 * t, 0, 1) * np.clip(2.1 - 2.1 * t, 0, 1)
    return (np.stack([r, g, b], -1) * 255).astype(np.uint8)


def save_sheet(spec_t, freqs, marks_frac, out, max_w=1600):
    """spec_t: 频率行 × 时间列（dB）。时间轴压到 <= max_w。"""
    t = spec_t.shape[1]
    step = max(1, int(np.ceil(t / float(max_w))))
    t2 = t // step
    db = spec_t[:, :t2 * step].reshape(spec_t.shape[0], t2, step).mean(axis=2)
    # 行=频率（低频在**下**）、列=时间。PIL 的 ndarray 是 (高, 宽)，
    # 所以**频率轴不要转置**，只上下翻一次——第一版多转了一次，
    # 结果是"时间竖着、频率横着"的图（能看，但读起来别扭）。
    rgb = colorize(db)[::-1]          # 时间×频率，低频在下
    im = Image.fromarray(rgb)
    px = np.asarray(im).copy()
    for f in marks_frac[:-1]:
        c = min(max(int(round(f * px.shape[1])), 0), px.shape[1] - 1)
        px[:, c] = [255, 60, 60]
    Image.fromarray(px).save(out)
    return Image.fromarray(px).size


# ---------------------------------------------------------------- 主流程
def self_test():
    """控制信号：负向（定音嗡鸣、白噪、静音）与正向（语速调制+基频起伏的合成"说话"）。"""
    t = np.arange(SR * 4) / float(SR)
    sigs = {
        "静音": np.zeros_like(t),
        "定音嗡鸣 220Hz": 0.2 * np.sin(2 * np.pi * 220 * t),
        "白噪声": 0.2 * np.random.RandomState(0).randn(len(t)),
        "荧光灯嗡鸣 100Hz+噪声": (0.15 * np.sin(2 * np.pi * 100 * t)
                                  + 0.03 * np.random.RandomState(1).randn(len(t))),
        "脚步拟音（2Hz 脉冲噪声）": None,
        "合成说话（4Hz 音节 + 声调起伏 + 清音）": None,
    }
    rs = np.random.RandomState(3)
    step = np.clip(np.sin(2 * np.pi * 2.0 * t), 0, 1) ** 10
    sigs["脚步拟音（2Hz 脉冲噪声）"] = 0.25 * step * np.random.RandomState(1).randn(len(t))
    rs = np.random.RandomState(2)
    f0 = 130 * 2 ** (np.cumsum(rs.randn(len(t)) * 0.004) / 12.0)   # 慢速声调起伏
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)                 # 4 Hz 音节包络
    voiced = 0.25 * syll * np.sin(2 * np.pi * np.cumsum(f0) / SR)
    # 清音：每音节末尾一小段噪声，**限带在 1.5–6 kHz**
    # （真实擦音是高频窄带；不限带的话全带白噪会把"语音带占比"稀释掉，
    #   于是这条正向对照会被自己的判据误杀——实测踩过）
    burst = np.clip(np.sin(2 * np.pi * 4.0 * t + 2.6), 0, 1) ** 8
    unvoiced = 0.9 * burst * bandpass(rs.randn(len(t)), 1500.0, 6000.0)
    sigs["合成说话（4Hz 音节 + 声调起伏 + 清音）"] = voiced + unvoiced

    print("控制信号自检（判据必须把负向信号判成「无语音迹象」）\n")
    print("%-34s %8s %8s %8s %8s %s"
          % ("信号", "调制2-8Hz", "浊音占比", "基频起伏", "谱平坦度", "判定"))
    print("-" * 88)
    for name, x in sigs.items():
        m = shot_metrics(x.astype(np.float32))
        print("%-34s %8.3f %8.3f %8.2f %8.3f %s"
              % (name, m["mod"], m["voiced"], m["f0_var"], m["flat"],
                 "**判成有人声**" if m["speech"] else "无语音迹象"))
    print("\n注：合成说话是**人造的**正向对照，不是真语音；它只验证判据方向对不对。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="量成片音轨里有没有人声（本机无 ASR）")
    ap.add_argument("--episode-id", type=int, default=None)
    ap.add_argument("--audio", default=None,
                    help="直接指定音频/视频文件（默认取 asset 表里最新的成片）")
    ap.add_argument("--sheet", default=None, help="语谱图输出路径")
    ap.add_argument("--zoom", type=int, default=None, help="只出这一镜的高清语谱图")
    ap.add_argument("--self-test", action="store_true", help="跑控制信号自检")
    C.add_common_args(ap)
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.episode_id is None and not args.audio:
        print("要么给 --episode-id，要么给 --audio")
        return 2

    C.require_ffmpeg()
    con = C.connect(args.db)
    src = args.audio
    shots = []
    ep_seq = 1
    if args.episode_id is not None:
        ep = C.episode_info(con, args.episode_id)
        if not ep:
            print("找不到集:", args.episode_id)
            return 2
        ep_seq = ep["seq"]
        shots = C.episode_shots(con, args.episode_id)
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
    wav = os.path.join(tmp, "audio_check.wav")
    r = C.run_ffmpeg(["-i", C.abs_path(src), "-vn", "-ac", "1", "-ar", str(SR),
                      "-f", "wav", wav], cwd=C.ROOT)
    if r.returncode != 0:
        print("提音频失败:", C.tail(r.stderr, 200))
        return 2
    x = load_wav(wav)
    print("成片：%s" % src)
    print("时长 %.2f 秒（取样 %d 帧，%.0f 帧/秒）\n" % (len(x) / SR,
                                                  len(frames_of(x)), FPS))

    # 整片特征
    whole = shot_metrics(x)
    print("整片：调制 %.3f ｜浊音占比 %.3f ｜基频起伏 %.2f 半音 ｜谱平坦度 %.3f "
          "｜%.1f dBFS" % (whole["mod"], whole["voiced"], whole["f0_var"],
                          whole["flat"], whole["dbfs"]))
    print()

    pos, marks, verdicts = 0, [], []
    if shots:
        # ⚠️ 时间轴口径别混：`x` 是**采样点**数组，`spec` 才是**帧**。
        # 第一版拿"帧号"去切采样点数组（5 秒 × 62.5 帧/秒 = 312 < 2048 个采样点），
        # 于是每一镜都被 `len(seg) < WIN*2` 静默跳过——**报表是空的，却没有任何报错**。
        # 现在一切按**秒**算，最后再换算成采样点/帧号。
        total_shot_s = sum((C.duration(s["video_path"], cwd=C.ROOT)
                            if s["video_path"] else 0) or s["shot_seconds"]
                           for s in shots)
        audio_s = len(x) / float(SR)
        if abs(total_shot_s - audio_s) > 1.0:
            print("⚠️ 各镜时长之和 %.2fs 与音频实际 %.2fs 差 %.2fs——"
                  "对不齐的镜会切错段，先查明原因\n"
                  % (total_shot_s, audio_s, abs(total_shot_s - audio_s)))
        print("%-5s %8s %8s %8s %8s %8s %8s %s"
              % ("镜", "调制2-8Hz", "浊音占比", "浊音段数", "音节率/s", "基频起伏",
                 "dBFS", "判定"))
        print("-" * 92)
        acc = 0.0
        for s in shots:
            # 用**素材实测时长**而不是分镜计划时长：成片是这些素材直接拼的，
            # 而实测普遍比计划长 0.06–0.09 秒（5.06 vs 5.0）。按计划时长切，
            # 误差会**逐镜累积**——全片累计漂到 1.24 秒（实测），后面的镜就切错段了。
            dur = C.duration(s["video_path"], cwd=C.ROOT) if s["video_path"] else -1
            if dur <= 0:
                dur = s["shot_seconds"]
            a = int(round(acc * SR))
            b = int(round((acc + dur) * SR))
            acc += dur
            seg = x[a:b]
            if len(seg) < WIN * 2:
                print("S%02d   （片长不足，跳过）" % s["shot_seq"])
                continue
            m = shot_metrics(seg)
            verdicts.append((s["shot_seq"], m))
            marks.append(min(acc / audio_s, 1.0))
            print("S%02d   %8.3f %8.3f %8d %8.2f %8.2f %8.1f %s"
                  % (s["shot_seq"], m["mod"], m["voiced"], m["packets"],
                     m["syl_rate"], m["f0_var"], m["dbfs"],
                     "**有人声迹象**" if m["speech"] else "环境音/无语音迹象"))
        n_sp = sum(1 for _s, m in verdicts if m["speech"])
        print("-" * 84)
        print("有语音迹象的镜头：%d / %d" % (n_sp, len(verdicts)))
        print("⚠️ 这是**信号特征**判定，不是转写：能说'像有人在说话'，"
              "说不出'说的是哪句台词'。要核对台词只能另做 ASR。")

    # 语谱图
    fr = frames_of(x)
    spec = np.abs(np.fft.rfft(fr * np.hanning(WIN)[None, :], axis=1))
    freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
    if args.zoom:
        s = next((s for s in shots if s["shot_seq"] == args.zoom), None)
        # 用镜号时间轴反推起点
        acc, start = 0.0, 0.0
        for sh in shots:
            if sh["shot_seq"] == args.zoom:
                start = acc
                break
            acc += sh["shot_seconds"]
        a, b = int(start * FPS), int((start + (s["shot_seconds"] if s else 5)) * FPS)
        rows = spectro_rows(spec[a:b], freqs)
        out = args.sheet or os.path.join(C.ROOT, "out", "E%02d_S%02d_audio.png"
                                         % (ep_seq, args.zoom))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        size = save_sheet(rows, freqs, [], out, max_w=2400)
        print("\n高清语谱图（S%02d）：%s %s" % (args.zoom, os.path.relpath(out, C.ROOT), size))
        return 0

    rows = spectro_rows(spec, freqs)
    out = args.sheet or os.path.join(C.ROOT, "out", "E%02d_audio.png" % ep_seq)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    size = save_sheet(rows, freqs, marks, out, max_w=1600)
    print("\n语谱图：%s（%s；纵轴 80Hz–8kHz 对数，红竖线=分镜边界）"
          % (os.path.relpath(out, C.ROOT), "%dx%d" % size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
