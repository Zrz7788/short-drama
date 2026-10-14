# -*- coding: utf-8 -*-
"""第 6 阶段：提示词定稿。

**刻意不走 LLM，而是确定性拼接。** 原因只有一个，但足以定案：
实体的外观描述块必须**逐字复制**进每个镜头的提示词。一旦让模型"润色"，
同一个人在不同镜头里就会被描述成不同的人——这正是跨镜头崩脸的起点。

确定性拼接带来三个好处：
  1. 实体描述跨镜头逐字一致（可机械校验，脚本里就断言了这一点）
  2. 完全可复现，没有模型随机性
  3. 零成本，改了模板或改了实体描述，重跑一次即可，不必再调模型

用法：
  python pipeline/stage_prompts.py --episode-id 1
  python pipeline/stage_prompts.py --episode-id 1 --dry-run
  python pipeline/stage_prompts.py --episode-id 1 --show 3    # 打印第 3 镜的成品
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

TEMPLATE = os.path.join(C.ROOT, "pipeline", "prompts", "shot_prompt.md")

# 实体在提示词里的排列顺序：主体在前，背景在后（模型对靠前的描述更敏感）
ROLE_ORDER = {"subject": 0, "prop": 1, "background": 2}
ROLE_LABEL = {"subject": "主体", "prop": "道具", "background": "场景"}

NEGATIVE_BASE = [
    "五官扭曲", "面部模糊", "换脸", "多手多指", "肢体畸变",
    "画面抖动", "文字水印", "低分辨率", "过曝",
]


class PromptError(Exception):
    pass


def _g(row, key, default=""):
    """安全取列。sqlite3.Row 缺列会抛 IndexError（不是 KeyError），两种都兜。

    为什么不用 row[key] 直接取：本文件的 build() 同时被主流程和自检调用，
    自检传进来的 row 来自另一条 SELECT，未必带全部列。取不到就退回默认值，
    比让整个第 6 阶段崩掉好——缺的锁本来就是可选的（warn 已在第 5 阶段报过）。
    """
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


# ---------------------------------------------------------------- 渲染陷阱门
#
# 这两条都是**花了钱才知道**的：字段本身"填了字、看着没问题"，
# 但模型**照字面执行**，结果是画面里出现不该有的东西。
# 放在第 6 阶段（提示词定稿）拦，是因为它是**最后一道不花钱的关口**——
# 再往后一步就是按秒计费的视频生成。

# 分镜批注的形状：`S03 特写：遗体脚趾牌：三〇〇 盛伯渊 入炉 09:00`
_ANNOT_RE = re.compile(r"^\s*S\d{1,3}\s*[·:：、]?\s*"
                       r"(大特写|特写|近景|中景|中近景|全景|远景)")
_FLAT_WORDS = ("平躺", "仰卧", "躺卧", "躺在")
# 只查"看得见体态"的景别：**特写（眼睛/嘴/手）不查**——
# 眼睛特写没有朝向信息，判它"像站着"是误报（S05 实测：眼睛特写本来就正常）。
_POSE_SHOTS = ("近景", "中景", "中近景")


def check_render_traps(row):
    """返回 (blocks, warns)。只查**能证明会出错**的字段组合。"""
    blocks, warns = [], []
    seq = _g(row, "seq", "?")
    ost = str(_g(row, "on_screen_text")).strip()
    if ost and _ANNOT_RE.match(ost):
        blocks.append(
            "S%s 的 on_screen_text 是**分镜批注**、不是画面文字：%r\n"
            "        模型会把整串**逐字烧进画面**（实测 S03 已中招，"
            "画面里出现『S03 特写：…』）。\n"
            "        改成「只写该出现在画面上的那几个字」（如『三〇〇 盛伯渊 入炉 09:00』）。"
            % (seq, ost[:44]))
    if ost and len(ost) > 30:
        warns.append("S%s 的 on_screen_text 有 %d 字，偏长；"
                     "画面文字越长越容易被整句糊上去" % (seq, len(ost)))

    blob = " ".join(str(_g(row, k)) for k in
                    ("description", "action", "stillness_lock", "positive_locks"))
    # 「平躺」不一定写出来：S02 只写了"身体静止"，但描述里是"为遗体勾勒唇线"、
    # 动作里有"台面"——**躺着做整容**这件事藏在词里。所以两个信号取或：
    # 明写姿态，或"遗体 + 台面"同时出现。
    flat = any(w in blob for w in _FLAT_WORDS) or ("遗体" in blob and "台" in blob)
    ang = str(_g(row, "camera_angle"))
    size = str(_g(row, "shot_size"))
    if flat and "平视" in ang and any(x in size for x in _POSE_SHOTS):
        # ⚠️ 这条**只提醒、不阻断**：字段长得一样，实际结果不一样。
        # 实测 S02（近景/平视）真的渲染成"站着的人"（用户一眼看出），
        # 而 S07/S08 同类字段却是正常的躺姿——**脚本判不了，得看帧**。
        # 一刀切重出会白烧钱，所以这里只把**候选镜头**列出来交给人眼。
        warns.append(
            "S%s：平躺主体 + 机位平视 + 景别 %s —— **要人看帧确认**：\n"
            "        实测同类里 S02 渲染成了『站着的人』（正面像、看不出躺在台面上），"
            "S07/S08 同类字段却正常。\n"
            "        确实像站着就改**俯视/侧俯视**，并在提示词里写清"
            "『身体沿台面水平延伸、头枕台面、脸朝正上方』（肯定式）后再重出。"
            % (seq, size))
    return blocks, warns


def consistency_line(ents, shot):
    """跨镜一致性的锁定语。
    依据 YouMind 提示词库（703 条）里三种实测写法
    ——见 knowledge/02-模型与成本/图片提示词库可用手法.md §二·3：

        consistent lighting and color grading **across all panels**   → 光与调色
        **identical object continuity**                               → 物件
        the person looks **consistent**, maintain lighting/perspective → 人 + 光 + 视角

    **三条都点名了"哪一层"和"跨什么范围"。**
    笼统写"保持前后一致"没有约束力——模型不知道要锁哪一层。

    ⚠️ 再说清一件事：**这只是"请求它一致"，不是"保证一致"。**
    真正的一致性靠参考图 + 内容指纹（`entity_ref.sha256`）+ 事后审核；
    提示词只能减少漂移，不能消除。
    """
    parts = ["光影与调色跨镜一致"]
    # ⚠️ 用 .get：build() 传进来的 ents 来自另一个查询，
    # **只有 role/name/description/style_note，没有 kind**。
    # 直接 e["kind"] 会 KeyError——实测踩过（会让整个第 6 阶段不写库）。
    kinds = {e.get("kind") for e in (ents or []) if isinstance(e, dict)}
    if ents:
        parts.append("人物外观以角色卡为准，本镜不重复描述五官与服装")
    if "prop" in kinds:
        parts.append("同一件道具每次用同一个名称与外观")
    if "location" in kinds:
        parts.append("场景的空间关系与陈设跨镜不变")
    return "；".join(parts)


def entity_block(ents):
    """按角色排序拼出实体块。description 原样使用，不做任何改写。"""
    parts = []
    for e in sorted(ents, key=lambda x: ROLE_ORDER.get(x["role"], 9)):
        head = "【%s·%s】" % (ROLE_LABEL.get(e["role"], "其他"), e["name"])
        body = e["description"].strip()
        if e["style_note"] and e["style_note"].strip():
            body += "（%s）" % e["style_note"].strip()
        parts.append(head + body)
    return "\n".join(parts)


def locks_block(shot):
    """连续性具名锁块。**用正面措辞陈述"什么保持不变"。**

    依据《Seedance提示词与连续性锁定》：约束**不属于**末尾的 negative_prompt，
    而属于具名块。原文一句话点明根因——
    *"约束属于「具名块」，且通常用正面措辞陈述'什么保持不变'"*。

    而且否定式对画面本就无效（我们自己的结论：写"不要文字"，模型照样给文字）。
    所以这里全部写成肯定句："沈九永远在画面左侧"，而不是"不要换边"。

    缺项就不占位（warn 已在第 5 阶段逐镜报过），不在这里编造。
    """
    def g(k):
        return str(_g(shot, k)).strip()
    rows = [("轴线", g("screen_direction")),
            ("计数", g("count_lock")),
            ("静止", g("stillness_lock")),
            ("保持", g("positive_locks"))]
    rows = [(k, v) for k, v in rows if v]
    if not rows:
        return ""
    return "\n".join("【%s】%s" % (k, v) for k, v in rows)


def build(shot, ents):
    # 运镜拼成一行：情绪化命名（命名）+ 基底运镜 + 速度 + 防崩坏说明。
    # 依据《运镜与分镜规范》：同一个"推"，写成"聚焦式推进"就同时是动作描述
    # 和情绪指令；速度必须带数字；防崩坏说明显式排除糊/畸变/抖动/重影。
    # 情绪化命名已经含了动作（"聚焦式推进"里就有"推"），再拼一次基底运镜
    # 会变成"聚焦式推进推"这种重复。所以：有情绪化命名就用它，没有才退回基底运镜。
    emo = (shot["camera_emotion"] or "").strip()
    base = (shot["camera_move"] or "").strip()
    cam = emo or base or "固定"
    if (shot["movement_speed"] or "").strip():
        cam += "，%s" % shot["movement_speed"].strip()
    if (shot["camera_lock"] or "").strip():
        cam += "，%s" % shot["camera_lock"].strip()

    optics = "　".join(x for x in (
        ("焦段 %s" % shot["lens_mm"].strip()) if (shot["lens_mm"] or "").strip() else "",
        (shot["dof"] or "").strip()) if x) or "标准焦段"

    tpl = L.load_prompt(
        TEMPLATE,
        shot_size=shot["shot_size"],
        camera_line=cam,
        optics_line=optics,
        composition=(shot["composition"] or "").strip() or "三分法",
        camera_angle=(shot["camera_angle"] or "").strip() or "平视",
        entity_block=entity_block(ents),
        action=(shot["action"] or "").strip() or "（未填动作）",
        description=(shot["description"] or "").strip(),
        lighting=(shot["lighting"] or "").strip() or "自然光",
        sfx_line=("环境音：" + shot["sfx"].strip()) if shot["sfx"] else "",
        # 转场接续提示（依外部教程做法：每个提示词结尾给一句契合画面的转场，
        # 管 80 秒成片的镜头衔接）。shot.transition 为空就不占位，不编造。
        transition_line=(str(shot["transition"]).strip()
                         if (shot["transition"] or "").strip() else ""),
        consistency_line=consistency_line(ents, shot),
        locks_block=locks_block(shot),
        # ⚠️ 措辞很要紧：这里只说"画面文字…逐字照此"，模型会**把字浮在画面上**
        # （实测 S03 新拍：文字对了，但成了悬空字幕条）。要写明**字长在物件上**，
        # 否则"画面里的一行字"和"贴在纸牌上的字"模型分不清。
        on_screen_text=("画面内文字（本镜唯一一次出现，逐字照此，"
                        "**文字是印在/写在画面中的那个物件表面上的**，"
                        "不要做成悬空字幕或角标）："
                        + str(_g(shot, "on_screen_text")).strip())
        if str(_g(shot, "on_screen_text")).strip() else "",
        dialogue_line=("台词（需口型同步）：" + shot["dialogue"].strip())
        if shot["dialogue"] else "")
    # 可选行为空时会留下连续空行，收敛掉
    return re.sub(r"\n{3,}", "\n\n", tpl).strip()


def verify_verbatim(prompt, ents):
    """机械校验：每个实体的描述块必须原样出现在提示词里。

    这是本阶段的全部意义所在。模板被改坏、或者有人加了"润色"步骤，
    都会在这里立刻暴露，而不是等 80 集之后发现主角每次长得都不一样。
    """
    missing = [e["name"] for e in ents
               if e["description"] and e["description"].strip() not in prompt]
    if missing:
        raise PromptError(
            "实体描述未逐字出现在提示词中：%s\n"
            "这通常意味着模板改动把实体块吃掉了，或有人在中间加了改写步骤。"
            % "、".join(missing))


def audit_only(con, ep):
    """只跑渲染陷阱审查：**不调 LLM、不写库、不花钱**。

    用途：片子已经出完了才发现画面里有『S03 特写：…』这种批注、
    或者遗体看着像站着——先在这里把**同类**镜头一次性找齐，
    再决定重出哪几个，而不是一镜一镜试。
    """
    rows = con.execute("SELECT * FROM shot WHERE episode_id=? ORDER BY seq",
                       (ep["id"],)).fetchall()
    print("=" * 72)
    print("渲染陷阱审查 · EP%03d（不调 LLM、不写库、不花钱）" % ep["seq"])
    print("=" * 72)
    bad = 0
    for s in rows:
        b, w = check_render_traps(s)
        for x in b:
            bad += 1
            print("  ⛔ %s\n" % x)
        for x in w:
            print("  ⚠️ %s" % x)
    if not bad:
        print("  ✅ 没查出渲染陷阱")
    print("\n共 %d 处阻断 / 审查 %d 个镜头" % (bad, len(rows)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="第 6 阶段：提示词定稿")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--show", type=int, default=None, help="打印指定镜号的成品提示词")
    ap.add_argument("--audit", action="store_true",
                    help="只审『渲染陷阱』（画面文字批注/平躺主体写平视）：不调 LLM、不写库")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    if args.audit:
        code = audit_only(con, ep)
        con.close()
        return code
    rep = C.Report("第 6 阶段 · 提示词定稿")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    shots = con.execute(
        "SELECT id, seq, duration_s, shot_size, camera_move, camera_emotion,"
              " movement_speed, camera_lock, camera_angle, lens_mm, composition, dof,"
              " description, action,"
              " screen_direction, count_lock, stillness_lock, positive_locks,"
              " on_screen_text,"
        " lighting, dialogue, sfx, transition, strategy FROM shot WHERE episode_id=? ORDER BY seq",
        (ep["id"],)).fetchall()
    if not shots:
        rep.add("已有分镜", False, "先跑第 5 阶段")
        con.close()
        return rep.finish()

    ents_by_shot = {}
    for r in con.execute(
            "SELECT se.shot_id, se.role, e.name, e.description, e.style_note "
            "FROM shot_entity se JOIN entity e ON e.id = se.entity_id "
            "JOIN shot s ON s.id = se.shot_id WHERE s.episode_id = ?", (ep["id"],)):
        ents_by_shot.setdefault(r["shot_id"], []).append(dict(r))

    # 逐镜组装
    built, failed, no_ent = {}, {}, []
    traps, trap_warns = {}, []
    for s in shots:
        tb, tw = check_render_traps(s)
        if tb:
            traps[s["seq"]] = tb
        trap_warns += tw
        ents = ents_by_shot.get(s["id"], [])
        if not ents:
            no_ent.append("S%02d" % s["seq"])
            continue
        try:
            p = build(s, ents)
            verify_verbatim(p, ents)
            built[s["id"]] = (p, ents)
        except PromptError as e:
            failed[s["seq"]] = str(e)

    rep.add("镜头总数", True, "%d 个" % len(shots))
    rep.add("没有缺实体的镜头", not no_ent,
            "全部有实体" if not no_ent else "缺实体：%s" % ", ".join(no_ent))
    rep.add("全部通过逐字校验", not failed,
            "全部通过" if not failed else "失败 %d 个：%s"
            % (len(failed), list(failed)[:4]))
    # **渲染陷阱门**：这些字段组合模型会照字面执行，画面里就多出不该有的东西。
    # 放在这里拦是因为再往后一步就是按秒计费的视频生成。
    rep.add("没有渲染陷阱（画面文字/姿态）", not traps,
            "全部通过" if not traps else "%d 个镜头有问题" % len(traps))
    for seq in sorted(traps):
        for b in traps[seq]:
            print("  ⛔ %s" % b)
    for w in trap_warns:
        print("  ⚠️ %s" % w)

    if failed:
        for seq, msg in list(failed.items())[:3]:
            print("\n第 %d 镜：%s" % (seq, msg))
        con.close()
        return rep.finish()

    # 负面提示词：基础项 + 多主体时追加一致性约束
    negs = {}
    for s in shots:
        ents = ents_by_shot.get(s["id"], [])
        n = list(NEGATIVE_BASE)
        if sum(1 for e in ents if e["role"] == "subject") > 1:
            n.append("人物融合")
            n.append("人数变化")
        negs[s["id"]] = ", ".join(n)

    if args.show:
        row = next((s for s in shots if s["seq"] == args.show), None)
        if not row or row["id"] not in built:
            print("没有第 %s 镜" % args.show)
        else:
            print("\n" + "=" * 64)
            print("第 %s 镜 · %s · %ss · 策略 %s（计划抽 %s 次）"
                  % (row["seq"], row["shot_size"], row["duration_s"],
                     row["strategy"], row["draws_planned"]
                     if "draws_planned" in row.keys() else "?"))
            print("=" * 64)
            print(built[row["id"]][0])
            print("-" * 64)
            print("负面：" + negs[row["id"]])

    lens = [len(p) for p, _ in built.values()]
    rep.add("提示词长度", True, "最短 %d / 平均 %d / 最长 %d 字"
            % (min(lens), sum(lens) // len(lens), max(lens)))

    if args.dry_run:
        rep.info("dry-run：未写库")
    else:
        for sid, (p, _) in built.items():
            # 有渲染陷阱的镜头**不覆盖它的提示词**：人改完字段再跑，
            # 否则"重跑一次把好提示词冲掉"比不跑更糟。
            seq = next(s["seq"] for s in shots if s["id"] == sid)
            if seq in traps:
                continue
            con.execute("UPDATE shot SET prompt=?, negative_prompt=? WHERE id=?",
                        (p, negs[sid], sid))
        con.commit()
        n = con.execute("SELECT COUNT(*) AS n FROM shot WHERE episode_id=? "
                        "AND prompt IS NOT NULL AND prompt<>''", (ep["id"],)).fetchone()["n"]
        rep.add("已写入 shot.prompt", True, "%d / %d 个镜头%s"
                % (n, len(shots), "" if not traps else
                   "（%d 个因渲染陷阱未覆盖）" % len(traps)))
        rep.info("下一步：第 7 阶段出首帧图（需 ARK_API_KEY），"
                 "或直接查看 python pipeline/stage_prompts.py --episode-id %d --show 1"
                 % ep["id"])

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
