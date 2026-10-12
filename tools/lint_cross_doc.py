# -*- coding: utf-8 -*-
r"""跨文档一致性检查（lint）：剧本 / 视觉设定 / 分镜 三份 Markdown 与数据库对齐。

## 为什么需要

实测踩过：视觉设定与分镜由**另一个会话**产出，而数据库里第 2 阶段
**独立重抽**过实体。两边没有共同的命名权威，于是漂移了：

    视觉设定.md          数据库实体
    硬纸牌脚签      vs   遗体脚趾牌
    玫瑰色痕迹      vs   棉质遗体盖单
    焚化车间        vs   殡仪馆焚化车间
    中年男主管      vs   主管（别名里有）

而且**覆盖度差很多**：文档写 6 个道具，库里有 20 个
（颌部固定金属针、整容台控制按钮……都没进文档）。

**没有这个检查，图会按一份命名出、分镜按另一份命名引用，交接处就断。**

## 判级（遵循项目三档纪律）

- **block**：同一实体两种叫法都指向它，但文档之间不一致 —— 会导致下游各信一套
- **warn**：库里实体未被文档覆盖（可能只是不必出图）、文档里的名字库中找不到

## 检查项

1. 视觉设定 的 `###` 小节 ↔ 库实体（名字 / 别名）
2. 库里每个实体 → 是否在视觉设定里出现过（名字或别名）
3. 库里每个实体 → 是否在剧本里出现过（**抓"抽出来但剧本从不提"的幽灵实体**）
4. 分镜引用的实体名 → 是否都能在库里找到
5. 视觉设定 的「跨集状态」↔ 剧本末尾的 `[连续性]` 标注

用法：
    python tools/lint_cross_doc.py --episode-id 1
    python tools/lint_cross_doc.py --episode-id 1 --json
"""
import argparse
import json
import os
import re
import sqlite3
import sys

DB = "data/drama.db"
DOC_DIR = os.path.join("剧集", "EP%03d")


def parse_headings(text, level=3):
    """取出指定层级的 Markdown 小标题（去掉圆括号里的补充说明）。"""
    out = []
    prefix = "#" * level + " "
    for ln in text.splitlines():
        if ln.startswith(prefix) and not ln.startswith(prefix + "#"):
            raw = ln[len(prefix):].strip()
            base = re.sub(r"[（(].*?[）)]", "", raw).strip()
            out.append((raw, base))
    return out


def norm(s):
    """归一化：去掉空格与常见限定词，便于宽松比较。"""
    s = re.sub(r"\s+", "", s)
    for pre in ("殡仪馆",):
        if s.startswith(pre) and len(s) > len(pre):
            s = s[len(pre):]
    return s


def main():
    ap = argparse.ArgumentParser(description="跨文档一致性检查")
    ap.add_argument("--episode-id", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ep = args.episode_id
    d = DOC_DIR % ep
    paths = {
        "剧本": os.path.join(d, "剧本.md"),
        "视觉设定": os.path.join(d, "视觉设定.md"),
        "分镜": os.path.join(d, "分镜.md"),
    }
    docs = {}
    for k, p in paths.items():
        if os.path.exists(p):
            docs[k] = open(p, encoding="utf-8").read()

    if "视觉设定" not in docs:
        print("找不到 %s，先产出视觉设定再检查" % paths["视觉设定"])
        return 1

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ents = {}
    for r in con.execute("select id, kind, name, alias, role from entity"):
        aliases = [x.strip() for x in (r["alias"] or "").split(",") if x.strip()]
        ents[r["name"]] = {"kind": r["kind"], "role": r["role"],
                           "alias": aliases, "id": r["id"]}

    blocks, warns, notes = [], [], []

    # ---------- 1) 视觉设定 小节 ↔ 库实体 ----------
    heads = parse_headings(docs["视觉设定"], 3)
    # 「造型」小节是造型不是实体，单独放行
    STYLE_KEYS = ("造型",)
    unmatched, loose = [], []
    for raw, base in heads:
        if any(k in raw for k in STYLE_KEYS):
            continue
        nb = norm(base)
        # ① 名字完全一致
        if base in ents or any(nb == norm(k) for k in ents):
            continue
        # ② 是某个实体的别名（真·同一实体）
        if any(nb == norm(a) for v in ents.values() for a in v["alias"]):
            loose.append((raw, "是别名"))
            continue
        # ③ 只是名字相似 —— **不算对上**，要报出来
        sim = [k for k in ents if norm(k) and (nb in norm(k) or norm(k) in nb)]
        if sim:
            loose.append((raw, "名字与库里的「%s」相似但不一致" % "、".join(sim[:3])))
            continue
        unmatched.append(raw)
    if unmatched:
        for u in unmatched:
            warns.append("视觉设定 的「%s」在库里找不到对应实体"
                         "（要么命名不一致，要么该实体没抽出来）" % u)
    for raw, why in loose:
        # 别名 = 同一个东西的两种叫法，**不是缺陷**。列成警告会让警告贬值
        # （"狼来了"），所以降为提示；但正文用哪个叫法仍需统一，故保留可见。
        notes.append("视觉设定 的「%s」%s（非缺陷，但提示词请统一用库里的规范名）"
                     % (raw, why))

    # ---------- 2) 库实体 → 文档覆盖 ----------
    vis_text = docs["视觉设定"]
    uncovered, covered_not_in_script = [], []
    script_text = docs.get("剧本", "")
    shot_text = docs.get("分镜", "")
    for name, v in sorted(ents.items()):
        names = [name] + v["alias"]
        in_vis = any(n and n in vis_text for n in names)
        in_script = any(n and n in script_text for n in names)
        if not in_vis:
            uncovered.append((v["kind"], v["role"], name))
        if not in_script:
            covered_not_in_script.append((v["kind"], v["role"], name))
    # background 道具不必进文档（不出图）
    hard_uncovered = [x for x in uncovered if x[1] != "background"]
    if hard_uncovered:
        warns.append("库里有 %d 个**非 background** 实体未进视觉设定：%s"
                     % (len(hard_uncovered),
                        "、".join(x[2] for x in hard_uncovered)))
    if covered_not_in_script:
        warns.append("库里 %d 个实体在剧本正文里从不出现（幽灵实体，"
                     "会白白出图）：%s"
                     % (len(covered_not_in_script),
                        "、".join(x[2] for x in covered_not_in_script)))

    # ---------- 3) 分镜引用 ↔ 库 ----------
    if shot_text:
        # ⚠️ 只数真正的镜头标题。分镜文件末尾还有「## 覆盖与节奏报告」
        #    之类的小节，早先把它也数成镜头，报出"13 镜"（实际 12）。
        shot_heads = [(r, b) for r, b in parse_headings(shot_text, 2)
                      if re.match(r"^(SHOT|镜)\s*[-_#]?\s*\d+", b, re.I)]
        extra = [r for r, b in parse_headings(shot_text, 2)
                 if not re.match(r"^(SHOT|镜)\s*[-_#]?\s*\d+", b, re.I)]
        notes.append("分镜共 %d 镜" % len(shot_heads))
        if extra:
            notes.append("分镜文件另有 %d 个非镜头小节：%s"
                         % (len(extra), "、".join(extra)))

        # 镜头总时长 vs 剧本目标时长
        durs = [float(x) for x in
                re.findall(r"(?:时长|duration)[：:\s]*([\d.]+)\s*秒?", shot_text)]
        if durs:
            tot = sum(durs)
            # ⚠️ `scene` 表**没有** episode_id 列（只有 script_id）。
            #    早先这里写 `where episode_id=?` 抛 no such column，
            #    被下面的 except 吞掉 → tgt=0 → 时长检查**静默失效**。
            #    必须经 script 连接，并只取现行稿（否则废弃稿的场次也算进来，
            #    总时长会翻倍）。异常改为显式报告，不再静默。
            tgt = None
            try:
                row = con.execute(
                    "select coalesce(sum(sc.duration_s),0) "
                    "from scene sc join script s on s.id = sc.script_id "
                    "where s.episode_id = ? and s.is_current = 1",
                    (ep,)).fetchone()
                tgt = row[0] if row else 0
            except sqlite3.Error as e:
                warns.append("无法取分集目标时长（%s）——分镜时长无法校验" % e)
            notes.append("分镜时长合计 %.1f 秒" % tot)
            if tgt:
                gap = tgt - tot
                if abs(gap) >= 3:
                    warns.append("分镜时长 %.1f 秒 与分集目标 %.1f 秒 差 %+.1f 秒"
                                 "（分镜偏短则节奏会赶，偏长则超集时长）"
                                 % (tot, tgt, gap))
                else:
                    notes.append("与分集目标 %.1f 秒相差 %+.1f 秒，在容差内"
                                 % (tgt, gap))
            elif tgt == 0:
                warns.append("现行稿没有场次时长记录，分镜时长无法校验")

        all_known = set()
        for name, v in ents.items():
            all_known.add(name)
            all_known.update(v["alias"])
        # 抓分镜里形如「实体：xxx」或『一致性主体』的字段
        refs = set()
        for m in re.finditer(r"(?:实体|主体|主体锁定)[：:]\s*([^\n]+)", shot_text):
            for part in re.split(r"[、,，/]", m.group(1)):
                part = part.strip().strip("（）()")
                if part:
                    refs.add(part)
        bad_refs = [r for r in refs
                    if r not in all_known and
                    not any(r in a or a in r for a in all_known if a)]
        if bad_refs:
            blocks.append("分镜引用了库里没有的实体名：%s"
                          % "、".join(sorted(bad_refs)))
        elif refs:
            notes.append("分镜引用的 %d 个实体名全部能在库里找到" % len(refs))

    # ---------- 4) 跨集状态 ↔ [连续性] ----------
    m = re.search(r"^##\s*跨集状态[^\n]*\n(.*?)(?=^##\s|\Z)",
                  vis_text, re.M | re.S)
    if m:
        items = [x.strip("-• \t") for x in m.group(1).splitlines()
                 if x.strip().startswith(("-", "•"))]
        notes.append("跨集状态 %d 条" % len(items))
        if script_text:
            cont = re.findall(r"\[连续性\]\s*(.+)", script_text)
            if not cont:
                warns.append("剧本里没有 [连续性] 标注，但视觉设定写了 %d 条跨集状态"
                             "——两边对不上时无法判断谁准" % len(items))
            else:
                notes.append("剧本 [连续性] %d 条" % len(cont))

    con.close()

    # ---------- 输出 ----------
    if args.json:
        print(json.dumps({"blocks": blocks, "warns": warns, "notes": notes},
                         ensure_ascii=False, indent=2))
        return 1 if blocks else 0

    print("=" * 72)
    print("跨文档一致性检查 · EP%03d" % ep)
    print("=" * 72)
    print("  脚本/视觉设定/分镜 与数据库（%d 个实体）对照\n" % len(ents))
    for n in notes:
        print("  -- %s" % n)
    print()
    if blocks:
        print("  【阻断 · 必须修】")
        for b in blocks:
            print("    ✗ %s" % b)
        print()
    if warns:
        print("  【提醒 · 需要人判断】")
        for w in warns:
            print("    ! %s" % w)
        print()
    if not blocks and not warns:
        print("  ✅ 三份文档与数据库一致")
    print("=" * 72)
    print("  阻断 %d 项，提醒 %d 项 -> %s"
          % (len(blocks), len(warns), "有阻断" if blocks else
             ("有提醒" if warns else "全部通过")))
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
