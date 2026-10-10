# -*- coding: utf-8 -*-
"""验证 ffmpeg 工具链是否真能干「视频衔接」这一段所需的活。

不满足于 --version：真的合成视频、真的抽帧、真的拼接、真的烧字幕（含中文），
并检查依赖的编码器与滤镜是否存在。

用法： python tools/verify_ffmpeg.py
退出码 0 = 全部通过。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, ".tools", "ffmpeg", "bin")
FFMPEG = os.path.join(BIN, "ffmpeg.exe")
FFPROBE = os.path.join(BIN, "ffprobe.exe")
WORK = os.path.join(ROOT, ".tools", "ffmpeg-test")

results = []


def ok(name, passed, detail=""):
    results.append((name, passed, detail))
    print("  [%s] %-46s %s" % ("OK" if passed else "!!", name, detail))


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def ff(args, **kw):
    """在 WORK 目录下调用 ffmpeg。"""
    return run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
               cwd=WORK, **kw)


def probe_duration(path):
    p = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path], cwd=WORK)
    try:
        return float((p.stdout or "").strip())
    except ValueError:
        return -1.0


def main():
    print("=== 0. 二进制 ===")
    for exe in (FFMPEG, FFPROBE):
        ok(os.path.basename(exe) + " 存在", os.path.exists(exe),
           "%.1f MB" % (os.path.getsize(exe) / 1048576.0) if os.path.exists(exe) else "缺失")
    if not os.path.exists(FFMPEG):
        return 1

    p = run([FFMPEG, "-version"])
    ver = (p.stdout or "").splitlines()[0] if p.stdout else ""
    ok("ffmpeg 可执行", p.returncode == 0, ver[:62])

    print("\n=== 1. 能力检查（衔接与字幕所需的组件）===")
    p = run([FFMPEG, "-hide_banner", "-encoders"])
    enc = p.stdout or ""
    for name, key, why in [("libx264 编码器", "libx264", "烧字幕/重编码必须"),
                           ("aac 编码器", "aac", "带音轨时需要")]:
        ok(name, key in enc, why)

    p = run([FFMPEG, "-hide_banner", "-filters"])
    filt = p.stdout or ""
    for name, key, why in [("subtitles 滤镜", " subtitles ", "字幕烧录（libass）"),
                           ("concat 滤镜", " concat ", "拼接"),
                           ("overlay 滤镜", " overlay ", "画中画/水印")]:
        ok(name, key in filt, why)

    # 注意：-f concat -i 用的是**解复用器**（输入），不是 -muxers（输出）。
    # 实测踩过：查 -muxers 会误报缺失，而功能测试其实是过的。
    p = run([FFMPEG, "-hide_banner", "-demuxers"])
    dem = p.stdout or ""
    ok("concat 解复用器", "concat" in dem, "按清单拼接（输入格式）")

    print("\n=== 2. 功能测试（真跑一遍衔接流程）===")
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)

    # 2.1 合成两段竖屏测试片（模拟相邻两个镜头）
    for i, src in enumerate(["testsrc", "smptebars"], start=1):
        r = ff(["-f", "lavfi",
                "-i", "%s=size=270x480:rate=24:duration=2" % src,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "clip%d.mp4" % i])
        ok("合成测试片段 clip%d.mp4" % i, r.returncode == 0,
           (r.stderr or "").strip()[:50])

    d1 = probe_duration("clip1.mp4")
    ok("ffprobe 读出时长", abs(d1 - 2.0) < 0.15, "%.3f 秒（预期 2.0）" % d1)

    # 2.2 抽尾帧 —— 流程第 9 步的核心动作
    r = ff(["-sseof", "-0.1", "-i", "clip1.mp4", "-vframes", "1", "-q:v", "2", "last.png"])
    ok("抽尾帧（-sseof）", r.returncode == 0 and os.path.exists(os.path.join(WORK, "last.png")),
       "%.1f KB" % (os.path.getsize(os.path.join(WORK, "last.png")) / 1024.0)
       if os.path.exists(os.path.join(WORK, "last.png")) else (r.stderr or "")[:50])

    # 2.3 清单拼接（-c copy，不重编码）
    with open(os.path.join(WORK, "list.txt"), "w", encoding="utf-8") as f:
        for i in (1, 2):
            f.write("file 'clip%d.mp4'\n" % i)
    r = ff(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"])
    ok("concat 拼接（不重编码）", r.returncode == 0, (r.stderr or "").strip()[:50])

    dj = probe_duration("joined.mp4")
    ok("拼接后时长为两段之和", abs(dj - 4.0) < 0.2, "%.3f 秒（预期 4.0）" % dj)

    # 2.4 字幕烧录：先 ASCII，再中文（中文才是真实场景）
    with open(os.path.join(WORK, "ascii.srt"), "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nhello ffmpeg\n")
    with open(os.path.join(WORK, "cjk.srt"), "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\n你到底是谁？\n")

    # 路径用相对名 + cwd，绕开 Windows 盘符冒号的转义坑
    r = ff(["-i", "joined.mp4", "-vf", "subtitles=ascii.srt",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "sub_ascii.mp4"])
    ok("字幕烧录（ASCII）", r.returncode == 0, (r.stderr or "").strip()[:60])

    r = ff(["-i", "joined.mp4", "-vf", "subtitles=cjk.srt",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "sub_cjk.mp4"])
    cjk_ok = r.returncode == 0
    ok("字幕烧录（中文）", cjk_ok,
       "ok" if cjk_ok else (r.stderr or "").strip()[:70])

    print("\n=== 3. 产物 ===")
    for n in ("clip1.mp4", "last.png", "joined.mp4", "sub_ascii.mp4", "sub_cjk.mp4"):
        p = os.path.join(WORK, n)
        if os.path.exists(p):
            print("  %-16s %8.1f KB" % (n, os.path.getsize(p) / 1024.0))

    if not cjk_ok:
        print("\n  中文失败通常不是缺滤镜，而是 libass 找不到中文字体。")
        print("  解法：加 -vf \"subtitles=cjk.srt:force_style='FontName=Microsoft YaHei'\"")
        print("        或指定 fontsdir 指向字体目录。")

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 64)
    print("共 %d 项，通过 %d 项，失败 %d 项 -> %s"
          % (len(results), len(results) - len(failed), len(failed),
             "全部通过" if not failed else "**有失败**"))
    for n, _, d in failed:
        print("  失败: %s %s" % (n, d))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
