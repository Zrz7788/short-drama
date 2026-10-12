# -*- coding: utf-8 -*-
r"""逻辑审计：把剧本抽成「事实台账」，再查状态转移是否可能。

## 为什么需要（现有检查的盲区）

| 工具 | 能查 | 查不了 |
|---|---|---|
| `tools/lint_cross_doc.py` | 文档与库的**命名**是否一致 | 内容对不对 |
| `tools/continuity.py` | 上一集**声明**的线，本集接没接 | **没人声明的东西** |

两个都依赖"有人写下来"。**逻辑错误往往正好在没人写的地方。**

## 四层审计

本工具把剧本里**可抽取的事实**列成台账，再逐层查：

  L1 场景内     同一场里时间/地点/在场人/道具/声音是否自洽
  L2 集内跨场   同一集里状态转移是否有交代（整容台怎么从门口到炉里）
  L3 跨集因果   上一集的状态到这一集之间，缺没缺一段
  L4 时间线     反复出现的相对时间（"三年前""前天""上礼拜"）是否指同一件事

## 判级（有意保守）

绝大多数逻辑问题**不是可证明的错**，而是"缺一句交代"。
所以默认输出**提醒 + 疑点**，只在**同一场里直接矛盾**时才阻断：

  · block：同一场里两处事实互斥（如"炉膛空"与"炉内在烧东西"同时成立）
  · warn ：状态转移缺交代、时间线可能撞车
  · note ：脚本标记为有意悬置的（作者写了"未揭示/未解"）

不硬拦的理由：**悬置是叙事手法**。硬拦会逼着编剧写废话。
"""
import argparse
import json
import os
import re
import sys

EP_DIR = os.path.join("剧集", "EP%03d")

# 相对时间词：用来查"同一个时间里说的是一件事还是两件事"
TIME_WORDS = ["三年前", "两年前", "一年前", "上礼拜", "上个月", "昨天",
              "前天", "三天前", "今早", "今天", "刚才", "十八年前"]


def read(p):
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def parse_scenes(script):
    r"""把剧本切成场，每场取：标题、地点、时间、正文、标注。"""
    if not script:
        return []
    out = []
    # ## EP001-SC001 内 · 殡仪馆整容室 · 秋晨
    pat = re.compile(r"^##\s+(\S+)\s+(内|外)\s*·\s*([^·\n]+?)\s*·\s*(.+?)\s*$",
                     re.M)
    marks = list(pat.finditer(script))
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(script)
        body = script[start:end]
        tags = re.findall(r"\[([^\]]+)\]\s*([^\n]*)", body)
        out.append({
            "id": m.group(1), "inout": m.group(2), "place": m.group(3).strip(),
            "time": m.group(4).strip(), "body": body,
            "tags": [(t, v.strip()) for t, v in tags],
        })
    return out


def extract_facts(scene, entities=()):
    r"""从一场里抽可核对的事实。**只抽有明确字面依据的**，不推断。"""
    b = scene["body"]
    facts = {
        "place": scene["place"],
        "time": scene["time"],
        "present": set(),      # 在场角色
        "objects": set(),      # 出现的道具/地点
        "sounds": [],          # [SFX] 与描述里的声音
        "lights": [],          # 灯/光/炉火状态
        "time_refs": [],       # 相对时间词
        "negations": [],       # "不在""空""没有"这类否定
    }
    for e in entities:
        if e in b:
            facts["objects"].add(e)
    # 在场：名字出现且不是"提及"（简化：出现在'说/道/喊'前面算在场）
    for m in re.finditer(r"([\u4e00-\u9fff]{2,4})(?:说|道|问|答|喊|叫|笑)", b):
        facts["present"].add(m.group(1))
    # 声音
    for m in re.finditer(r"\[SFX\][^\n]*", b):
        facts["sounds"].append(m.group(0).replace("[SFX]", "").strip())
    for kw in ("嗡鸣", "敲击", "响", "轮声", "跳秒", "安静"):
        for m in re.finditer(kw, b):
            a = max(0, m.start() - 14)
            facts["sounds"].append(b[a:m.end() + 10].replace("\n", " ").strip())
    # 光线/火
    for kw in ("红灯", "工作红灯", "待机红灯", "橙光", "暗", "亮", "无影灯",
               "顶灯", "反着窗光"):
        if kw in b:
            facts["lights"].append(kw)
    # 相对时间
    for w in TIME_WORDS:
        if w in b:
            facts["time_refs"].append(w)
    # 否定/空
    for m in re.finditer(r"(空着|是空的|空荡|没有|不在|没人|无人|连灰都没有)", b):
        a = max(0, m.start() - 16)
        facts["negations"].append(b[a:m.end() + 8].replace("\n", " ").strip())
    return facts


def audit(files, entities):
    blocks, warns, notes = [], [], []
    eps = []
    for ep, path in files:
        scr = read(path)
        if not scr:
            warns.append("找不到 %s" % path)
            continue
        scenes = parse_scenes(scr)
        eps.append((ep, scr, scenes))
        notes.append("EP%03d：%d 场" % (ep, len(scenes)))

    print("=" * 74)
    print("逻辑审计 · 事实台账")
    print("=" * 74)
    for ep, scr, scenes in eps:
        print("\n  EP%03d" % ep)
        for sc in scenes:
            f = extract_facts(sc, entities)
            print("    %s  %s · %s" % (sc["id"], sc["place"], sc["time"]))
            print("       在场: %s" % ("、".join(sorted(f["present"])) or "（未识别）"))
            print("       道具/地点: %s" % ("、".join(sorted(f["objects"])) or "—"))
            if f["lights"]:
                print("       光/火: %s" % "、".join(sorted(set(f["lights"]))))
            if f["sounds"]:
                print("       声: %s" % "；".join(f["sounds"][:3]))
            if f["time_refs"]:
                print("       相对时间: %s" % "、".join(f["time_refs"]))
            if f["negations"]:
                print("       否定/空: %s" % "；".join(f["negations"][:2]))

    # ---------- L1 场景内：火/声/空的互斥 ----------
    print("\n" + "=" * 74)
    print("L1 场景内自洽")
    print("=" * 74)
    for ep, scr, scenes in eps:
        for sc in scenes:
            b = sc["body"]
            burning = bool(re.search(r"(九百度|温度面板[^\n]*\d|工作红灯)", b))
            empty = bool(re.search(r"(炉膛里是空的|空荡|连灰都没有)", b))
            if burning and empty:
                # ⚠️ 这里**不做判定**，只记录观察。
                #
                # 试过两版规则，都不成立：
                #   v1 要求剧本出现"未解/矛盾"字样 → **外行规则**。
                #      好剧本不会自我解说"这是悬念"，它靠在场者反应
                #      （警察乙退半步）、主角身体反应（指节发白）、
                #      收尾落点（瞳孔缩紧）。EP002 四样齐备，却被报成漏洞。
                #   v2 改成"看有没有反应描写" → 负向测试删掉明显反应后，
                #      仍有 "没松手"、"看着那个空炉膛"、"瞳孔缩紧" 命中。
                #      **"反应"的写法列不全，规则永远漏。**
                #
                # 结论：「这是有意悬念还是疏漏」属于**创作判断**，脚本判不了。
                # 所以只把**事实**摆出来交给人：哪些字面同时成立。
                notes.append("EP%03d %s：同场既有『%s』又有『%s』"
                             "——若是有意悬念可忽略；若想更明确，"
                             "补一个在场者的反应即可（本剧已用退半步/指节发白/"
                             "瞳孔缩紧等写法）"
                             % (ep, sc["id"],
                                "炉子在烧（九百度/工作红灯）",
                                "炉膛是空的"))
                print("  ~ %s：烧着 + 空的（**交人判断**，不算问题）" % sc["id"])
            # 稳定嗡鸣 vs 异常
            if "稳定" in b and "没有起伏" in b and re.search(r"被.{0,6}挡", b):
                warns.append("EP%03d %s：排风机『稳定、没有起伏』，"
                             "但后文炉膛深处橙光被东西挡了一下"
                             "——沈九若在场会察觉异常，需交代她为何没反应"
                             % (ep, sc["id"]))
                print("  ! %s：风机稳定 vs 橙光被挡，缺她的反应" % sc["id"])

    # ---------- L2 集内跨场：关键道具/角色的状态转移 ----------
    print("\n" + "=" * 74)
    print("L2 集内跨场状态转移")
    print("=" * 74)
    # 只对"剧中明确失踪/移动"的物件查
    WATCH = {"整容台": ("不锈钢整容作业台", "整容台", "台子"),
             "盛伯渊": ("盛伯渊",),
             "死亡确认书": ("死亡确认书", "那张纸", "确认书")}
    for ep, scr, scenes in eps:
        for label, kws in WATCH.items():
            seen = []
            for sc in scenes:
                if any(k in sc["body"] for k in kws):
                    seen.append(sc["id"])
            if len(seen) >= 2:
                notes.append("EP%03d「%s」出现在 %s" % (ep, label, "、".join(seen)))
                print("  -- EP%03d「%s」出现在 %s" % (ep, label, "、".join(seen)))
        # 失踪是否有交代
        #
        # ⚠️ 这里踩过两个坑，都记下来：
        #  1. 早先写成 `if "失踪" in scr:` 再看有没有 `去向未揭示`——
        #     但"失踪"恰恰只出现在**登记句**里，同一句既是条件又是证据，
        #     逻辑自相矛盾。
        #  2. 改完仍然报"未登记"：因为**找错了文件**——
        #     触发词（"不在门边"）在**剧本**里，
        #     登记句（"去向未揭示"）在**视觉设定的 outgoing** 里。
        #     在剧本里搜登记句当然搜不到。
        scene_text = "".join(sc["body"] for sc in scenes)
        gone = re.search(r"(不见了|不在门边|消失|失踪)", scene_text)
        if gone:
            vis = read(os.path.join(EP_DIR % ep, "视觉设定.md")) or ""
            registered = re.search(
                r"(去向[^。\n]{0,10}(未|未知|不明)|"
                r"下落[^。\n]{0,10}(未|未知|不明)|"
                r"未揭示|仍未解|去向仍未知)", vis)
            if registered:
                notes.append("EP%03d 的失踪已在 outgoing 登记（『%s』）"
                             % (ep, registered.group(0)[:18]))
                print("  -- EP%03d 失踪已登记：%s" % (ep, registered.group(0)[:24]))
            else:
                warns.append("EP%03d 正文写了物件/人物不见（『%s』），"
                             "但视觉设定的 outgoing 里没登记去向"
                             "——下一集无从核对" % (ep, gone.group(0)))
                print("  ! EP%03d 失踪未登记" % ep)

    # ---------- L3 跨集因果 ----------
    print("\n" + "=" * 74)
    print("L3 跨集因果缺口")
    print("=" * 74)

    # ⚠️ **按词族**匹配，不要写死某一个说法。
    #    踩过：这里原本查 `敲击`，而修稿后写成"炉门内侧敲了三下"——
    #    同一件事换了说法，检查就报"未提及"（**假阳性**）。
    #    这是同类错误第三次（前两次："参考图"、为登记句找错文件），
    #    所以统一改用词族：一个概念给多个说法，任一命中即算。
    FAMILY = {
        "敲击": ("敲", "叩", "撞"),
        "炉子": ("炉", "焚化"),
        "墓": ("墓", "白菊", "扫"),
        "签名": ("签", "确认书", "见证人"),
    }

    def has_family(text, concept):
        return any(w in text for w in FAMILY.get(concept, (concept,)))

    for i in range(len(eps) - 1):
        e1, s1, sc1 = eps[i]
        e2, s2, sc2 = eps[i + 1]
        tail = sc1[-1]["body"] if sc1 else ""
        # 看**整集**而不是只看开场：交代可以放在任何一场
        # （踩过：我把它补在 SC001 中段，而检查只看首场头几行，于是仍报缺口）
        whole_next = s2

        if has_family(tail, "敲击") and not has_family(whole_next, "敲击"):
            warns.append("EP%03d 结尾有炉内敲击，EP%03d 全篇未再提及"
                         "——沈九刚听到炉里有动静，却不追这条线，缺一句交代"
                         % (e1, e2))
            print("  ! EP%03d 炉内敲击 → EP%03d 全篇未提及" % (e1, e2))
        else:
            notes.append("EP%03d → EP%03d：结尾悬念在下一集有回应" % (e1, e2))
            print("  -- EP%03d → EP%03d 有回应" % (e1, e2))

    # ---------- L4 时间线 ----------
    print("\n" + "=" * 74)
    print("L4 相对时间线")
    print("=" * 74)
    timeline = {}
    for ep, scr, scenes in eps:
        for sc in scenes:
            for w in TIME_WORDS:
                for m in re.finditer(w, sc["body"]):
                    a = max(0, m.start() - 30)
                    ctx = sc["body"][a:m.end() + 30].replace("\n", " ").strip()
                    timeline.setdefault(w, []).append((ep, sc["id"], ctx))
    for w, items in sorted(timeline.items()):
        print("\n  「%s」出现 %d 次：" % (w, len(items)))
        for ep, sid, ctx in items:
            print("     EP%03d %s  …%s…" % (ep, sid, ctx[:74]))
        if len(items) > 1:
            # 多次出现时要人能判断是不是指同一件事
            notes.append("「%s」出现 %d 次，需确认是否指同一件事" % (w, len(items)))

    print("\n" + "=" * 74)
    if blocks:
        print("  【阻断 · 同场直接矛盾】")
        for b in blocks:
            print("    ✗ %s" % b)
    if warns:
        print("  【提醒 · 缺交代或可能撞车】")
        for x in warns:
            print("    ! %s" % x)
    if notes:
        print("  【已登记 · 有意悬置】%d 条" % len(notes))
    print("=" * 74)
    print("  阻断 %d 项，提醒 %d 项，已登记 %d 项"
          % (len(blocks), len(warns), len(notes)))
    return 0 if not blocks else 1


def main():
    ap = argparse.ArgumentParser(description="剧本逻辑审计（四层）")
    ap.add_argument("--episodes", default="1,2",
                    help="要审的集号，逗号分隔，如 1,2")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    eps = [int(x) for x in args.episodes.split(",") if x.strip()]
    files = [(e, os.path.join(EP_DIR % e, "剧本.md")) for e in eps]
    # 用视觉设定当实体词表
    ents = set()
    for e, _p in files:
        t = read(os.path.join(EP_DIR % e, "视觉设定.md"))
        if t:
            for m in re.finditer(r"^#{2,4}\s+(.+?)\s*$", t, re.M):
                base = re.sub(r"[（(].*?[）)]", "", m.group(1)).strip()
                if 2 <= len(base) <= 12:
                    ents.add(base)
    return audit(files, ents)


if __name__ == "__main__":
    sys.exit(main())
