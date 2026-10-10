# -*- coding: utf-8 -*-
"""下载并校验 git-for-windows 官方 MinGit 便携版到工作区 .tools 目录。"""
import hashlib
import json
import os
import urllib.request
import zipfile

WS = os.getcwd()
TOOLS = os.path.join(WS, ".tools")
ZIP = os.path.join(TOOLS, "MinGit.zip")
DEST = os.path.join(TOOLS, "mingit")
UA = {"User-Agent": "dsh-setup", "Accept": "application/vnd.github+json"}

os.makedirs(TOOLS, exist_ok=True)

api = "https://api.github.com/repos/git-for-windows/git/releases/latest"
with urllib.request.urlopen(urllib.request.Request(api, headers=UA), timeout=30) as r:
    rel = json.loads(r.read().decode())

assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
target = next(n for n in assets if n.startswith("MinGit-") and n.endswith("-64-bit.zip")
              and "busybox" not in n)
url = assets[target]
print("版本 :", rel["tag_name"])
print("文件 :", target)
print("校验 :", "有 .sha256 资产" if (target + ".sha256") in assets else "官方未提供 .sha256")

if not os.path.exists(ZIP) or os.path.getsize(ZIP) == 0:
    got, total = 0, 0
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dsh-setup"}), timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        with open(ZIP, "wb") as w:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                w.write(chunk)
                got += len(chunk)
                if total and got % (8 << 20) < (1 << 16):
                    print("  下载 %.0f%% (%.1f/%.1f MB)" % (got * 100.0 / total, got / 1048576.0, total / 1048576.0))
    print("下载完成: %.1f MB" % (os.path.getsize(ZIP) / 1048576.0))

digest = hashlib.sha256(open(ZIP, "rb").read()).hexdigest()
print("SHA256:", digest)

if (target + ".sha256") in assets:
    with urllib.request.urlopen(urllib.request.Request(assets[target + ".sha256"],
                                                       headers={"User-Agent": "dsh-setup"}), timeout=30) as r:
        expected = r.read().decode().split()[0].strip().lower()
    print("期望  :", expected)
    print("校验结果:", "通过" if expected == digest else "**不匹配，拒绝解压**")
    if expected != digest:
        raise SystemExit(1)

if not os.path.isdir(DEST):
    print("解压中 ...")
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        bad = [n for n in names if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/")]
        if bad:
            raise SystemExit("压缩包含可疑路径，拒绝解压: %s" % bad[:5])
        z.extractall(DEST)
    print("解压完成，共 %d 个条目" % len(names))
else:
    print("目标目录已存在，跳过解压")

for rel_path in ("cmd/git.exe", "mingw64/bin/git.exe"):
    p = os.path.join(DEST, rel_path)
    print("%-26s %s" % (rel_path, "存在 %.1f MB" % (os.path.getsize(p) / 1048576.0) if os.path.exists(p) else "缺失"))
