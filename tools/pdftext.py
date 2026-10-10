# -*- coding: utf-8 -*-
"""极简 PDF 文本提取（零依赖，只用 zlib + re）。

为什么自己写：这台机器上没有 pypdf/PyPDF2/fitz/pdfplumber，
也没有 pdftotext 或 LibreOffice。

做法：
  1. 找出所有 stream...endstream，按 FlateDecode 解压
  2. 从解压后的内容流里提取文本操作符（Tj / TJ / ' / "）的字符串
  3. **中文 PDF 多半是 CID 编码**——直接取字节是乱码，必须走 ToUnicode CMap
     把 CID 映射回 Unicode。所以先把文档里所有 CMap 的
     `beginbfchar` / `beginbfrange` 拉出来做一个全局映射表。

局限（写明了免得误用）：
  - 只处理 FlateDecode；其它滤镜（LZW/DCT/JPX）跳过
  - 多个字体各有各的 CMap 时，全局映射可能串味
    （实测本批 PDF 是单一主字体，能用）
  - 不还原排版顺序，只按内容流出现顺序拼接
  - **是"够用"，不是"准确"。要精确请装 pypdf。**
"""
import re
import sys
import zlib

STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _decode_streams(data):
    out = []
    for m in STREAM_RE.finditer(data):
        raw = m.group(1)
        try:
            out.append(zlib.decompress(raw))
        except Exception:
            try:
                out.append(zlib.decompressobj().decompress(raw))
            except Exception:
                pass
    return out


def _cmap(streams):
    """把文档里所有 ToUnicode CMap 合成一张 {cid:int -> str}。

    ⚠️ 顺序要紧：**先处理 bfrange，再处理 bfchar**，让显式映射覆盖区间映射。
    一开始写反了，而且 bfchar 的正则会把 bfrange 的 `<lo> <hi>` 也吃进去
    （实测后果：'Seedance' 被映射成 `'HFTXIV` 这种乱码）。
    """
    table = {}
    for s in streams:
        if b"beginbfchar" not in s and b"beginbfrange" not in s:
            continue
        t = s.decode("latin-1")

        # ---- 1. bfrange 的数组形式：<lo> <hi> [<u1> <u2> ...] ----
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
                             t, re.S):
            a = int(m.group(1), 16)
            us = re.findall(r"<([0-9A-Fa-f]+)>", m.group(3))
            for i, u in enumerate(us):
                try:
                    table[a + i] = bytes.fromhex(u).decode("utf-16-be", "ignore")
                except Exception:
                    pass

        # ---- 2. bfrange 的递增形式：<lo> <hi> <base> ----
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*"
                             r"<([0-9A-Fa-f]+)>(?!\s*<)", t):
            try:
                a, b, c = (int(m.group(i), 16) for i in (1, 2, 3))
            except Exception:
                continue
            if b < a or b - a > 65535:
                continue
            for i in range(a, b + 1):
                table[i] = chr(c + (i - a))

        # ---- 3. bfchar 的显式对：<cid> <unicode>，只取 beginbfchar 段内的 ----
        for seg in re.findall(r"beginbfchar(.*?)endbfchar", t, re.S):
            for a, b in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", seg):
                try:
                    table[int(a, 16)] = bytes.fromhex(b).decode("utf-16-be", "ignore")
                except Exception:
                    pass
    return table


def _textops(content):
    """产出内容流里的字符串片段（原始字节）。"""
    t = content.decode("latin-1")
    # 十六进制字符串 <...> 后跟 Tj/TJ
    for m in re.finditer(r"<([0-9A-Fa-f\s]+)>\s*(?=Tj|TJ|'|\")", t):
        yield ("hex", re.sub(r"\s", "", m.group(1)))
    # 字面量字符串 (...) 后跟 Tj/TJ，处理转义
    for m in re.finditer(r"\(((?:\\.|[^\\()])*)\)\s*(?=Tj|TJ|'|\")", t):
        yield ("lit", m.group(1))
    # TJ 数组里的十六进制项
    for arr in re.finditer(r"\[(.*?)\]\s*TJ", t, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f\s]+)>", arr.group(1)):
            yield ("hex", re.sub(r"\s", "", m.group(1)))


def extract(path):
    data = open(path, "rb").read()
    streams = _decode_streams(data)
    cmap = _cmap(streams)
    chunks = []
    for s in streams:
        if b"BT" not in s and b"Tj" not in s and b"TJ" not in s:
            continue
        for kind, val in _textops(s):
            if kind == "hex":
                if len(val) % 4 == 0 and cmap:
                    # 2 字节 CID
                    out = []
                    for i in range(0, len(val), 4):
                        cid = int(val[i:i + 4], 16)
                        out.append(cmap.get(cid, ""))
                    chunks.append("".join(out))
                else:
                    try:
                        chunks.append(bytes.fromhex(val).decode("utf-16-be", "ignore"))
                    except Exception:
                        pass
            else:
                try:
                    b = val.encode("latin-1").decode("unicode_escape").encode("latin-1")
                except Exception:
                    b = val.encode("latin-1", "ignore")
                if cmap and len(b) % 2 == 0:
                    out = []
                    for i in range(0, len(b), 2):
                        cid = (b[i] << 8) | b[i + 1]
                        out.append(cmap.get(cid, ""))
                    chunks.append("".join(out))
                else:
                    chunks.append(b.decode("latin-1", "ignore"))
    txt = "".join(chunks)
    # 清理：去掉零宽字符与多余空白
    txt = txt.replace("\x00", "").replace("\ufeff", "")
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for p in sys.argv[1:]:
        try:
            t = extract(p)
        except Exception as e:
            print("!! %s -> %s" % (p, e))
            continue
        print("=" * 78)
        print("【%s】提取 %d 字符" % (p.split("\\")[-1], len(t)))
        print("=" * 78)
        print(t[:6000])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
