# -*- coding: utf-8 -*-
"""第 2 阶段：从剧本提取实体（人物 / 场景 / 道具）并分级。

产出写入 entity 表。**分级是本阶段的核心产出**——它直接决定后面要出多少张图：
  main       大头照 + 全身照   = 每个 2 张
  secondary  只出全身照        = 每个 1 张
  background 不出图            = 0 张

实体挂在 project 下（全剧共享），所以跨集重跑是幂等的：同名同类的会被更新。

用法：
  python pipeline/stage_entities.py --episode-id 1
  python pipeline/stage_entities.py --episode-id 1 --from-json ents.json
  python pipeline/stage_entities.py --episode-id 1 --dump-prompt
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402
from pipeline import ark as A  # noqa: E402  取默认模型 ID，不抄字面量

PROMPT = os.path.join(C.ROOT, "pipeline", "prompts", "entities.md")

KINDS = {"character", "location", "prop"}
TIERS = {"main", "secondary", "background"}
IMAGES_PER_TIER = {"main": 4, "secondary": 1, "background": 0}   # 旧口径，仅作兜底

# 新素材体系下的出图张数（依据 stage_refs 的 CHARACTER_VIEWS / SCENE_VIEWS）：
#   人物  main=大头照+全身照=2   secondary=全身照=1
#   场景  main=正视图+站位图+俯视图=3   secondary=正视图+站位图=2
#   道具  main/secondary=1
# 这里**不 import stage_refs**，因为本阶段（第 2 阶段）要能独立跑，
# 而 stage_refs 会拉起 Ark 相关的依赖。数字变了要同步改两处。
_IMG_BY_KIND = {"character": {"main": 2, "secondary": 1, "background": 0},
                "location": {"main": 3, "secondary": 2, "background": 0},
                "prop": {"main": 1, "secondary": 1, "background": 0}}


def images_for(kind, tier):
    """该实体预计出几张图。"""
    return _IMG_BY_KIND.get(kind, {}).get(tier, IMAGES_PER_TIER.get(tier, 0))

SPEC = {
    "entities": [{
        "kind": str,
        "name": str,
        "alias": L.OPTIONAL_STR,
        "tier": str,
        "description": str,
        "style_note": L.OPTIONAL_STR,
        # 领域事实溯源（v9）。location / prop 必须回一个 F-xx 编号；
        # 人物与纯剧情道具写 'story'。**必须显式填**：
        # 留空分不清"不需要"和"忘了填"，所以空值会在 persist 里被拦。
        "domain_fact": str,
    }],
}

# 不需要领域事实的类别：人物是剧情设定；道具要分"剧情道具"和"职业用具"，
# 由模型自己按 domain_fact 取值区分（'story' vs 'F-xx'）。
DOMAIN_KINDS = ("location", "prop")
FACT_ID = re.compile(r"^F-\d+$")
ASSUMED_ID = re.compile(r"^F-9\d+$")   # 90+ 段是"假设·待验证"

PROJECT_DIR = os.path.join(C.ROOT, "knowledge", "03-项目档案")


def parse_domain_facts(path):
    """解析项目档案里的「领域事实」表。

    返回 ({编号: {"sentence","source","confidence","assumed"}}, ok, why)。

    只认 Markdown 表格行。**已确认**与**假设**分开：
    编号 `F-9x` 或置信度含"低/待验证"的算假设，默认不许用来出图。
    """
    if not os.path.exists(path):
        return {}, False, "项目档案不存在：%s" % path
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # 只取「## 领域事实」到下一个二级标题之间
    m = re.search(r"^##\s*领域事实\s*$(.*?)(?=^##\s|\Z)",
                  text, re.M | re.S)
    if not m:
        return {}, False, "项目档案里没有「## 领域事实」这一章"
    body = m.group(1)

    facts = {}
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        fid = cells[0]
        if not FACT_ID.match(fid):
            continue
        source = cells[3]
        conf = cells[4]
        assumed = bool(ASSUMED_ID.match(fid)) or any(
            k in conf for k in ("低", "待验证")) or source in ("无", "")
        facts[fid] = {"sentence": cells[2], "source": source,
                      "confidence": conf, "assumed": assumed}
    if not facts:
        return {}, False, ("「领域事实」章里没有可解析的表格行"
                           "（第一列要是 F-01 这样的编号）")
    return facts, True, "%d 条" % len(facts)


def domain_gate(con, ep, data, facts, ok, why, allow_assumed):
    """领域事实门禁。返回 (blocks, warns)。

    **为什么是 block 而不是 warn**：实体描述是生图提示词的直接输入，
    错了每张图 ¥0.30，而且错图会成为首帧参考、把错锁进下游。
    第一集就是这样三样全错（空间/载体/动作）——那时没有任何东西挡它。
    """
    blocks, warns = [], []
    if not ok:
        # 与 main() 里的提前拦截用**同一句话**开头——两处措辞不一致的话，
        # 自检和运维脚本按关键字判断就会漏。实测被自检抓到过一次。
        return (["领域事实不可用：%s\n"
                 "    第 2 阶段无法确认实体是真实存在的还是编的。\n"
                 "    先补 knowledge/03-项目档案/%s.md 的「## 领域事实」章"
                 "（模板见该目录 README）。" % (why, ep["project"])], warns)

    ents = data["entities"]
    for e in ents:
        tag = "%s「%s」" % (e["kind"], e["name"])
        df = str(e.get("domain_fact") or "").strip()
        if not df:
            blocks.append("%s 没填 domain_fact。"
                          "location/prop 要填 F-xx 编号；"
                          "人物或纯剧情道具填 story（**必须显式写**）" % tag)
            continue
        if df == "story":
            if e["kind"] == "location":
                blocks.append("%s 是场景，场景不能标 story——"
                              "场景的真实布局必须有领域事实依据" % tag)
            continue
        # ⚠️ 容忍逗号分隔的多条：模型常写 `F-01,F-04,F-16`（意思是"这些都对得上"）。
        #    字段设计上只该写一条，但**为了一个格式选择让整次模型调用白花**
        #    不划算——拆开逐条校验，全部有效才放行，多条只给提醒。
        ids = [x.strip() for x in re.split(r"[,，、/\s]+", df) if x.strip()]
        unknown = [x for x in ids if x not in facts]
        if unknown:
            blocks.append("%s 的 domain_fact=%s 里这几项在「领域事实」表里不存在：%s"
                          % (tag, df, "、".join(unknown)))
            continue
        assumed = [x for x in ids if facts[x]["assumed"]]
        if assumed and not allow_assumed:
            f = facts[assumed[0]]
            blocks.append("%s 依据的 %s 是**假设·待验证**（出处：%s，置信度：%s）：%s\n"
                          "    先用真实资料核实这条事实并补出处，"
                          "或加 --allow-assumed-facts 明确承担风险"
                          % (tag, "、".join(assumed), f["source"] or "无",
                             f["confidence"], f["sentence"][:40]))
        elif assumed:
            warns.append("%s 依据的是未验证事实 %s——已按 --allow-assumed-facts 放行"
                         % (tag, "、".join(assumed)))
        elif len(ids) > 1:
            warns.append("%s 的 domain_fact 写了 %d 条（%s）——建议只留最直接那条"
                         % (tag, len(ids), df))
    return blocks, warns


def load_domain_facts(ep):
    return parse_domain_facts(os.path.join(PROJECT_DIR, "%s.md" % ep["project"]))


def load_domain_questions(ep):
    """问题清单。**未回答的问题同样阻断**——否则问题清单会退化成没人看的清单。"""
    from pipeline import stage_domain_questions as DQ
    return DQ.parse_questions(os.path.join(PROJECT_DIR, "%s.md" % ep["project"]))


def facts_digest(facts):
    """给提示词的领域事实清单。**假设条目显式标注不可用**。"""
    if not facts:
        return ""
    confirmed = {k: v for k, v in sorted(facts.items()) if not v["assumed"]}
    assumed = {k: v for k, v in sorted(facts.items()) if v["assumed"]}
    out = []
    if confirmed:
        out.append("### 已确认（可以依据）")
        for k, v in confirmed.items():
            out.append("- %s：%s" % (k, v["sentence"]))
    if assumed:
        out.append("")
        out.append("### 假设·未验证（**禁止**作为实体描述的依据）")
        for k, v in assumed.items():
            out.append("- %s：%s" % (k, v["sentence"]))
    return "\n".join(out)


def build_prompt(con, ep, facts):
    row = con.execute(
        "SELECT id, content, version FROM script "
        "WHERE episode_id = ? AND is_current = 1", (ep["id"],)).fetchone()
    if not row:
        raise L.LLMError("第 %s 集还没有现行剧本，先跑第 1 阶段" % ep["seq"])
    scenes = con.execute(
        "SELECT seq, slug, beat, action, dialogue FROM scene "
        "WHERE script_id = ? ORDER BY seq", (row["id"],)).fetchall()
    digest = "\n".join(
        "- 第%d场 %s【%s】%s%s" % (s["seq"], s["slug"], s["beat"], s["action"],
                                 ("  台词：" + s["dialogue"]) if s["dialogue"] else "")
        for s in scenes)
    return L.load_prompt(PROMPT, script_content=row["content"],
                         scenes_digest=digest,
                         domain_facts=facts_digest(facts))


def persist(con, ep, data, dry):
    ents = data["entities"]

    bad_kind = sorted({e["kind"] for e in ents} - KINDS)
    if bad_kind:
        raise L.LLMError("kind 取值非法：%s（只允许 %s）"
                         % (bad_kind, "/".join(sorted(KINDS))))
    bad_tier = sorted({e["tier"] for e in ents} - TIERS)
    if bad_tier:
        raise L.LLMError("tier 取值非法：%s（只允许 %s）"
                         % (bad_tier, "/".join(sorted(TIERS))))

    seen = set()
    for e in ents:
        key = (e["kind"], e["name"].strip())
        if key in seen:
            raise L.LLMError("实体重复：%s / %s" % key)
        seen.add(key)

    if not dry:
        for e in ents:
            con.execute(
                "INSERT INTO entity (project_id, kind, name, alias, role,"
                " description, style_note, domain_fact) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id, kind, name) DO UPDATE SET "
                "alias = excluded.alias, role = excluded.role, "
                "description = excluded.description, style_note = excluded.style_note,"
                " domain_fact = excluded.domain_fact",
                (ep["project_id"], e["kind"], e["name"].strip(),
                 (e.get("alias") or "").strip(), e["tier"],
                 e["description"].strip(), (e.get("style_note") or "").strip(),
                 str(e.get("domain_fact") or "").strip()))
        con.commit()

    by_kind, by_tier = {}, {}
    for e in ents:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_tier[e["tier"]] = by_tier.get(e["tier"], 0) + 1
    images = sum(images_for(e["kind"], e["tier"]) for e in ents)
    return by_kind, by_tier, images


def main():
    ap = argparse.ArgumentParser(description="第 2 阶段：提取实体并分级")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--allow-assumed-facts", action="store_true",
                    help="允许实体依据「假设·待验证」的领域事实（默认阻断）")
    ap.add_argument("--allow-missing-domain-facts", action="store_true",
                    help="项目档案缺「领域事实」章时也继续（默认阻断，账会失真）")
    ap.add_argument("--allow-open-questions", action="store_true",
                    help="领域问题清单里还有未回答的问题时也继续（默认阻断）")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    rep = C.Report("第 2 阶段 · 提取实体")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    # 领域事实门禁（v9）。**先读事实再问模型**——顺序反了就会先编出实体再来找依据。
    facts, facts_ok, why = load_domain_facts(ep)
    if not facts_ok and not args.allow_missing_domain_facts:
        rep.add("领域事实可用", False, why)
        rep.info("实体描述是生图提示词的直接输入，错了每张图 ¥0.30，且错图会被"
                 "当成首帧参考把错锁进下游。所以这一关必须在出图之前。")
        rep.info("补法：knowledge/03-项目档案/%s.md 写「## 领域事实」章"
                 "（模板见该目录 README）。" % ep["project"])
        rep.info("确实要先跳过：加 --allow-missing-domain-facts。")
        con.close()
        return rep.finish()
    rep.add("领域事实可用", facts_ok, why)

    # 问题清单：**未回答的必须拦住**。这一关防的是"没意识到自己不懂"——
    # 第 1 集就是因为没人问过"化妆间和冷柜间是不是同一间"，才把两者合成了一间屋。
    qrows, q_ok, q_why = load_domain_questions(ep)
    if q_ok:
        left = [r for r in qrows if not r["answered"]]
        if left and not args.allow_open_questions:
            rep.add("领域问题已全部回答", False,
                    "%d 条未回答：%s" % (len(left),
                                        "、".join(r["id"] for r in left[:6])))
            for r in left[:6]:
                rep.info("  %s %s" % (r["id"], r["question"][:58]))
            rep.info("把答案填进 knowledge/03-项目档案/%s.md 的「## 领域问题」表；"
                     "确与画面无关的填「无关」。" % ep["project"])
            rep.info("确实要先跳过：加 --allow-open-questions。")
            con.close()
            return rep.finish()
        rep.add("领域问题已全部回答", True,
                "%d 条（无关 %d 条）" % (len(qrows),
                                      sum(1 for r in qrows if r["waived"])))
    else:
        # 没有清单章不算错——老项目还没有。但要提醒，因为它正是防"自己不知道"的机制。
        rep.add("领域问题清单", True, "无清单章（%s）——建议先跑第 1.5 阶段" % q_why)

    try:
        prompt = build_prompt(con, ep, facts)
    except L.LLMError as e:
        rep.add("现行剧本存在", False, str(e))
        con.close()
        return rep.finish()

    if args.dump_prompt:
        print(prompt)
        con.close()
        return 0

    try:
        if args.from_json:
            with open(args.from_json, encoding="utf-8") as f:
                obj = L.extract_json(f.read()) if args.from_json.endswith(".txt") \
                    else json.load(f)
            L.validate(obj, SPEC, "实体")
            rep.info("来源：%s（跳过模型调用）" % args.from_json)
        else:
            obj = L.chat_json([{"role": "user", "content": prompt}], SPEC, "实体",
                              base_url=args.base_url, model=args.model)
            rep.info("来源：模型调用")

        blocks, warns = domain_gate(con, ep, obj, facts, facts_ok, why,
                                    args.allow_assumed_facts)
        if blocks:
            for b in blocks:
                rep.add("实体可溯源到领域事实", False, b)
            rep.info("这一关挡的是「凭想象编的实体」。它比出图便宜得多——"
                     "一张图 ¥0.30，一个错实体会污染它所属的每一张图。")
            rep.info("修法：改剧本/改档案，让每个 location、prop 都能指回一条已确认事实；"
                     "确实不需要领域事实的（人物、一封信）显式写 domain_fact=story。")
            con.close()
            return rep.finish()
        rep.add("实体可溯源到领域事实", True,
                "%d 个实体 %s" % (len(obj["entities"]),
                                  "（含未验证事实，已显式放行）" if warns else ""))
        for w in warns:
            rep.add("待确认（warn）", True, w)

        by_kind, by_tier, images = persist(con, ep, obj, args.dry_run)
    except L.LLMError as e:
        rep.add("产出可用", False, str(e)[:100])
        print("\n" + str(e))
        con.close()
        return rep.finish()

    rep.add("实体总数", True, "%d 个" % sum(by_kind.values()))
    rep.add("三类都覆盖到了", set(by_kind) == KINDS,
            "  ".join("%s=%d" % (k, by_kind[k]) for k in sorted(by_kind)))
    rep.add("分级分布", True,
            "  ".join("%s=%d" % (t, by_tier[t]) for t in sorted(by_tier)))

    # 取**当前默认图片模型**的单价，不写死模型 ID。
    # 这里原来写死 'doubao-seedream-4-0-250828'——一个我们因账号未开通而弃用的模型，
    # 于是成本预估一直用着 ¥0.20 的老价格（真实是 ¥0.30），**低估约 1/3**。
    row = con.execute("SELECT unit_price FROM price_book "
                      "WHERE model_id = ? AND unit = 'image'",
                      (A.MODEL_IMAGE,)).fetchone()
    detail = "预计要出 %d 张参考图" % images
    if row:
        detail += "，约 ¥%.1f（按 ¥%.2f/张估）" % (images * row["unit_price"], row["unit_price"])
    rep.add("出图工作量预估", True, detail)
    rep.info("main=大头照+全身照(2张)  secondary=只出全身照(1张)  background=不出图")

    if args.dry_run:
        rep.info("dry-run：未写库")
    else:
        total = con.execute("SELECT COUNT(*) FROM entity WHERE project_id = ?",
                            (ep["project_id"],)).fetchone()[0]
        rep.add("已写入 entity 表", True, "本项目累计 %d 个实体" % total)

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
