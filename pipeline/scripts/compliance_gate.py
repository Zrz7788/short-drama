# -*- coding: utf-8 -*-
r"""**出片前的合规闸门** —— 红果《动画微短剧（漫剧）内容创作建议》的表驱动落地。

## 为什么单开一个文件（而不是塞进 precheck.py）

precheck.py 的 P01–P09 查的是「**模型会不会照字面执行错**」（语义物理闸门），
本文件查的是「**平台审核会不会拒**」（合规闸门）。两类规则的修法完全不同：
前者改提示词，后者**改剧本/备案**。混在一张表里，出问题时分不清该找谁。
两者共用同一套模式：表驱动、每条记**出处**、block/review 分级。

## 数据源与边界

- `剧集/EP00x/剧本.md`（正文逐行，含对白与 [画面文字] 行）——**主数据源**。
- `data/drama.db` 的 shot / entity（prompt、on_screen_text、description 等）——存在才查。
- `剧集/EP00x/*.srt|ass` ——只做**机械**检查（空行、缺块），不做语义校对。

**判不了的绝不硬判**：立意导向、封建婚恋观、贫富对立、历史虚无……
这些要读完整剧情才能下结论，关键词必误报。它们进「人工自查清单」，
与红果原文逐条对应，出片前人过一遍。

## 用法

```bash
python pipeline/compliance_gate.py --episode-id 1          # 不调 API、不花钱
python pipeline/compliance_gate.py --episode-id 2 --json
```
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C  # noqa: E402

# GBK 控制台编不出 emoji（📋 等），会直接 UnicodeEncodeError 终止——
# HANDOFF §八 已踩过（run.py --status 被 ¥ 打挂），必须先降级处理
C.setup_console()

RULES_DOC = ("knowledge/02-模型与成本/红果内容审核标准（漫剧版全文）.md")

# ---------------------------------------------------------------- 词表
# 每条注释都标规则原文的章节，出处字段在 RULES 表里再落一次。

# §一「特殊题材：……政治、军事、外交、国家安全、统战、民族、宗教、司法、公安」
# 命中即把剧推成**重点微短剧**（广电总局统一备案＋成片审查）→ 需要特殊备案。
# 只收**题材性**词；「报警」是情节动作不是题材，收「警察/公安」这类身份词。
SPECIAL_TOPICS = ("公安", "警察", "刑警", "民警", "武警", "特警", "缉毒",
                  "法院", "法官", "检察官", "律师", "司法",
                  "军人", "军队", "部队", "军区", "军官", "士兵",
                  "外交", "国家安全局", "国安", "统战", "政治局",
                  "领导人", "主席", "总理", "部长", "市长", "书记")

# §四「禁止过度呈现血腥暴力／极端血腥引人不适／极端施虐」。
# 只收多字词，单字"血"会误伤"血色/血统"（同 P09 的教训）。
GORE = ("血腥", "血肉", "血肉模糊", "血迹", "血污", "鲜血", "流血不止", "断肢",
        "残肢", "开膛", "剖腹", "内脏", "肠子", "腐尸", "尸斑", "虐尸",
        "施虐", "凌迟", "碎尸", "分尸", "自残", "自杀过程", "自尽")

# §三「宣扬封建迷信／法术玄学」——只收**重度**迷信与玄学词，
# 「鬼故事/悬疑氛围」不算宣扬迷信，交给人工清单。
SUPERSTITION = ("封建迷信", "邪祟", "通灵", "招魂", "养小鬼", "下蛊",
                "符箓", "阵法害人", "风水阵", "改命", "改命格", "逆天改命",
                "投胎转世", "灵魂附身", "阴曹地府", "算命改运")

# §五「禁止呈现低俗色情内容」——关键词只能兜最露骨的，擦边的进人工清单。
PORN = ("裸体", "全裸", "性交", "做爱", "招嫖", "嫖娼", "卖淫", "性交易",
        "情趣", "色情网站", "性侵", "强奸")

# §七「禁止呈现未成年负面内容」——未成年人 + 违规行为组合判，
# 单独出现「孩子/儿童」不判（正常剧情需要）。
MINOR_WORDS = ("未成年", "儿童", "小学生", "初中生", "孩子")
MINOR_ACTS = ("抽烟", "酗酒", "喝酒", "赌博", "吸毒", "斗殴", "自残",
              "自杀", "直播打赏", "遗弃", "虐待", "欺凌")

# §二「显著使用脏话／持续侮辱」——只收明显脏话与极端侮辱场景词。
INSULT = ("狗链", "狗笼", "逼", "他妈的", "操你", "贱人", "废物",
          "下跪磕头", "钻胯", "扇巴掌", "排泄物", "吃屎")

# §六「危害国家统一／领土主权／公众人物」——政治敏感词兜底。
POLITICAL = ("台独", "港独", "藏独", "疆独", "分裂国家", "颠覆",
             "国家领导人", "领导人恶搞", "领导人模仿")


def _hit(text, words):
    return sorted({w for w in words if w in text})


# ---------------------------------------------------------------- 规则
# 内容词类规则只扫 texts["script"]（剧本.md ＋ DB 镜头/实体文本）；
# 引用原文的备忘/自查文档不参与判定，避免自指误报。


def r01_special_topics(texts, ctx):
    for w in _hit(texts["script"], SPECIAL_TOPICS):
        lines = ["剧本.md:%d %s" % (i + 1, l.strip()[:50])
                 for i, l in enumerate(texts["script_lines"]) if w in l][:3]
        if not lines:
            lines = ["DB(镜头/实体文本)"]
        yield {"hit": w, "msg": "命中特殊题材关键词「%s」（%s）—— 按 §一，"
               "主要剧情涉及特殊题材即归**重点微短剧**，需广电总局统一备案＋"
               "成片审查（特殊备案）。要么改写题材，要么先办备案再出片。" %
               (w, "；".join(lines) or "实体/镜头库")}


def r02_gore(texts, ctx):
    for w in _hit(texts["script"], GORE):
        yield {"hit": w, "msg": "出现血腥/施虐词「%s」—— §四禁止『极端血腥引人不适、"
               "展示极端施虐的场景』。若为情节必需，须模糊化处理、避免过程与"
               "结果特写；改写建议：被震退/踉跄、器物碎裂等间接呈现。" % w}


def r03_superstition(texts, ctx):
    for w in _hit(texts["script"], SUPERSTITION):
        yield {"hit": w, "msg": "出现封建迷信/法术玄学词「%s」—— §三禁止『宣扬封建"
               "迷信、现代法术逆天改命』。确认是破除迷信的正向叙事还是宣扬，"
               "是宣扬即删改。" % w}


def r04_porn(texts, ctx):
    for w in _hit(texts["script"], PORN):
        yield {"hit": w, "msg": "出现低俗色情词「%s」—— §五禁止呈现低俗色情内容，"
               "命中即需删改或彻底重写该桥段。" % w}


def r05_minors(texts, ctx):
    # 组合判：未成年词 + 违规行为词出现在**同一段**，才算候选。
    sls = texts["script_lines"]
    for i, l in enumerate(sls):
        m = [w for w in MINOR_WORDS if w in l]
        a = [w for w in MINOR_ACTS if w in l]
        if m and a:
            yield {"hit": "%s+%s" % (m[0], a[0]), "msg":
                   "疑似未成年人负面情节（%s × %s，剧本.md:%d）—— §七禁止『未成年人"
                   "主动错误行为／暴力欺凌未成年』。人工确认角色是否未成年、"
                   "有无制止与批判。" % (m[0], a[0], i + 1)}


def r06_insult(texts, ctx):
    for w in _hit(texts["script"], INSULT):
        yield {"hit": w, "msg": "出现侮辱/脏话词「%s」—— §二禁止『持续/极端侮辱"
               "他人、显著使用脏话』。单次出现多为可过；密集/持续即需删改。" % w}


def r07_political(texts, ctx):
    for w in _hit(texts["script"], POLITICAL):
        yield {"hit": w, "msg": "出现政治敏感词「%s」—— §六禁止危害国家统一、"
               "领土主权，恶搞公众人物。命中即删改，无讨论余地。" % w}


def r08_subtitle_files(ctx):
    """§八『字幕缺失错乱』——只做机械检查：srt/ass 有没有空块/缺字段。"""
    d = ctx["ep_dir"]
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.srt"))):
        blocks = [b for b in re.split(r"\n\s*\n", open(p, encoding="utf-8-sig").read()) if b.strip()]
        for i, b in enumerate(blocks):
            lines = [l for l in b.splitlines() if l.strip()]
            if len(lines) < 2 or "-->" not in b:
                out.append("%s 第 %d 块疑似空/缺时间轴（%r）" %
                           (os.path.basename(p), i + 1, " ".join(lines)[:30]))
    for p in sorted(glob.glob(os.path.join(d, "*.ass"))):
        txt = open(p, encoding="utf-8-sig").read()
        n_ev = len([l for l in txt.splitlines() if l.startswith("Dialogue:")])
        if n_ev == 0:
            out.append("%s 没有任何 Dialogue 行" % os.path.basename(p))
    return out


def r08_rule(texts, ctx):
    for m in r08_subtitle_files(ctx):
        yield {"hit": "字幕文件", "msg": "%s —— §八『画面、声音、字幕存在质量缺陷…"
               "字幕缺失错乱』需避免；请人工复核该处字幕。" % m}


# RULES 表：(id, level, 名称, 出处, fn)
# level: block = 平台红线/需特殊备案，命中别花生成钱；review = 候选，人定。
RULES = [
    ("R01", "block", "特殊题材识别（需特殊备案）",
     "§一 内容题材合规及分级备案要求（特殊题材：政治、军事、…、公安）", r01_special_topics),
    ("R02", "review", "血腥/施虐词",
     "§四 禁止呈现画风观感不适（过度呈现血腥暴力/极端施虐）", r02_gore),
    ("R03", "review", "封建迷信/法术玄学",
     "§三 禁止宣扬封建迷信", r03_superstition),
    ("R04", "block", "低俗色情词",
     "§五 禁止呈现低俗色情内容", r04_porn),
    ("R05", "review", "未成年人负面情节",
     "§七 禁止呈现未成年负面内容", r05_minors),
    ("R06", "review", "侮辱/脏话",
     "§二 禁止宣扬不良价值观（持续侮辱/显著脏话）", r06_insult),
    ("R07", "block", "政治敏感/国家主权",
     "§六 禁止呈现其他不良导向问题", r07_political),
    ("R08", "review", "字幕机械缺陷",
     "§八 内容质量要求（字幕缺失错乱）", r08_rule),
]

# ---------------------------------------------------------------- 人工自查清单
# 关键词判不了的条目，逐条对着红果原文，出片前人过一遍。
MANUAL_CHECKLIST = [
    ("M01", "封面无恐怖惊悚画面/引人不适元素（如遗体特写、金属器械口部）",
     "§八 漫剧信息·封面"),
    ("M02", "封面无低俗色情、酗酒吸烟、非正当身份持枪、推广引流/二维码/营销字段",
     "§八 漫剧信息·封面"),
    ("M03", "剧名/简介与内容一致，无【热播】【爆款】营销词、无错别字、标点合规",
     "§八 漫剧信息·书名与简介"),
    ("M04", "全剧立意正向、结局向善；无低俗恶俗内核（需读完整剧情）",
     "总原则"),
    ("M05", "无宣扬不良婚恋观（出轨、一对多、非正当亲缘称呼）",
     "§二 禁止过度呈现/宣扬错误伦理观"),
    ("M06", "无美化非法借贷、金钱万能、贫富对立等错误金钱观",
     "§二 禁止过度呈现/宣扬错误金钱观"),
    ("M07", "复仇/犯罪桥段未美化极端手段、未过度细节展示犯罪过程",
     "§二 禁止宣扬暴力仇恨"),
    ("M08", "无历史虚无、民族/地域歧视、蹭社会热点舆情、恶搞公众人物",
     "§六 禁止呈现其他不良导向问题"),
    ("M09", "性擦边（暴露着装、性暗示台词/镜头语言）——关键词只能兜露骨的",
     "§五 禁止过度呈现挑逗性画面/情节"),
    ("M10", "恐怖/灵异氛围是否过度渲染（画风色调、配乐）——需看成片",
     "§四 规避呈现恶劣观感画面"),
    ("M11", "无其他平台水印/备案号、无无关广告植入",
     "§八 单集创作建议"),
    ("M12", "字幕语义正确（本机无 ASR，字幕由音频窗口推得，未做语义校验）",
     "§八 单集创作建议 + 本项目批注"),
]


# ---------------------------------------------------------------- 数据收集
def collect_texts(ep_dir):
    """聚合剧集目录文本，并区分**作用域**：

    - `script`：只有 `剧本.md`（＋DB 镜头/实体文本）——**内容判定以此为准**。
      视觉设定/大纲/自查备忘里常整段引用规则原文（"特殊题材：……公安……"），
      拿去扫会全是自指误报（实测 EP001 的过审风险自查.md 就引了原文）。
    - `all`：目录下全部 .md——只给「字幕文件」这类机械检查用。
    """
    lines, script_lines, all_text = {}, [], []
    for p in sorted(glob.glob(os.path.join(ep_dir, "*.md"))):
        name = os.path.basename(p)
        with open(p, encoding="utf-8-sig") as f:
            ls = f.read().splitlines()
        lines[name] = ls
        all_text.extend(ls)
        if name == "剧本.md":
            script_lines.extend(ls)
    return {"lines": lines, "script_lines": script_lines,
            "script": "\n".join(script_lines), "all": "\n".join(all_text)}


def db_texts(con, episode_id):
    """把该集的 shot / entity 文本也并进语料（存在才查）。"""
    chunks = []
    for r in con.execute(
            "SELECT prompt, description, action, dialogue, on_screen_text "
            "FROM shot WHERE episode_id=?", (episode_id,)):
        for v in r:
            if v:
                chunks.append(str(v))
    ep = con.execute("SELECT seq FROM episode WHERE id=?", (episode_id,)).fetchone()
    if ep:
        for r in con.execute(
                "SELECT DISTINCT e.name, e.description FROM entity e "
                "JOIN shot_entity se ON se.entity_id = e.id "
                "JOIN shot s ON s.id = se.shot_id WHERE s.episode_id=?",
                (episode_id,)):
            for v in r:
                if v:
                    chunks.append(str(v))
    return "\n".join(chunks)


def main():
    ap = argparse.ArgumentParser(description="红果审核规则·出片前合规闸门（表驱动）")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    ep = con.execute("SELECT * FROM episode WHERE id=?", (args.episode_id,)).fetchone()
    ep_seq = ep["seq"] if ep else args.episode_id
    ep_dir = os.path.join(C.ROOT, "剧集", "EP%03d" % ep_seq)
    if not os.path.isdir(ep_dir):
        print("找不到剧集目录:", ep_dir)
        return 2
    db_text = db_texts(con, args.episode_id)
    con.close()

    texts = collect_texts(ep_dir)
    texts["lines"]["DB(镜头/实体)"] = db_text.splitlines()
    texts["all"] += "\n" + db_text
    ctx = {"ep_dir": ep_dir, "episode_id": args.episode_id}

    blocks, reviews = [], []
    for rid, level, kind, src, fn in RULES:
        for item in fn(texts, ctx):
            entry = {"rule": rid, "level": level, "kind": kind, "source": src,
                     "hit": item["hit"], "msg": item["msg"]}
            (blocks if level == "block" else reviews).append(entry)

    if args.json:
        print(json.dumps({
            "episode": ep_seq,
            "blocks": blocks,
            "reviews": reviews,
            "manual_checklist": [
                {"id": i, "item": t, "source": s} for i, t, s in MANUAL_CHECKLIST],
        }, ensure_ascii=False, indent=2))
        return 1 if blocks else 0

    print("=" * 88)
    print("合规闸门（红果漫剧审核规则）· EP%03d ｜ %d 条机器规则 + %d 条人工自查"
          % (ep_seq, len(RULES), len(MANUAL_CHECKLIST)))
    print("规则原文：%s" % RULES_DOC)
    print("=" * 88)

    if blocks:
        print("\n⛔ 阻断（平台红线/需特殊备案——先解决，别花生成钱）")
        for b in blocks:
            print("  [%s] 命中「%s」" % (b["rule"], b["hit"]))
            print("        %s" % b["msg"])
            print("        出处：%s ｜ 类型：%s" % (b["source"], b["kind"]))
    if reviews:
        print("\n👁 待复核（候选，改不改由人定）")
        for b in reviews:
            print("  [%s] 命中「%s」" % (b["rule"], b["hit"]))
            print("        %s" % b["msg"])
            print("        出处：%s ｜ 类型：%s" % (b["source"], b["kind"]))

    print("\n📝 人工自查清单（关键词判不了，出片前逐条人过）")
    for i, t, s in MANUAL_CHECKLIST:
        print("  [%s] %s  （%s）" % (i, t, s))

    if not blocks and not reviews:
        print("\n✅ 机器可判条目均未命中；人工清单仍需过一遍。")
    print("\n阻断 %d 项 ／ 待复核 %d 项 ／ 人工自查 %d 项。"
          % (len(blocks), len(reviews), len(MANUAL_CHECKLIST)))
    print("⚠️ 关键词法只能兜『能从字面判定的』；立意、伦理、价值观类一律进人工清单，不硬判。")
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
