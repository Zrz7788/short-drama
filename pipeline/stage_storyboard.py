# -*- coding: utf-8 -*-
"""第 5 阶段：生成分镜。

把剧本拆成逐个镜头，写入 shot 表，并用 shot_entity 关联到实体。

按镜头决定抽卡策略（draft/direct + 次数）是本阶段最大的价值——它直接决定成本：
空镜抽 5 次是纯浪费，正脸特写抽 1 次是灾难。

用法：
  python pipeline/stage_storyboard.py --episode-id 1
  python pipeline/stage_storyboard.py --episode-id 1 --from-json shots.json
  python pipeline/stage_storyboard.py --episode-id 1 --dump-prompt
  python pipeline/stage_storyboard.py --episode-id 1 --force   # 覆盖已有分镜
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

PROMPT = os.path.join(C.ROOT, "pipeline", "prompts", "storyboard.md")

SHOT_SIZES = {"特写", "近景", "中景", "全景", "远景"}
CAMERA_MOVES = {"固定", "推", "拉", "摇", "移", "跟", "升降"}
ROLES = {"subject", "background", "prop"}
STRATEGIES = {"draft", "direct"}

# 运镜的"防崩坏说明"白名单。依据《运镜与分镜规范》的 50 条运镜库——
# 这一项是我们原来完全缺的：模型很容易把运动做糊、做畸变，必须显式排除。
CAMERA_LOCKS = {"匀速无卡顿", "画面无模糊拖影", "边缘无拉伸", "拉镜防畸变",
                "摇镜防抖", "人物无重影", "画面无眩晕感", "建筑线条笔直无变形"}
# "运镜四件套"：缺一项模型就只能自由发挥
CAMERA_REQUIRED = ("camera_emotion", "movement_speed", "camera_lock",
                   "camera_angle", "lens_mm", "composition", "dof")

SPEC = {
    "shots": [{
        "scene_seq": int,
        "seq": int,
        "duration_s": float,
        "shot_size": str,
        "camera_move": str,
        "camera_emotion": str,
        "movement_speed": str,
        "camera_lock": str,
        "camera_angle": str,
        "lens_mm": str,
        "composition": str,
        "dof": str,
        "description": str,
        "action": str,
        "dialogue": L.OPTIONAL_STR,
        "sfx": L.OPTIONAL_STR,
        "lighting": str,
        "entities": [{"name": str, "role": str}],
        "strategy": str,
        "draws_planned": int,
    }],
}


def load_context(con, ep):
    row = con.execute("SELECT id, content FROM script "
                      "WHERE episode_id = ? AND is_current = 1", (ep["id"],)).fetchone()
    if not row:
        raise L.LLMError("第 %s 集还没有现行剧本，先跑第 1 阶段" % ep["seq"])
    scenes = con.execute(
        "SELECT id, seq, slug, beat, action, dialogue, duration_s FROM scene "
        "WHERE script_id = ? ORDER BY seq", (row["id"],)).fetchall()
    entities = con.execute(
        "SELECT id, kind, name, alias, role, description FROM entity "
        "WHERE project_id = ? ORDER BY kind, name", (ep["project_id"],)).fetchall()
    if not entities:
        raise L.LLMError("本项目还没有实体，先跑第 2 阶段")
    return row, scenes, entities


def build_prompt(ep, script, scenes, entities, target):
    scenes_digest = "\n".join(
        "- 第%d场 %s【%s｜%ss】%s%s"
        % (s["seq"], s["slug"], s["beat"], s["duration_s"], s["action"],
           ("  台词：" + s["dialogue"]) if s["dialogue"] else "")
        for s in scenes)
    ent_digest = "\n".join(
        "- [%s / tier=%s] %s%s：%s"
        % (e["kind"], e["role"], e["name"],
           ("（别名：%s）" % e["alias"]) if e["alias"] else "",
           e["description"])
        for e in entities)
    return L.load_prompt(PROMPT,
                         script_content=script["content"],
                         scenes_digest=scenes_digest,
                         entities_digest=ent_digest,
                         target_seconds=target,
                         aspect="9:16")


def check_shots(con, ep, shots, scenes, entities):
    """返回 (scene_id_map, entity_id_map, 统计, 警告列表)。

    ⭐ **规则强度三分法**（依据 dsh-film 的交接规范）：

      block  可核查的——引用完整性、枚举、API 硬限制、预算护栏。
             **修正或报告；这些会阻断交接。**
      warn   工艺默认值——craft 层面的建议（运镜四件套是否写全、
             速度是否带数字…）。**遵循，除非作者意图另有说明；
             偏离时说清理由即可，不阻断。**
      note   品味——结局基调、钩子类型、视角、风格。
             **作者决定；绝不用它阻断。**（本阶段暂无此类检查）

    原来这里**所有**检查都 raise——把"工艺建议"当成了"机制性错误"。
    结果是作者想用别的防崩坏词、或某镜暂时不写运镜情绪命名时，整批被拒收。
    """
    by_seq = {s["seq"]: s["id"] for s in scenes}
    ent_by_name = {e["name"]: e["id"] for e in entities}

    seqs = [s["seq"] for s in shots]
    if seqs != list(range(1, len(shots) + 1)):
        raise L.LLMError("shot.seq 必须从 1 连续编号，实际：%s" % seqs[:12])

    block, warn = [], []
    for s in shots:
        tag = "第%d镜" % s["seq"]
        # ---- block：机制性的，过不去 ----
        if s["shot_size"] not in SHOT_SIZES:
            block.append("%s shot_size=%s" % (tag, s["shot_size"]))
        if s["camera_move"] not in CAMERA_MOVES:
            block.append("%s camera_move=%s" % (tag, s["camera_move"]))
        if s["strategy"] not in STRATEGIES:
            block.append("%s strategy=%s" % (tag, s["strategy"]))
        if not (4 <= float(s["duration_s"]) <= 15):
            block.append("%s duration_s=%s 超出 4–15 秒（API 硬限制）"
                         % (tag, s["duration_s"]))
        if not (1 <= int(s["draws_planned"]) <= 20):
            block.append("%s draws_planned=%s 超出 1–20" % (tag, s["draws_planned"]))
        if s["scene_seq"] not in by_seq:
            block.append("%s scene_seq=%s 不存在" % (tag, s["scene_seq"]))
        if not s["entities"]:
            block.append("%s 没有任何实体（出图无从约束）" % tag)
        for e in s["entities"]:
            if e["role"] not in ROLES:
                block.append("%s 实体 role=%s 非法" % (tag, e["role"]))
            if e["name"] not in ent_by_name:
                block.append("%s 引用了不存在的实体「%s」" % (tag, e["name"]))

        # ---- warn：工艺默认值，说清理由就能偏离 ----
        for k in CAMERA_REQUIRED:
            if not str(s.get(k) or "").strip():
                warn.append("%s %s 为空（缺了模型只能自由发挥）" % (tag, k))
        spd = str(s.get("movement_speed") or "")
        if spd and not re.search(r"\d", spd):
            warn.append("%s movement_speed 里没有数字：「%s」对模型没有约束力"
                        % (tag, spd[:24]))
        lock = str(s.get("camera_lock") or "")
        if lock:
            parts = [x.strip() for x in re.split(r"[、,，]", lock) if x.strip()]
            unknown = [x for x in parts if x not in CAMERA_LOCKS]
            if unknown:
                warn.append("%s camera_lock 不在常用短语表里：%s"
                            "（能用就留着，好用的话值得加进 CAMERA_LOCKS）"
                            % (tag, "、".join(unknown)))

    if block:
        raise L.LLMError(
            "分镜校验未通过（%d 项机制性问题）：\n  - %s\n\n"
            "（另有 %d 项工艺建议，见下方 warn）"
            % (len(block), "\n  - ".join(block[:10]), len(warn)))
    for w in warn:
        print("  --   [warn] %s" % w)

    total = sum(float(s["duration_s"]) for s in shots)
    draft = [s for s in shots if s["strategy"] == "draft"]
    draws = sum(int(s["draws_planned"]) for s in shots)
    return (by_seq, ent_by_name,
            {"total": total, "draft": len(draft),
             "direct": len(shots) - len(draft), "draws": draws},
            warn)


def estimate_cost(con, shots):
    """按 price_book 估算视频成本（默认用 2.5 + Draft 策略）。"""
    def price(variant):
        r = con.execute("SELECT unit_price FROM price_book WHERE model_id = "
                        "'doubao-seedance-2-5-260628' AND variant = ?", (variant,)).fetchone()
        return r["unit_price"] if r else None
    draft_p, final_p = price("Draft 样片"), price("旗舰")
    if draft_p is None or final_p is None:
        return None
    total = 0.0
    for s in shots:
        d, n = float(s["duration_s"]), int(s["draws_planned"])
        if s["strategy"] == "draft":
            total += n * d * draft_p + d * final_p
        else:
            total += n * d * final_p
    return total


def persist(con, ep, shots, by_seq, ent_by_name, dry):
    if dry:
        return
    for s in shots:
        sid = con.execute(
            "INSERT INTO shot (episode_id, scene_id, seq, duration_s, shot_size,"
            " camera_move, description, action, lighting, dialogue, sfx, strategy,"
            " camera_emotion, movement_speed, camera_lock, camera_angle, lens_mm,"
            " composition, dof,"
            " draws_planned, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
            (ep["id"], by_seq[s["scene_seq"]], s["seq"], float(s["duration_s"]),
             s["shot_size"], s["camera_move"], s["description"], s["action"],
             s["lighting"], s.get("dialogue") or "", s.get("sfx") or "",
             s["strategy"],
             s["camera_emotion"], s["movement_speed"], s["camera_lock"],
             s["camera_angle"], s["lens_mm"], s["composition"], s["dof"],
             int(s["draws_planned"]))).lastrowid
        for e in s["entities"]:
            con.execute("INSERT OR IGNORE INTO shot_entity (shot_id, entity_id, role) "
                        "VALUES (?,?,?)", (sid, ent_by_name[e["name"]], e["role"]))
    con.execute("UPDATE episode SET status = 'storyboarded' WHERE id = ?", (ep["id"],))
    con.commit()


def main():
    ap = argparse.ArgumentParser(description="第 5 阶段：生成分镜")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--force", action="store_true", help="已有分镜且已出片时强制覆盖")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 5 阶段 · 生成分镜")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    try:
        script, scenes, entities = load_context(con, ep)
    except L.LLMError as e:
        rep.add("上游数据齐备", False, str(e))
        con.close()
        return rep.finish()

    target = con.execute("SELECT target_seconds FROM episode WHERE id=?",
                         (ep["id"],)).fetchone()["target_seconds"] or 80
    prompt = build_prompt(ep, script, scenes, entities, target)
    if args.dump_prompt:
        print(prompt)
        con.close()
        return 0

    # 覆盖保护
    existing = con.execute("SELECT COUNT(*) AS n FROM shot WHERE episode_id=?",
                           (ep["id"],)).fetchone()["n"]
    if existing:
        made = con.execute(
            "SELECT COUNT(*) AS n FROM generation g JOIN shot s ON s.id=g.shot_id "
            "WHERE s.episode_id=?", (ep["id"],)).fetchone()["n"]
        if made and not args.force:
            rep.add("可安全覆盖", False,
                    "已有 %d 条生成记录，覆盖会连带删除。确认请加 --force" % made)
            con.close()
            return rep.finish()
        if not args.dry_run:
            con.execute("DELETE FROM shot WHERE episode_id=?", (ep["id"],))
            con.commit()
        rep.info("已清除原有 %d 个镜头" % existing)

    try:
        if args.from_json:
            with open(args.from_json, encoding="utf-8") as f:
                raw = f.read()
            obj = L.extract_json(raw)
            L.validate(obj, SPEC, "分镜")
            rep.info("来源：%s（跳过模型调用）" % args.from_json)
        else:
            obj = L.chat_json([{"role": "user", "content": prompt}], SPEC, "分镜",
                              base_url=args.base_url, model=args.model)
            rep.info("来源：模型调用")
        by_seq, ent_by_name, stat, warns = check_shots(
            con, ep, obj["shots"], scenes, entities)
    except L.LLMError as e:
        rep.add("产出可用", False, str(e)[:90])
        print("\n" + str(e))
        con.close()
        return rep.finish()

    # 工艺建议不阻断，但要显式报出来——作者可以据此决定改不改
    rep.add("机制性校验通过", True, "无阻断项")
    if warns:
        rep.add("工艺建议（不阻断）", True, "%d 项，见上方 [warn]" % len(warns))
    else:
        rep.add("工艺建议（不阻断）", True, "无")

    rep.add("镜头数", True, "%d 个" % len(obj["shots"]))
    rep.add("时长合计接近目标", abs(stat["total"] - target) <= target * 0.2,
            "%.0f 秒（目标 %s）" % (stat["total"], target))
    rep.add("抽卡策略已分化（不是一刀切）", stat["draft"] > 0 and stat["direct"] > 0,
            "draft=%d  direct=%d  计划总抽卡=%d 次"
            % (stat["draft"], stat["direct"], stat["draws"]))

    persist(con, ep, obj["shots"], by_seq, ent_by_name, args.dry_run)

    est = estimate_cost(con, obj["shots"])
    if est is not None:
        rep.add("视频成本预估", True, "约 ¥%.0f（按 2.5 + Draft 现价估算）" % est)
    if args.dry_run:
        rep.info("dry-run：未写库")
    else:
        n = con.execute("SELECT COUNT(*) AS n FROM shot_entity se JOIN shot s "
                        "ON s.id=se.shot_id WHERE s.episode_id=?", (ep["id"],)).fetchone()["n"]
        rep.add("已写入 shot 与 shot_entity", True, "%d 条镜头-实体关联" % n)
    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
