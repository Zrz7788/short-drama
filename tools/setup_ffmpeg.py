# -*- coding: utf-8 -*-
"""下载并解压便携版 ffmpeg 到工作区 .tools/ffmpeg。

源：BtbN/FFmpeg-Builds（GitHub），带版本号的发布分支构建，GPL 版含 libx264/x265。
选它而不是 rolling master：版本可预期。选 GPL 而不是 LGPL：烧字幕要重新编码。

用法： python tools/setup_ffmpeg.py
"""
import hashlib
import json
import os
import shutil
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, ".tools")
ZIP = os.path.join(TOOLS, "ffmpeg.zip")
DEST = os.path.join(TOOLS, "ffmpeg")
TMP = os.path.join(TOOLS, "ffmpeg-tmp")

# 优先带版本号的发布分支；找不到再退回 master
PREFERRED = ["ffmpeg-n9.0-latest-win64-gpl-9.0.zip",
             "ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
             "ffmpeg-master-latest-win64-gpl.zip"]
UA = {"User-Agent": "dsh-setup", "Accept": "application/vnd.github+json"}


def human(n):
    return "%.1f MB" % (n / 1048576.0)


def main():
    os.makedirs(TOOLS, exist_ok=True)

    req = urllib.request.Request(
        "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.loads(r.read().decode())
    assets = {a["name"]: a for a in rel["assets"]}

    target = next((n for n in PREFERRED if n in assets), None)
    if not target:
        print("找不到预期资产，可用:", sorted(assets))
        return 1
    print("选中 :", target)
    print("大小 :", human(assets[target]["size"]))

    if not os.path.exists(ZIP) or os.path.getsize(ZIP) == 0:
        url = assets[target]["browser_download_url"]
        got = 0
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "dsh-setup"}),
                timeout=300) as r:
            total = int(r.headers.get("Content-Length") or 0)
            with open(ZIP, "wb") as w:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    w.write(chunk)
                    got += len(chunk)
                    if total and got % (32 << 20) < (1 << 20):
                        print("  下载 %.0f%% (%.1f/%.1f MB)"
                              % (got * 100.0 / total, got / 1048576.0, total / 1048576.0))
        print("下载完成:", human(os.path.getsize(ZIP)))
    else:
        print("已存在 zip，跳过下载")

    digest = hashlib.sha256()
    with open(ZIP, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            digest.update(b)
    print("SHA256:", digest.hexdigest())
    print("（BtbN 未随资产发布 .sha256，此值仅作记录留痕）")

    if os.path.isdir(DEST):
        print("目标目录已存在，跳过解压:", DEST)
    else:
        print("解压中 ...")
        if os.path.isdir(TMP):
            shutil.rmtree(TMP)
        os.makedirs(TMP)
        with zipfile.ZipFile(ZIP) as z:
            names = z.namelist()
            bad = [n for n in names
                   if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/")]
            if bad:
                print("压缩包含可疑路径，拒绝解压:", bad[:5])
                return 1
            z.extractall(TMP)
        print("解压 %d 个条目" % len(names))

        # zip 内层是单层目录，把它整体提上来，得到稳定的 .tools/ffmpeg/bin/ffmpeg.exe
        inner = [d for d in os.listdir(TMP) if os.path.isdir(os.path.join(TMP, d))]
        src = os.path.join(TMP, inner[0]) if len(inner) == 1 else TMP
        shutil.move(src, DEST)
        shutil.rmtree(TMP, ignore_errors=True)

    # 清理调试态可执行文件（体积大且日常用不到）
    pruned = []
    for base, _dirs, files in os.walk(DEST):
        for fn in files:
            if fn.endswith("_g.exe") or fn == "ffplay.exe":
                p = os.path.join(base, fn)
                pruned.append("%s (%s)" % (fn, human(os.path.getsize(p))))
                os.remove(p)
    if pruned:
        print("已清理:", ", ".join(pruned))

    print("\n--- 结果 ---")
    for name in ("bin/ffmpeg.exe", "bin/ffprobe.exe"):
        p = os.path.join(DEST, name.replace("/", os.sep))
        print("  %-18s %s" % (name, human(os.path.getsize(p)) if os.path.exists(p) else "缺失"))

    total = sum(os.path.getsize(os.path.join(b, f))
                for b, _d, fs in os.walk(DEST) for f in fs)
    print("  目录总计        %s" % human(total))
    print("\n下一步验证： python tools/verify_ffmpeg.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
