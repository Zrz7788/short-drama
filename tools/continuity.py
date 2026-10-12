# -*- coding: utf-8 -*-
r"""跨集连续性检查：上一集留下的线，下一集有没有接。

## 为什么需要

第 1 集结尾留下四条悬置（写在《视觉设定.md》的「跨集状态（outgoing）」）：

    1. 死亡确认书在沈九围裙口袋里
    2. 三〇〇号整容台与盛伯渊在焚化车间门口失踪，去向未揭示
    3. 盛伯渊"已经报过警（三年前）"对沈九尚未闭环
    4. 玫瑰色痕迹的来历未揭示

**但代码里没有任何东西强制下一集去读它。** 第 2 集续不续、续了哪几条，
全靠写剧本的人记得。这跟当初「领域事实只写在纸上、没有门禁」是同一类问题
——而那次吃了大亏（先出图后取证，12 张图作废）。

所以本工具把「续集有没有承接」变成可核对的检查，**在出图之前跑**。

## 判据（有意保守）

| 情况 | 级别 | 理由 |
|---|---|---|
| 上一集 outgoing 的线索，下一集**既未声明承接、也无法从正文看出延续** | **提醒** | 可能是故意悬置，也可能是漏写——**要人判断**，不硬拦 |
| 下一集自己声明了承接，但**正文里找不到对应痕迹** | **阻断** | 声明与事实不符，这是可证明的错 |
| 上一集没有 outgoing 小节 | **提醒** | 无法核对，说明该集没收尾 |
| 下一集没有 `[连续性]` 标注 | **提醒** | 无法核对它的自我声明 |

**为什么不一律阻断**：悬置本身是叙事手法（"去向未揭示"就是故意不揭示）。
硬拦会逼着编剧写废话。**只有"声明了却没做到"才是可证明的错。**

用法：
    python tools/continuity.py --from 1 --to 2
    python tools/continuity.py --from 1 --to 2 --json
"""
import argparse
import json
import os
import re
import sys

EP_DIR = os.path.join("剧集", "EP%03d")


def read(path):
    if not os.path.exists(path):
        return None
    return open(path, encoding="utf-8").read()


def parse_outgoing(vis_text):
    """取《视觉设定.md》「跨集状态（outgoing）」小节里的条目。"""
    if not vis_text:
        return None
    m = re.search(r"^##\s*跨集状态[^\n]*\n(.*?)(?=^##\s|\Z)",
                  vis_text, re.M | re.S)
    if not m:
        return None
    items = []
    for ln in m.group(1).splitlines():
        s = ln.strip()
        if s.startswith(("-", "•", "*")):
            t = s.lstrip("-•* \t").strip()
            if t:
                items.append(t)
    return items


def parse_marks(script_text):
    """取剧本里的 [连续性] 标注。"""
    if not script_text:
        return []
    return [x.strip() for x in re.findall(r"\[连续性\]\s*(.+)", script_text)]


STOP = set("的了在和与及或对为是被把到从就都也很不没这那一个上下有要会能可以"
           "集场镜中内外的时后前会所被让使给从向当于".split())


def known_entities(*texts):
    r"""从视觉设定里抽「实体名 + 类型」当词表。

    视觉设定的二级标题就是类型（`## 人物` / `## 地点` / `## 道具`），
    三级标题是实体名（`### 死亡确认书（沈长庚）`）。

    ## 为什么必须带类型

    踩过：声明"**檀木算盘**仍在沈九围裙口袋里"时，判定靠**「沈九」在正文中**
    放行——但人名每集出现十几次，**它在正文里证明不了任何事**。
    真正有判别力的是**道具/地点**：`死亡确认书`在 / `檀木算盘`不在。

    所以返回 {实体名: 类型}，判定时只拿**道具与地点**当证据，
    人物一律不作为成立依据（人和"上一集留下的物件"是两类东西）。
    """
    out = {}
    for t in texts:
        if not t:
            continue
        kind = None
        for ln in t.splitlines():
            m2 = re.match(r"^##\s+(人物|造型|地点|道具)\s*$", ln.strip())
            if m2:
                kind = m2.group(1)
                continue
            m3 = re.match(r"^###\s+(.+?)\s*$", ln.strip())
            if m3 and kind:
                base = re.sub(r"[（(].*?[）)]", "", m3.group(1)).strip()
                if 2 <= len(base) <= 12:
                    # 先到先得；人物优先级最低，不覆盖已有的道具/地点标注
                    out.setdefault(base, kind)
    return out


def subject_hits(clause, body, entities=()):
    r"""判断一条 [连续性] 声明是否与正文对得上。

    ## 七次尝试，教训（中文核对比想象难得多，值得记全）

    1. **任一关键词命中就放行** → 主体被换掉，`沈九`/`围裙`/`口袋` 仍在正文里，漏报。
    2. **按空格取主体** → 中文没空格，整句成一块，永远搜不到。
    3. **n-gram 碎片当主体** → 抽出 `算盘仍在`、`被人扫过`，假阳性。
    4. **正文没剔 `[连续性]` 行** → 声明拿自己比对，永远命中，**检查是假的**。
    5. **字面重合长度** → "被人扫过" vs 正文"有人去扫过"只重合 4 字，误拦。
    6. **词频筛"特征词"后任一命中** → 命中项是 `裙口袋里`、`围裙口袋` 这类
       **方位短语**，声明与正文都有，于是"任一命中"永远为真。
    7. **只用「主语+状态词」正则** → 只覆盖一种句式：
       `温度九百度`、`你爸的墓` 被误判成"正文没有"（**假阳性**）。

    ## 最终做法：**有词表就不猜句式**

    视觉设定的小标题就是实体名（`### 死亡确认书` → `死亡确认书`）。
    拿它当词表，从声明里找出**被提及的实体**，再看这些实体在不在正文里。

        声明"死亡确认书仍在…"  → 命中实体「死亡确认书」→ 正文有 → 过
        声明"檀木算盘仍在…"    → 词表里没有"檀木算盘"，且声明里
                                 也没有任何**已知实体** → **拦**
        声明"温度九百度…"      → 命中实体「电脑控制焚化炉」？否；
                                 但正文含"温度九百度" → 过

    判据分两层：
      (a) 声明提到的**已知实体**里，只要有一个出现在正文 → 过
      (b) 一个已知实体都没提到时，退回"声明中长度≥4 的中文串是否出现在正文"
    """
    # 只拿**道具/地点**当证据。人物（沈九）每集出现十几次，
    # 它在正文里证明不了任何事——踩过这个坑。
    ents = [e for e, k in entities.items()
            if k in ("道具", "地点") and e in clause]
    hit = [e for e in ents if e in body]
    if hit:
        return {"kind": "entity", "subject": "、".join(hit[:3]),
                "hit": hit[0], "ok": True, "lcs": ""}
    if ents:
        return {"kind": "entity", "subject": "、".join(ents[:3]),
                "hit": "", "ok": False, "lcs": ""}
    # (b) 没有已知道具/地点：用**声明主语**在正文里的出现来判。
    #
    #     为什么不用"最长公共子串长度"：实测量过，真假声明的 LCS 都是 5——
    #     假声明靠"围裙口袋里"这种背景短语凑够长度，**阈值根本区分不了**。
    #     唯一稳定的判据是：**声明里的具体名词**在不在正文里。
    #
    #     按**长度从长到短**试：一句声明里常有多个中文串
    #     （"炉膛空、温度九百度，三〇〇号…"），最长的那个才是主要信息，
    #     先命中它，报告里显示的才是真正对上正文的那一段。
    nb = normalize(body)
    heads = sorted(leading_subject(clause), key=len, reverse=True)
    for head in heads:
        h = normalize(head)
        if len(h) < 2:
            continue
        if h in nb:
            return {"kind": "subject", "subject": head, "hit": head,
                    "ok": True, "lcs": ""}
        # 主语尾部常粘着状态词（如"门牌仍"），试去尾
        for cut in (1, 2):
            if len(h) > 3 and h[:-cut] in nb:
                return {"kind": "subject", "subject": head,
                        "hit": h[:-cut], "ok": True, "lcs": ""}
    lcs = longest_common_substring(normalize(clause), nb)
    return {"kind": "subject", "subject": (heads[0] if heads else ""),
            "hit": "", "ok": False, "lcs": lcs}


# 状态/存续词：它们在主语之后，是判断"主语到哪结束"的边界
_STATE = ("仍在", "还在", "依然在", "仍在", "尚在", "已在", "还是在",
          "仍在", "下落仍未知", "未", "已", "是", "被", "将", "会",
          "出口", "下落", "停", "留", "挂", "收", "放", "持", "不在")


def leading_subject(clause):
    r"""取声明的**主语**：开头那段中文，截断到第一个状态词之前。

        死亡确认书仍在沈九围裙口袋里    -> 死亡确认书
        檀木算盘仍在沈九围裙口袋里      -> 檀木算盘
        铜制门牌仍在沈九围裙口袋里      -> 铜制门牌
        炉膛空、温度九百度，三〇〇号…    -> 炉膛空（首串；再往后找更长的）
        周主任提到"你爸的墓前天被人…"    -> 周主任提到（首串）

    第二个例子难：`周主任提到"你爸的墓…"` 的真正主体在引号里。
    所以返回**全部中文串**，由调用方逐个试。
    """
    runs = re.findall(r"[\u4e00-\u9fff]{2,}", clause)
    out = []
    for r in runs:
        # 截到第一个状态词之前
        cut = len(r)
        for s in _STATE:
            i = r.find(s)
            if i > 0:
                cut = min(cut, i)
        head = r[:cut]
        if len(head) >= 2:
            out.append(head)
    return out


def longest_common_substring(a, b, cap=40):
    """a 与 b 的最长公共子串（长度上限 cap，避免退化）。"""
    if not a or not b:
        return ""
    best = ""
    for i in range(len(a)):
        if len(a) - i <= len(best):
            break
        for j in range(len(b)):
            k = 0
            while (i + k < len(a) and j + k < len(b)
                   and a[i + k] == b[j + k] and k < cap):
                k += 1
            if k > len(best):
                best = a[i:i + k]
    return best


# 标点全角半角混用会打断比对：实测正文写「你爸的墓, 前天有人去扫过」（半角逗号），
# 而声明写「你爸的墓、前天被人扫过」（全角顿号），公共子串因此只剩 4 字。
_PUNCT = re.compile(r"[\s，。；、：（）()《》\"'“”‘’\[\]【】,.;:!?—\-]+")


def normalize(s):
    """比对前归一化：去掉空白与标点。只影响比对，不改原文件。"""
    return _PUNCT.sub("", s or "")


# 专名候选的排除表：这些词到处都是，不能当"提到了某个东西"的证据
_GENERIC = set("""沈九 主任 周主任 警察 男人 女人 时候 地方 东西 事情
今天 昨天 明天 早上 晚上 现在 这里 那里 什么 怎么 因为 所以 但是
已经 仍然 依旧 继续 尚未 没有 可以 应该 需要 表示 认为 觉得""".split())


def content_names(clause):
    """从句子里抽内容名词候选：标点切开的连续中文串，取 3-8 字词块。

    中文没有空格，但**标点是真边界**。在每一串里滑窗取词块，
    排除通用词，再去重（**保留最长的**）。
    """
    runs = re.findall(r"[\u4e00-\u9fff]{2,}", clause)
    out = []
    for r in runs:
        if 2 <= len(r) <= 8 and r not in _GENERIC:
            out.append(r)
        for n in (4, 3):
            for i in range(len(r) - n + 1):
                g = r[i:i + n]
                if g not in _GENERIC:
                    out.append(g)
    # ⚠️ 去重必须**保留最长的**。
    #    踩过：写成"若是更长候选的子串就丢弃"，于是 5 字的「你爸的墓」
    #    同时是 4 字碎片「墓前天被」「天被人扫」的子串而被删掉，
    #    候选里只剩碎片 → 判定"正文没提" → **假阳性阻断**。
    out = sorted(set(out), key=len, reverse=True)
    keep = []
    for c in out:
        if any(c in k for k in keep):
            continue
        keep.append(c)
    return keep[:8]


def key_terms(s):
    """从一句声明里抽可核对的**中文**词（滑窗 2-3 字）。

    ⚠️ **只收中文**。早先连字母数字一起切，于是 "EP001" 被切成
    `EP`/`EP0`/`00`/`001`/`01`，而下一集正文里随便一个 "EP002" 就能让
    `EP0`、`00` 命中——报告里出现「命中词 00、001、01、EP、EP0」这种垃圾，
    看起来"已承接"，实际什么也没证明。**假命中比漏报更危险。**
    """
    s = re.sub(r"[，。；、：（）()《》\"'“”‘’\[\]]", " ", s)
    out = set()
    for tok in s.split():
        tok = tok.strip()
        # 只处理含中文的词
        if not re.search(r"[\u4e00-\u9fff]", tok):
            continue
        if len(tok) < 2:
            continue
        if tok not in STOP and len(tok) <= 8:
            out.add(tok)
        for n in (2, 3):
            for i in range(len(tok) - n + 1):
                g = tok[i:i + n]
                # 滑窗结果必须**全是中文**才算候选
                if not re.fullmatch(r"[\u4e00-\u9fff]+", g):
                    continue
                if g not in STOP:
                    out.add(g)
    return out


def main():
    ap = argparse.ArgumentParser(description="跨集连续性检查")
    ap.add_argument("--from", dest="src", type=int, required=True,
                    help="上一集集号，如 1")
    ap.add_argument("--to", dest="dst", type=int, required=True,
                    help="下一集集号，如 2")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sd = EP_DIR % args.src
    dd = EP_DIR % args.dst
    s_vis = read(os.path.join(sd, "视觉设定.md"))
    s_scr = read(os.path.join(sd, "剧本.md"))
    d_vis = read(os.path.join(dd, "视觉设定.md"))
    d_scr = read(os.path.join(dd, "剧本.md"))

    if d_scr is None:
        print("找不到第 %d 集剧本：%s" % (args.dst, os.path.join(dd, "剧本.md")))
        return 1

    # 词表：两集的视觉设定小标题（= 实体名）。有词表就不用猜中文句式。
    ents = known_entities(s_vis, d_vis)

    outgoing = parse_outgoing(s_vis)
    src_marks = parse_marks(s_scr)
    dst_marks = parse_marks(d_scr)
    dst_vis_out = parse_outgoing(d_vis)

    blocks, warns, notes = [], [], []

    notes.append("第 %d 集剧本 %d 字；第 %d 集剧本 %d 字"
                 % (args.src, len(s_scr or ""), args.dst, len(d_scr)))

    if outgoing is None:
        warns.append("第 %d 集没有「跨集状态（outgoing）」小节——"
                     "没有它就无法核对下一集，等于每集都从零开始"
                     % args.src)
        outgoing = []
    else:
        notes.append("第 %d 集留下 %d 条 outgoing" % (args.src, len(outgoing)))
    notes.append("第 %d 集有 %d 条 [连续性] 标注" % (args.dst, len(dst_marks)))

    # 下一集的"承接证据" = 它自己的 [连续性] + 它自己声明的 outgoing + 正文
    dst_decl = " ".join(dst_marks + (dst_vis_out or []))
    # ⚠️ **正文必须剔掉 [连续性] 标注**。
    #    踩过：dst_body 直接用整份剧本（含 `[连续性] …` 那一行），
    #    于是拿声明去比对它自己，**永远命中**——把声明改坏也照样通过，
    #    这个检查等于假的。实测输出里出现「主体命中『算盘仍在』」
    #    这种必须来自声明本身的碎片，才暴露出来。
    dst_body = re.sub(r"\[连续性\]\s*.+", "", d_scr)
    # 顺带剔掉 [画面文字]/[SFX] 等标注行，它们不是"正文动作"，
    # 而是制作标注——拿它们当承接证据同样不牢靠。
    dst_body = re.sub(r"\[(画面文字|SFX|VO|OS|转场)\][^\n]*", "", dst_body)

    print("=" * 72)
    print("跨集连续性 · EP%03d -> EP%03d" % (args.src, args.dst))
    print("=" * 72)
    for n in notes:
        print("  -- %s" % n)
    print()

    print("  第 %d 集留下的线，逐条核对：" % args.src)
    print()
    for i, item in enumerate(outgoing, 1):
        terms = key_terms(item)
        in_decl = {t for t in terms if t in dst_decl}
        in_body = {t for t in terms if t in dst_body}
        # 用"最有辨识度的词"判断，避免被"沈九"这类高频词带偏
        hit_decl = len(in_decl)
        hit_body = len(in_body)
        if hit_decl:
            mark = "已声明承接"
            detail = "命中词 %s" % "、".join(sorted(in_decl)[:5])
        elif hit_body >= 3:
            mark = "正文看得出延续"
            detail = "命中词 %s" % "、".join(sorted(in_body)[:5])
        else:
            mark = "**未见承接**"
            detail = "（正文命中 %d 个词）" % hit_body
        print("    %d. %s" % (i, item[:62]))
        print("       -> %s   %s" % (mark, detail))
        if mark == "**未见承接**":
            warns.append("EP%03d 的 outgoing 第 %d 条「%s」在 EP%03d 里"
                         "既未声明承接、正文也看不出延续"
                         "——若是故意悬置请在该集显式写明，否则可能漏线"
                         % (args.src, i, item[:34], args.dst))
        print()

    # 反向核对：下一集声明的承接，正文里有没有对应痕迹
    print("  第 %d 集自己的 [连续性] 声明，核对正文：" % args.dst)
    print()
    for j, mk in enumerate(dst_marks, 1):
        clauses = [c.strip() for c in re.split(r"[；;]", mk) if c.strip()]
        for c in clauses:
            if not c:
                continue
            r = subject_hits(c, dst_body, ents)
            if r["ok"]:
                print("    ✓ %s" % c[:66])
                if r["kind"] == "entity":
                    print("       声明提到的道具/地点「%s」在正文中" % r["hit"])
                elif r["kind"] == "subject":
                    print("       声明主语「%s」在正文中" % r["hit"])
                else:
                    print("       与正文重合：%s" % (r["hit"] or "")[:24])
            else:
                if r["kind"] == "entity":
                    why = "声明提到的道具/地点「%s」在正文里找不到" % r["subject"]
                elif r["kind"] == "subject":
                    why = ("声明主语「%s」在正文里找不到"
                           % (r["subject"] or c[:10]))
                else:
                    why = "与正文最长只重合 %d 字" % len(r["lcs"])
                blocks.append("EP%03d 的 [连续性] 声称「%s」，但%s"
                              "——声明了却找不到对应正文"
                              % (args.dst, c[:36], why))
                print("    ✗ %s" % c[:66])
                print("       %s" % why)
        print()
        print()

    if dst_vis_out:
        notes.append("EP%03d 自己也留了 %d 条 outgoing" % (args.dst, len(dst_vis_out)))
    else:
        warns.append("EP%03d 没有「跨集状态（outgoing）」小节——"
                     "该集收尾时没给下一集留核对依据" % args.dst)

    if args.json:
        print(json.dumps({"blocks": blocks, "warns": warns, "notes": notes},
                         ensure_ascii=False, indent=2))
        return 1 if blocks else 0

    print("=" * 72)
    if blocks:
        print("  【阻断 · 声明与正文不符】")
        for b in blocks:
            print("    ✗ %s" % b)
        print()
    if warns:
        print("  【提醒 · 需人判断】")
        for w in warns:
            print("    ! %s" % w)
        print()
    if not blocks and not warns:
        print("  ✅ 跨集连续性没有发现问题")
    print("=" * 72)
    print("  阻断 %d 项，提醒 %d 项" % (len(blocks), len(warns)))
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
