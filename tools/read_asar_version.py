# -*- coding: utf-8 -*-
r"""从 app.asar 里读 DSH 桌面版的内置核心版本。

**为什么必须读它而不是用 `dsh --version`**：插件 README 明确警告——
「Desktop 桌面版必须确认其**内置 DSH 核心版本**（桌面 App 的「关于」页），
不要拿独立 CLI 的版本代替桌面 App 的内核做判断」。
装错插件版本会让 DSH **启动失败**。

asar 是「8 字节头 + JSON 目录 + 拼接的文件数据」的简单容器，可以直接解析。
"""
import json
import os
import struct
import sys

ASAR = r"G:\deepseek\resources\app.asar"


def read_asar_header(path):
    with open(path, "rb") as f:
        head = f.read(16)
        # asar: uint32 x4 (4, header_size, header_str_size, json_size)，小端
        _a, _b, _c, json_size = struct.unpack("<IIII", head)
        f.seek(16)
        raw = f.read(json_size)
        # 头里前 8 字节是 pickle 协议标记，JSON 从第 8 字节开始（按 pickle 串长）
        # 稳妥做法：找第一个 '{'
        i = raw.find(b"{")
        return json.loads(raw[i:].decode("utf-8", "replace"))


def walk(node, prefix="", out=None, want=("package.json",)):
    if out is None:
        out = []
    for name, child in (node.get("files") or {}).items():
        p = prefix + "/" + name
        if child.get("files"):
            walk(child, p, out, want)
        elif name in want:
            out.append((p, child.get("size"), child.get("offset")))
    return out


def extract(path, offset, size):
    with open(path, "rb") as f:
        # offset 相对头部数据区起点；实际文件偏移 = 16 + json_size + offset
        head = f.read(16)
        _a, _b, _c, json_size = struct.unpack("<IIII", head)
        f.seek(16 + json_size + int(offset))
        return f.read(int(size))


def main():
    if not os.path.exists(ASAR):
        print("找不到：%s" % ASAR)
        return 1
    print("解析 %s（%.1f MB）" % (ASAR, os.path.getsize(ASAR) / 1048576.0))
    root = read_asar_header(ASAR)

    # 只找顶层与一层的 package.json，避免 node_modules 噪音
    hits = walk(root, "", None, want=("package.json",))
    shallow = [h for h in hits if h[0].count("/") <= 3]
    print("\n=== 候选 package.json（浅层 %d 个）===" % len(shallow))
    found = []
    for p, size, off in sorted(shallow, key=lambda x: x[0].count("/")):
        try:
            data = extract(ASAR, off, size)
            j = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            continue
        name, ver = j.get("name"), j.get("version")
        if not ver:
            continue
        found.append((p, name, ver, j))
        print("  %-46s %-34s %s" % (p, str(name), ver))

    print("\n=== 判定 ===")
    # 桌面 App 自身的版本最可靠
    for p, name, ver, j in found:
        if p == "/package.json" or p.count("/") == 1:
            print("  桌面 App: %s %s" % (name, ver))
            print("  → 内置内核若与 CLI 同为 0.2.0-rc.2，则该装插件 0.7.2")
    # 找 dsh 核心包
    for p, name, ver, j in found:
        if name and "dsh" in str(name) and "core" in str(name).lower():
            print("  内核包  : %s %s" % (name, ver))

    print("\n=== 全部含 dsh / deepseek 的浅层包 ===")
    for p, name, ver, j in found:
        if name and ("dsh" in str(name).lower() or "deepseek" in str(name).lower()):
            print("  %-42s %s" % (name, ver))
    return 0


if __name__ == "__main__":
    sys.exit(main())
