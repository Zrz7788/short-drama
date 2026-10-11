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
        consistency_line=consistency_line(ents, shot),
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


def main():
    ap = argparse.ArgumentParser(description="第 6 阶段：提示词定稿")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--show", type=int, default=None, help="打印指定镜号的成品提示词")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 6 阶段 · 提示词定稿")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    shots = con.execute(
        "SELECT id, seq, duration_s, shot_size, camera_move, camera_emotion,"
              " movement_speed, camera_lock, camera_angle, lens_mm, composition, dof,"
              " description, action,"
        " lighting, dialogue, sfx, strategy FROM shot WHERE episode_id=? ORDER BY seq",
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
    for s in shots:
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
            con.execute("UPDATE shot SET prompt=?, negative_prompt=? WHERE id=?",
                        (p, negs[sid], sid))
        con.commit()
        n = con.execute("SELECT COUNT(*) AS n FROM shot WHERE episode_id=? "
                        "AND prompt IS NOT NULL AND prompt<>''", (ep["id"],)).fetchone()["n"]
        rep.add("已写入 shot.prompt", True, "%d / %d 个镜头" % (n, len(shots)))
        rep.info("下一步：第 7 阶段出首帧图（需 ARK_API_KEY），"
                 "或直接查看 python pipeline/stage_prompts.py --episode-id %d --show 1"
                 % ep["id"])

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
