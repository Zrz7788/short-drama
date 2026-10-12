# -*- coding: utf-8 -*-
r"""从已取的资料里搜关键词，给出**带出处**的证据片段。

用法：python tools/research_grep.py "embalming table" "preparation room" ...
不带参数则跑一套针对 Q-01..Q-09 的默认查询。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, ".tools", "research")

# 针对第 1 集那 9 条问题的检索式
QUERIES = {
    "Q-01 空间：存放 vs 化妆是否分开": [
        "preparation room", "morgue", "separate room", "refrigeration room",
        "cold room", "holding room", "整容室", "冷藏", "遗体存放"],
    "Q-02 载体：作业台 vs 转运推车": [
        "embalming table", "preparation table", "operating table",
        "gurney", "trolley", "stainless steel table", "整容台", "推车"],
    "Q-03 动作：下颌闭合怎么处理": [
        "mouth", "jaw", "mandible", "suture the mouth", "mouth former",
        "needle injector", "下颌", "口腔"],
    "Q-04 动作：唇部怎么上妆": [
        "lip", "lipstick", "cosmetic", "wax", "rouge", "唇", "口红"],
    "Q-05 道具：遗体腕带": [
        "identification tag", "wristband", "bracelet", "toe tag", "band",
        "腕带", "标识带", "手环"],
    "Q-06 道具：紧急按钮": ["emergency", "call button", "alarm", "紧急", "按钮"],
    "Q-07 道具：工作编号签牌": ["badge", "tag", "nameplate", "工牌", "签牌", "编号"],
    "Q-08 设备：焚化炉与倒计时": [
        "cremator", "retort", "furnace", "cremation", "countdown",
        "焚化炉", "火化炉", "倒计时"],
    "Q-09 文件：死亡确认与签字权限": [
        "death certificate", "authorization", "permit", "signature",
        "next of kin", "死亡证明", "签字", "家属"],
}


def load():
    docs = {}
    if not os.path.isdir(SRC):
        return docs
    for fn in sorted(os.listdir(SRC)):
        if fn.endswith(".txt"):
            with open(os.path.join(SRC, fn), encoding="utf-8") as f:
                docs[fn[:-4]] = f.read()
    return docs


def hit_lines(text, terms, width=190):
    out = []
    for ln in text.splitlines():
        low = ln.lower()
        for t in terms:
            if t.lower() in low:
                s = ln.strip()
                if len(s) < 25:
                    continue
                out.append((t, s[:width]))
                break
    return out


def main():
    docs = load()
    if not docs:
        print("没有资料，先跑 tools/research_fetch.py")
        return 2
    print("资料：%s\n" % "、".join(docs))

    if len(sys.argv) > 1:
        terms = sys.argv[1:]
        print("检索：%s\n" % " / ".join(terms))
        for name, text in docs.items():
            hs = hit_lines(text, terms)
            if hs:
                print("── %s（%d 处）" % (name, len(hs)))
                for t, s in hs[:25]:
                    print("   [%s] %s" % (t, s))
                print()
        return 0

    for q, terms in QUERIES.items():
        print("=" * 78)
        print(q)
        print("=" * 78)
        total = 0
        for name, text in docs.items():
            hs = hit_lines(text, terms)
            if hs:
                total += len(hs)
                print("── %s（%d 处）" % (name, len(hs)))
                for t, s in hs[:8]:
                    print("   %s" % s)
        if total == 0:
            print("   （无命中——这一条在已有资料里查不到，不要编）")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
