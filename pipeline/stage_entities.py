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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

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
    }],
}


def build_prompt(con, ep):
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
    return L.load_prompt(PROMPT, script_content=row["content"], scenes_digest=digest)


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
                " description, style_note) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id, kind, name) DO UPDATE SET "
                "alias = excluded.alias, role = excluded.role, "
                "description = excluded.description, style_note = excluded.style_note",
                (ep["project_id"], e["kind"], e["name"].strip(),
                 (e.get("alias") or "").strip(), e["tier"],
                 e["description"].strip(), (e.get("style_note") or "").strip()))
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

    try:
        prompt = build_prompt(con, ep)
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

    row = con.execute("SELECT unit_price FROM price_book "
                      "WHERE model_id = 'doubao-seedream-4-0-250828' AND unit = 'image'"
                      ).fetchone()
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
