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

# 景别跟情绪走（外部教程印证，用得越一致成片越不呆）：
#   人物登场 → 全景交代空间；情绪反应 → 近景/特写；冲突 → 快切（拆多镜）。
# 只能机械查"同一场清一色景别"——那几乎一定是没做景别设计。
EMOTION_SIZES = {"登场": ("extreme_wide", "wide"),
                 "反应": ("close", "close_up", "medium_close")}

# action 里的情绪形容词：模型执行不了"她很震惊"，只能执行可见的动作。
# 依据与《视觉设定》"抽象效果无法执行"同源——那条管实体描述，这条管动作。
EMOTION_WORDS = ("震惊", "惊讶", "愤怒", "慌张", "犹豫", "紧张", "悲伤", "开心",
                 "害怕", "恐惧", "绝望", "痛苦", "焦虑", "羞愧", "得意", "不屑")

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
        # 连续性具名锁（v8）。依据《Seedance提示词与连续性锁定》：
        # 约束属于**具名块**且用正面措辞，不属于尾部 negative_prompt。
        #
        # ⚠️ 这五项是 OPTIONAL：技能把「工艺默认值」定为**可说明理由偏离**的一级
        #    （craft_default），所以**不能**在这里做成硬拒绝——那会拒收合法输入
        #    （HANDOFF §七 记过一次："我把 camera_lock 白名单做成硬拒绝，太严"）。
        #    缺项由 CONTINUITY_LOCKS 的 warn 逐镜报出来，作者据此补或说明。
        "screen_direction": L.OPTIONAL_STR,
        "count_lock": L.OPTIONAL_STR,
        "stillness_lock": L.OPTIONAL_STR,
        "positive_locks": L.OPTIONAL_STR,
        "on_screen_text": L.OPTIONAL_STR,   # 只有承载精确文字的那一镜才非空
        # 转场设计（依外部教程：每个提示词结尾给一句契合画面的转场，
        # 管 80 秒成片的镜头衔接）。OPTIONAL：不填则提示词不占位，不编造。
        "transition": L.OPTIONAL_STR,
        # 人物出场设计（《11、人物出场8技巧》之一：动态闯入/光影渐显/
        # 他人视线/前景遮挡/道具引入/先声后人/运镜移焦/局部先入）。
        # 防"人物直接站在镜头里"的平淡开场。
        "entrance": L.OPTIONAL_STR,
        "entities": [{"name": str, "role": str}],
        "strategy": str,
        "draws_planned": int,
    }],
}

# 连续性锁：缺一项，世界状态就只能自由发挥。这四个必须非空。
CONTINUITY_LOCKS = ("screen_direction", "count_lock", "stillness_lock",
                    "positive_locks")


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


def build_prompt(ep, script, scenes, entities, target, only_scene=None):
    """only_scene 不为空时，只给这一场的剧本内容与摘要（分场生成，防整集 JSON 截断）。"""
    if only_scene is not None:
        scenes = [s for s in scenes if s["seq"] == only_scene]
        if not scenes:
            raise L.LLMError("库里没有第 %d 场" % only_scene)
        # 剧本正文同样只保留这一场（## 场景标题起，到下一个 ## 或文件尾）
        md = script["content"]
        m = re.search(r"(?ms)^##\s+%s\s*$.*?(?=^##\s|\Z)" % re.escape(str(only_scene)), md)
        content = m.group(0) if m else md
    else:
        content = script["content"]
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
                         script_content=content,
                         scenes_digest=scenes_digest,
                         entities_digest=ent_digest,
                         target_seconds=target,
                         aspect="9:16",
                         scene_mode=("只分镜第 %d 场" % only_scene) if only_scene is not None else "")


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
        # 连续性锁：同样是 warn 级——它是 craft_default，作者能说明理由偏离，
        # 但"忘了写"和"故意不写"必须能被区分出来，所以逐项报。
        for k in CONTINUITY_LOCKS:
            if not str(s.get(k) or "").strip():
                warn.append("%s %s 为空（世界状态无锁，跨镜会漂：换边/多人/回退）"
                            % (tag, k))
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

    # ---- 画面文字的**归属**检查（跨镜头，不能只在单镜里看）----
    # 模型无法跨镜头保持同一串文字/数字完全一致，看起来就是纯漂移。
    # 所以精确内容必须只归属到唯一一镜，其余镜头留空。
    with_text = [(s["seq"], str(s.get("on_screen_text") or "").strip())
                 for s in shots if str(s.get("on_screen_text") or "").strip()]
    if len(with_text) > 2:
        warn.append("有 %d 个镜头都写了 on_screen_text（第 %s 镜）："
                    "精确内容应只归属到唯一一镜，其余留空——"
                    "同一串文字/数字跨镜重复等于要求模型逐字保持一致，做不到"
                    % (len(with_text), "、".join(str(n) for n, _ in with_text)))
    for n, txt in with_text:
        if re.search(r"\d", txt) and len(with_text) > 1:
            warn.append("第%d镜的 on_screen_text 含数字（「%s」）且不止一镜有画面文字——"
                        "倒计时/读数**绝不要靠多次显示数字**来表现，"
                        "改用红字往下跳的动作、光的变化或人物呼吸"
                        % (n, txt[:24]))

    # ---- 景别多样性（跨镜头，按场看）----
    # 成片"呆/人机感重"最常见的根因之一：一整场从头到尾同一个景别。
    # 只报"清一色"，不规定哪一镜该用什么——景别设计是导演工作。
    by_scene = {}
    for s in shots:
        by_scene.setdefault(s["scene_seq"], []).append(s)
    for scene_seq, group in sorted(by_scene.items()):
        if len(group) >= 3:
            sizes = {s["shot_size"] for s in group}
            if len(sizes) == 1:
                warn.append("第%d场 %d 个镜全是 %s——景别没跟情绪走"
                            "（登场全景交代、反应近景捕捉、冲突拆快切），"
                            "一景到底的成片节奏是平的"
                            % (scene_seq, len(group), next(iter(sizes))))

    # ---- action 里的情绪形容词（单镜可查）----
    # "她很震惊"对模型是一句空话——它不知道震惊长什么样，只会自由发挥。
    # 要求改成可见动作（捏皱纸、后退半步、呼吸一滞…）。
    for s in shots:
        act = str(s.get("action") or "")
        hit = [w for w in EMOTION_WORDS if w in act]
        if hit:
            warn.append("第%d镜 action 含情绪形容词「%s」——模型执行不了情绪，"
                        "只能执行动作：改成可见的身体反应（如「指节捏白」「后退半步」）"
                        % (s["seq"], "、".join(hit[:3])))

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
        # 具名参数： positional 占位符在 28 列这种长度下数错一次就炸一次
        # （刚才 29 values for 28 columns 就是数错的），列名与键名一一对应。
        vals = {
            "episode_id": ep["id"],
            "scene_id": by_seq[s["scene_seq"]],
            "seq": s["seq"],
            "duration_s": float(s["duration_s"]),
            "shot_size": s["shot_size"],
            "camera_move": s["camera_move"],
            "description": s["description"],
            "action": s["action"],
            "lighting": s["lighting"],
            "dialogue": s.get("dialogue") or "",
            "sfx": s.get("sfx") or "",
            "strategy": s["strategy"],
            "camera_emotion": s["camera_emotion"],
            "movement_speed": s["movement_speed"],
            "camera_lock": s["camera_lock"],
            "camera_angle": s["camera_angle"],
            "lens_mm": s["lens_mm"],
            "composition": s["composition"],
            "dof": s["dof"],
            "screen_direction": s.get("screen_direction") or "",
            "count_lock": s.get("count_lock") or "",
            "stillness_lock": s.get("stillness_lock") or "",
            "positive_locks": s.get("positive_locks") or "",
            "on_screen_text": s.get("on_screen_text") or "",
            "transition": s.get("transition") or "",
            "entrance": s.get("entrance") or "",
            "draws_planned": int(s["draws_planned"]),
        }
        cols = ", ".join(vals)
        marks = ", ".join(":%s" % k for k in vals)
        sid = con.execute(
            "INSERT INTO shot (%s, status) VALUES (%s, 'pending')"
            % (cols, marks), vals).lastrowid
        for e in s["entities"]:
            con.execute("INSERT OR IGNORE INTO shot_entity (shot_id, entity_id, role) "
                        "VALUES (?,?,?)", (sid, ent_by_name[e["name"]], e["role"]))
    con.execute("UPDATE episode SET status = 'storyboarded' WHERE id = ?", (ep["id"],))
    con.commit()


def main():
    ap = argparse.ArgumentParser(description="第 5 阶段：生成分镜")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--force", action="store_true", help="已有分镜且已出片时强制覆盖")
    ap.add_argument("--scene", type=int, default=None,
                    help="只生成第 N 场的镜头（分场生成，防整集 JSON 截断）；"
                         "不带此参数则一次生成全集")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 5 阶段 · 生成分镜" +
                   ("（第 %d 场）" % args.scene if args.scene else ""))
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    try:
        script, scenes, entities = load_context(con, ep)
    except L.LLMError as e:
        rep.add("上游数据齐备", False, str(e))
        con.close()
        return rep.finish()

    target = con.execute("SELECT target_seconds FROM episode WHERE id=?",
                         (ep["id"],)).fetchone()["target_seconds"] or 80
    if args.scene is not None:
        sc = [s for s in scenes if s["seq"] == args.scene]
        if not sc:
            print("库里没有第 %d 场（现有：%s）" % (
                args.scene, "、".join(str(s["seq"]) for s in scenes)))
            con.close()
            return 2
        # 分场时把该场的分配时长作为本场目标
        target = int(round(sc[0]["duration_s"]))
        prompt = build_prompt(ep, script, scenes, entities, target,
                              only_scene=args.scene)
    else:
        prompt = build_prompt(ep, script, scenes, entities, target)
    if args.dump_prompt:
        print(prompt)
        con.close()
        return 0

    # 覆盖保护（分场模式下只清这一场的旧镜头）
    if args.scene is not None:
        existing = con.execute(
            "SELECT COUNT(*) AS n FROM shot WHERE episode_id=? AND scene_id IN "
            "(SELECT id FROM scene WHERE script_id IN (SELECT id FROM script "
            "WHERE episode_id=?) AND seq=?)",
            (ep["id"], ep["id"], args.scene)).fetchone()["n"]
    else:
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
            if args.scene is not None:
                con.execute(
                    "DELETE FROM shot WHERE episode_id=? AND scene_id IN "
                    "(SELECT id FROM scene WHERE script_id IN (SELECT id FROM script "
                    "WHERE episode_id=?) AND seq=?)",
                    (ep["id"], ep["id"], args.scene))
            else:
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
            # 分镜输出很长（每镜 转场/出场/五锁+台词约 500 字）。
            # 默认 8192 max_tokens 会**安静地截断**——JSON 解析报"无法解析"。
            # 16384 在 EP003 加「台词三条铁律」后**仍被截断**（16 镜整集一次吐）。
            # 解法：--scene 分场生成（每场 4-8 镜，单次输出约 3-5k token），
            # 32768 作为整集模式的兜底。模型按实消耗计费，设大不等于多用。
            obj = L.chat_json([{"role": "user", "content": prompt}], SPEC, "分镜",
                              base_url=args.base_url, model=args.model,
                              max_tokens=32768)

            try:
                _n, _amt = L.flush_usage(con, ep["id"])
                if _n:
                    rep.info("LLM 用量已记账：%d 次调用 ¥%.2f" % (_n, _amt))
            except Exception as _e:      # 记账失败不影响本阶段结果
                rep.info("LLM 记账失败（不影响结果）：%s" % _e)
            rep.info("来源：模型调用")
        # ⚠️ 顺序：**先校验（要求 1..N 连续），后平移到全局 seq**。
        # 反过来会被 check_shots 的「seq 必须从 1 连续」拦下（EP003 第 2 场实测）。
        by_seq, ent_by_name, stat, warns = check_shots(
            con, ep, obj["shots"], scenes, entities)
        if args.scene is not None:
            # 分场模式：校验通过后，把本场镜头的 seq 平移到全局（接在该集最大 seq 之后）
            base = con.execute(
                "SELECT COALESCE(MAX(seq),0) AS m FROM shot WHERE episode_id=?",
                (ep["id"],)).fetchone()["m"]
            for i, s in enumerate(obj["shots"], 1):
                s["seq"] = base + i
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
