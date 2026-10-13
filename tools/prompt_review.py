# -*- coding: utf-8 -*-
r"""图片提示词审查：**出图之前**，对着规格逐项查漏。

## 为什么需要（今天亏的钱全在这）

今天出的图里，反复出现同一类错误——**东西生成了，没人对照规格检查**。
而这五处**全都能在出图之前审出来，一分钱不花**：

  塑形器画成刀片    实体描述把场景道具写进了人物参考
  嘴唇画成嘟嘴      用"效果描述"替换了"具体描述"
  手术帽没戴        视觉设定写了、实体描述没写（两条线各写一半）
  工牌位空着        需要遮挡的地方被"删掉"而不是"替换"
  三间房不像一栋楼   管线只有"房间"，没有"建筑"

## 审什么（每条都对应一次真实踩坑）

  C1 实体描述 / style_note 里有元信息、内部编号、品牌名
  C2 有否定式（项目结论：否定约束对画面无效，要改肯定式）
  C3 视觉设定的「识别锚点」在实体描述里缺失  ← 手术帽那次
  C4 domain_fact 为空 / 指向不存在的编号 / 指向"假设·待验证"
  C5 场景类：建筑公共元素（门/地脚线/天花/灯具/光色）是否写明
  C6 人物类：描述里混入了场景道具（嘴里的塑形器、白单、脚趾牌）
  C7 该"替换成纯色空白"的位置，是不是被删空了

## 用法

    python tools/prompt_review.py --episode-id 1              # 审全部三类
    python tools/prompt_review.py --episode-id 1 --phase props
    python tools/prompt_review.py --episode-id 1 --json
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C          # noqa: E402
from pipeline import stage_entities as SE  # noqa: E402

ROOT = C.ROOT
PROJECT_MD = os.path.join(ROOT, "knowledge", "03-项目档案", "殡仪馆的阿九.md")

# 元信息 / 内部术语 / 品牌名——这些不该出现在发给模型的提示词里
META_TERMS = ("领域事实缺失", "待补", "TODO", "FIXME", "见《", "见视觉设定",
              "备注：", "说明：", "注意：")
INTERNAL_IDS = re.compile(r"\bF-\d{2}\b")
BRANDS = ("美团", "饿了么", "大众点评", "抖音", "微信", "支付宝", "淘宝",
          "京东", "拼多多", "顺丰", "华为", "小米", "苹果", "耐克", "阿迪达斯")
# 否定式（对画面无效，项目已有结论）
NEGATIONS = ("不得", "不要有", "不能出现", "禁止出现", "不应有", "别出现",
             "不可有", "避免出现")

# 场景道具：不该出现在**人物**参考里（它们属于镜头提示词）
SCENE_PROPS_IN_CHARACTER = ("塑形器", "白单", "脚趾牌", "盖单", "签字板",
                            "死亡确认书", "倒计时牌", "记录板")

# 建筑公共元素：场景描述里应当写到的公共项
ARCH_ELEMENTS = {
    "门": ("门", "门口", "门框", "防火门", "平开门"),
    "地面材质": ("地面", "地板", "地砖", "水泥", "环氧", "瓷砖"),
    "天花": ("天花", "顶面", "吊顶", "顶棚", "梁"),
    "灯具型式": ("灯", "荧光", "无影灯", "顶灯", "射灯"),
    "墙面材质": ("墙面", "墙", "瓷砖", "涂料", "金属板", "耐火"),
    "踢脚线": ("踢脚", "地脚线", "踢脚线"),
    "光色": ("冷白", "暖白", "色温", "光色", "昏暗", "明亮"),
}


def parse_visual_spec(path):
    """从《视觉设定.md》抽每个实体的小节，返回 {名字: 正文}。"""
    if not os.path.exists(path):
        return {}
    t = open(path, encoding="utf-8").read()
    out, name, buf = {}, None, []
    for ln in t.splitlines():
        if ln.startswith("### "):
            if name:
                out[name] = "\n".join(buf).strip()
            raw = ln[4:].strip()
            name = re.sub(r"[（(].*?[）)]", "", raw).strip()
            buf = []
        elif ln.startswith("## "):
            if name:
                out[name] = "\n".join(buf).strip()
            name, buf = None, []
        elif name:
            buf.append(ln)
    if name:
        out[name] = "\n".join(buf).strip()
    return out


def spec_anchors(section):
    """从视觉设定小节里抽**只该看外观**的两行。

    ⚠️ **不收「本集状态」**。上一版把它也拿去比，报出
    「主管 本集状态里找不到：走过水渍地面、不抬头、对沈九的质问不回应」——
    **那些是剧情动作，本来就不该进人物描述**（人物描述写相貌与妆造，
    "不抬头""递给沈九"归分镜管）。同理「SC002 由沈九从推把上取下」
    是动作，不是外观。

    误报会让审查器失去信用（今天已在 lint 上踩过），所以这里宁可少报。
    """
    out = []
    for ln in (section or "").splitlines():
        s = ln.strip().lstrip("-•* ").strip()
        for key in ("识别锚点", "连续性锁"):
            if s.startswith(key):
                v = s[len(key):].lstrip("：: ").strip()
                if v:
                    out.append((key, v))
    return out


def anchor_items(text):
    """把锚点文本切成可逐项核对的短语。"""
    parts = re.split(r"[；;，,、]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def check_entity(e, facts, spec):
    """审一条实体。返回 (blocks, warns, notes)。"""
    blocks, warns, notes = [], [], []
    name = e["name"]
    desc = e["description"] or ""
    note = e["style_note"] or ""
    blob = desc + "\n" + note
    tag = "[%s]%s" % (e["kind"], name)

    # C1 元信息 / 内部编号 / 品牌名
    for t in META_TERMS:
        if t in blob:
            blocks.append("%s 描述里有元信息「%s」——那是给人看的备注，"
                          "会被原样发给模型。删掉或改成可见描述。" % (tag, t))
    for m in set(INTERNAL_IDS.findall(blob)):
        blocks.append("%s 描述里有内部编号「%s」——领域事实编号是我们的"
                      "内部引用，不该出现在提示词里。" % (tag, m))
    for b in BRANDS:
        if b in blob:
            blocks.append("%s 描述里有真实品牌名「%s」——"
                          "模板整段发给模型，等于把品牌注入提示词。" % (tag, b))

    # C2 否定式
    for n in NEGATIONS:
        if n in blob:
            i = blob.find(n)
            ctx = blob[max(0, i - 18):i + 24].replace("\n", " ")
            warns.append("%s 用了否定式「%s」（…%s…）。项目结论：否定约束对画面"
                         "无效，应改写成肯定式（例如「不得出现文字」→"
                         "「表面为纯色空白」）。" % (tag, n, ctx))

    # C3 视觉设定的识别锚点在实体描述里缺失
    sec = None
    for k in spec:
        if k == name or k in name or name in k:
            sec = spec[k]
            break
    if sec:
        # 只比**像外观**的项（见 _anchor_rules.missing_looks 的说明）
        try:
            from tools._anchor_rules import missing_looks
        except ImportError:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            from _anchor_rules import missing_looks
        for key, val in spec_anchors(sec):
            miss = missing_looks(val, desc)
            if miss:
                warns.append(
                    "%s 视觉设定「%s」里这几项**像外观、但实体描述里找不到**：%s"
                    "——实体描述才是生图输入，没写就不会画出来。"
                    % (tag, key,
                       "、".join("%s(命中%.0f%%)" % (p, r * 100)
                                 for p, r in miss[:4])))
    else:
        notes.append("%s 在《视觉设定.md》里没有对应小节" % tag)

    # C4 domain_fact
    df = (e["domain_fact"] or "").strip()
    if not df:
        blocks.append("%s 的 domain_fact 为空——门禁要求它可溯源到领域事实。" % tag)
    else:
        ids = [x.strip() for x in re.split(r"[,，、/\s]+", df) if x.strip()]
        for i in ids:
            if i == "story":
                continue
            if i not in facts:
                blocks.append("%s 的 domain_fact=%s 在领域事实表里不存在。" % (tag, i))
            elif facts[i]["assumed"]:
                warns.append("%s 依据的是**假设·待验证**事实 %s——"
                             "不得作为出图依据。" % (tag, i))

    # C5 场景：建筑公共元素
    #
    # ⚠️ 这里**不做"每间都要写全"的检查**——不是每间房都能看到天花或踢脚线。
    #    真正的判据是**跨房间是否一致**，交给 check_arch_consistency 统一比。
    if e["kind"] == "location":
        pass  # 见 main() 里的跨房间比对

    # C6 人物：混入场景道具
    if e["kind"] == "character":
        hit = [p for p in SCENE_PROPS_IN_CHARACTER if p in blob]
        if hit:
            blocks.append("%s 是人物，描述里却出现了场景道具：%s。"
                          "**人物参考只放妆造与相貌**；道具属于镜头提示词。"
                          "（今天把塑形器写进人物描述，模型就把它画成了"
                          "横穿嘴唇的刀片。）" % (tag, "、".join(hit)))

    # C7 该"替换成纯色空白"的位置被删空
    #
    # ⚠️ 关键词要窄：早先用了「标识」「编号」，
    #    结果「整容台控制按钮」那条因为描述里写了"圆形塑料外壳按钮"
    #    被误报成"提到标识却没写纯色空白"。**按钮不是标识。**
    #    只认**真会印字的载体**。
    if e["kind"] in ("prop", "location"):
        LABEL_CARRIERS = ("工牌", "证件", "招牌", "标签", "吊牌", "铭牌",
                          "印刷物", "文件", "证书", "证明")
        for kw in LABEL_CARRIERS:
            if kw in blob and not any(w in blob for w in
                                      ("空白", "无字", "无印刷", "无标识", "纯色")):
                warns.append("%s 提到「%s」但没写「表面为纯色空白/无字」——"
                             "不指定就会被模型自己填上内容"
                             "（踩过：填上了真实商标）。" % (tag, kw))
    return blocks, warns, notes


def check_arch_consistency(ents):
    r"""跨房间比对建筑公共元素——**只在"别的房间写了、这间没写"时报**。

    ## 为什么不能要求每间都写

    上一版要求每个 location 都写全 7 类公共元素，于是每间都报
    「没写天花、踢脚线」。但**不是每间房都能看到天花或踢脚线**——
    道具特写的场景里看不到，那不算缺。

    ## 正确的判据：**不一致**才报

    如果整容室写了「踢脚线」而焚化车间没写，那才是问题——
    观众会看出两间房的踢脚线不一样（或一间有一间没有）。
    本函数对每一类元素，找出**写了的房间**与**没写的房间**，
    两边都非空时才算不一致。
    """
    warns = []
    locs = [e for e in ents if e["kind"] == "location"]
    for elem, kws in ARCH_ELEMENTS.items():
        has, hasnt = [], []
        for e in locs:
            blob = (e["description"] or "") + " " + (e["style_note"] or "")
            (has if any(w in blob for w in kws) else hasnt).append(e["name"])
        if has and hasnt:
            warns.append("建筑元素「%s」只在 %s 里写了，%s 没写——"
                         "同一栋楼里这一项应当一致（同款门/踢脚线/天花/灯具），"
                         "否则观众会看出不是一栋楼。"
                         % (elem, "、".join(has), "、".join(hasnt)))
    return warns


def main():
    ap = argparse.ArgumentParser(description="图片提示词审查（出图前）")
    ap.add_argument("--episode-id", type=int, default=1)
    ap.add_argument("--phase", choices=("characters", "scenes", "props"),
                    default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    facts, ok, why = SE.parse_domain_facts(PROJECT_MD)
    spec = parse_visual_spec(os.path.join(ROOT, "剧集", "EP%03d" % args.episode_id,
                                          "视觉设定.md"))
    KIND = {"characters": "character", "scenes": "location", "props": "prop"}

    con = sqlite3.connect(os.path.join(ROOT, "data", "drama.db"))
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM entity"
    params = []
    if args.phase:
        sql += " WHERE kind=?"
        params.append(KIND[args.phase])
    sql += " ORDER BY kind, role DESC, name"
    ents = list(con.execute(sql, params))

    blocks, warns, notes = [], [], []
    notes.append("领域事实 %s（%s）" % (why, "可用" if ok else "不可用"))
    notes.append("《视觉设定.md》抽到 %d 个实体小节" % len(spec))
    notes.append("待审实体 %d 个" % len(ents))
    for e in ents:
        b, w, n = check_entity(dict(e), facts, spec)
        blocks += b
        warns += w
        notes += n
    # 跨房间建筑一致性（单独一步：它要同时看所有 location）
    warns += check_arch_consistency([dict(e) for e in ents])

    if args.json:
        print(json.dumps({"blocks": blocks, "warns": warns, "notes": notes},
                         ensure_ascii=False, indent=2))
        return 1 if blocks else 0

    print("=" * 74)
    print("图片提示词审查 · EP%03d%s" % (args.episode_id,
                                     "（%s）" % args.phase if args.phase else ""))
    print("=" * 74)
    for n in notes:
        print("  -- %s" % n)
    print()
    if blocks:
        print("  【阻断 · 会被模型照字面执行或违反项目结论】")
        for b in blocks:
            print("    ✗ %s" % b)
        print()
    if warns:
        print("  【提醒 · 可能出问题】")
        for w in warns:
            print("    ! %s" % w)
        print()
    if not blocks and not warns:
        print("  ✅ 没有发现问题")
    print("=" * 74)
    print("  阻断 %d 项，提醒 %d 项" % (len(blocks), len(warns)))
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
