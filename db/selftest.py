# -*- coding: utf-8 -*-
"""数据库 schema 自检。

插入一集完整的模拟数据（剧本 → 场 → 镜头 → 生成 → 成本），
验算各视图的汇总结果，最后整体回滚——**不改动生产数据**。

用法： python db/selftest.py
退出码 0 = 全部断言通过。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "drama.db")

# 模型 ID **从 ark.py 取**，不抄字面量——这里原来写死 doubao-seedream-4-0-250828，
# 一个我们因账号未开通而弃用的模型；真实默认是 5-0-pro。
# 抄值的坏处是改默认模型时测试不会跟着变。
# ⚠️ 仓库根必须先上 sys.path，再 import pipeline。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import pipeline.ark as A   # noqa: E402

# 与 knowledge/02-模型与成本/单集成本模型.md 一致的基准数字。
#
# ⚠️ 价格**从生产价目表读**，不硬编码——这里原来写死 IMAGE_PRICE = 0.20，
# 那是 doubao-seedream-4-0-250828 的价格（一个因账号未开通而弃用的模型），
# 真实默认模型是 0.30。硬编码意味着**价格一变测试就测老世界**。
# 视频那两档（0.26 / 1.45）本来就和生产一致，仍然读库以防将来漂移。
SHOTS = 20
SHOT_SECONDS = 4
DRAWS_EACH = 3
IMAGE_COUNT = 50


def _prices(con):
    """从生产库读**当前实际在用的模型**的三档单价。

    ⚠️ 不能只写 ORDER BY unit_price LIMIT 1——那会取到全库最便宜的，
    也就是**已弃用的模型**。实测踩过：拿到了 doubao-seedream-4-0-250828 的
    0.20 与 doubao-seedance-2-0 标准档的 1.00，都不是我们在用的。
    必须按 ark.py 的默认模型筛。
    """
    import pipeline.ark as A

    def one(model_id, unit, like=None, exclude=None):
        q = ("SELECT unit_price FROM price_book WHERE model_id=? AND unit=?"
             " AND unit_price IS NOT NULL")
        args = [model_id, unit]
        if like:
            q += " AND variant LIKE ?"; args.append(like)
        if exclude:
            q += " AND variant NOT LIKE ?"; args.append(exclude)
        q += " ORDER BY unit_price LIMIT 1"
        r = con.execute(q, args).fetchone()
        return r[0] if r else None

    img = one(A.MODEL_IMAGE, "image")
    draft = one(A.MODEL_VIDEO_25, "second", like="%Draft%")
    final = one(A.MODEL_VIDEO_25, "second", exclude="%Draft%")
    missing = [n for n, v in (("image", img), ("draft", draft), ("final", final)) if v is None]
    if missing:
        raise SystemExit(
            "价目表缺 %s 的单价（模型 %s / %s）—— 先补 price_book 再跑 db 自检"
            % ("、".join(missing), A.MODEL_IMAGE, A.MODEL_VIDEO_25))
    return img, draft, final

BEATS = [("钩子", 3.0), ("建立", 14.0), ("升级", 40.0), ("高潮", 17.0), ("扣子", 6.0)]

# 价格与据此算出的总额，在 main() 里连库后填（见 _set_prices）
DRAFT_PRICE = FINAL_PRICE = IMAGE_PRICE = None
DRAFT_COST = FINAL_COST = VIDEO_TOTAL = IMAGE_TOTAL = None


def _set_prices(con):
    """连库后读价目并算总额。**所有期望值都由它推导，测试里不再有魔数。**"""
    global DRAFT_PRICE, FINAL_PRICE, IMAGE_PRICE
    global DRAFT_COST, FINAL_COST, VIDEO_TOTAL, IMAGE_TOTAL
    IMAGE_PRICE, DRAFT_PRICE, FINAL_PRICE = _prices(con)
    DRAFT_COST = SHOT_SECONDS * DRAFT_PRICE
    FINAL_COST = SHOT_SECONDS * FINAL_PRICE
    VIDEO_TOTAL = SHOTS * (DRAWS_EACH * DRAFT_COST + FINAL_COST)
    IMAGE_TOTAL = IMAGE_COUNT * IMAGE_PRICE
    print("  价目（读自生产库）：图片 ¥%.2f/张　样片 ¥%.2f/秒　成片 ¥%.2f/秒"
          % (IMAGE_PRICE, DRAFT_PRICE, FINAL_PRICE))

checks = []


def check(name, got, want, tol=1e-6):
    ok = abs(float(got) - float(want)) <= tol
    checks.append(ok)
    print("  %-38s 实际=%-10s 预期=%-10s %s"
          % (name, round(float(got), 4), round(float(want), 4), "OK" if ok else "**FAIL**"))


def main():
    if not os.path.exists(DB_PATH):
        print("数据库不存在，先跑 python db/init_db.py")
        return 1

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    _set_prices(con)   # 期望值全部由生产价目表推导，不留魔数
    con.isolation_level = None
    BEFORE = con.execute("SELECT COUNT(*) FROM project").fetchone()[0]  # 开工前基线
    con.execute("BEGIN")

    try:
        pid = con.execute(
            "INSERT INTO project (name, logline, genre, target_episode_seconds) "
            "VALUES ('自检项目','用于验证 schema','测试',80)").lastrowid
        eid = con.execute(
            "INSERT INTO episode (project_id, seq, title, target_seconds) "
            "VALUES (?,1,'第1集',80)", (pid,)).lastrowid
        cid = con.execute(
            "INSERT INTO character (project_id, name, role, appearance) "
            "VALUES (?,?,'主角','35岁女性，东亚面孔')", (pid, "自检角色")).lastrowid

        sid = con.execute(
            "INSERT INTO script (episode_id, version, title, logline, content) "
            "VALUES (?,1,'第1集','一句话','（自检用剧本正文）')", (eid,)).lastrowid

        scene_ids = []
        for i, (beat, dur) in enumerate(BEATS, 1):
            scene_ids.append(con.execute(
                "INSERT INTO scene (script_id, seq, slug, beat, duration_s) "
                "VALUES (?,?,?,?,?)", (sid, i, "内景 · 夜 · 客厅", beat, dur)).lastrowid)

        shot_ids = []
        for i in range(1, SHOTS + 1):
            shot_ids.append(con.execute(
                "INSERT INTO shot (episode_id, scene_id, seq, duration_s, char_id, prompt) "
                "VALUES (?,?,?,?,?,?)",
                (eid, scene_ids[(i - 1) * len(scene_ids) // SHOTS], i,
                 SHOT_SECONDS, cid, "近景，女主回头")).lastrowid)

        # 每个镜头：3 次 480p 样片 + 1 次 1080p 成片
        for sh_id in shot_ids:
            for a in range(1, DRAWS_EACH + 1):
                con.execute(
                    "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
                    " duration_s, is_draft, status, cost) VALUES (?,?,?,?,?,1,'succeeded',?)",
                    (sh_id, a, A.MODEL_VIDEO_25, "480p",
                     SHOT_SECONDS, DRAFT_COST))
                con.execute(
                    "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                    " unit_price, amount, episode_id, shot_id) "
                    "VALUES ('video',?,?, 'second',?,?,?,?)",
                    (A.MODEL_VIDEO_25, SHOT_SECONDS, DRAFT_PRICE,
                     DRAFT_COST, eid, sh_id))
            con.execute(
                "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
                " duration_s, is_draft, status, cost) VALUES (?,?,?,?,?,0,'succeeded',?)",
                (sh_id, DRAWS_EACH + 1, A.MODEL_VIDEO_25, "1080p",
                 SHOT_SECONDS, FINAL_COST))
            con.execute(
                "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                " unit_price, amount, episode_id, shot_id) "
                "VALUES ('video',?,?,'second',?,?,?,?)",
                (A.MODEL_VIDEO_25, SHOT_SECONDS, FINAL_PRICE,
                 FINAL_COST, eid, sh_id))

        for _ in range(IMAGE_COUNT):
            con.execute(
                "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                " unit_price, amount, episode_id) VALUES ('image',?,1,'image',?,?,?)",
                (A.MODEL_IMAGE, IMAGE_PRICE, IMAGE_PRICE, eid))

        print("--- 视图验算（模拟 %d 镜 × %d 抽 + 1 成片）---" % (SHOTS, DRAWS_EACH))

        row = con.execute("SELECT * FROM v_episode_cost WHERE episode_id=?", (eid,)).fetchone()
        cols = [d[0] for d in con.execute("SELECT * FROM v_episode_cost LIMIT 0").description]
        r = dict(zip(cols, row))
        check("v_episode_cost.shots", r["shots"], SHOTS)
        check("v_episode_cost.generations", r["generations"], SHOTS * (DRAWS_EACH + 1))
        check("v_episode_cost.video_cost", r["video_cost"], VIDEO_TOTAL)
        check("v_episode_cost.total_cost", r["total_cost"], VIDEO_TOTAL + IMAGE_TOTAL)

        row = con.execute("SELECT * FROM v_episode_outline WHERE episode_id=?", (eid,)).fetchone()
        cols = [d[0] for d in con.execute("SELECT * FROM v_episode_outline LIMIT 0").description]
        r = dict(zip(cols, row))
        check("v_episode_outline.scenes", r["scenes"], len(BEATS))
        check("v_episode_outline.planned_seconds", r["planned_seconds"],
              sum(d for _, d in BEATS))
        check("v_episode_outline.shots", r["shots"], SHOTS)
        # 这一项专门防多表 JOIN 的笛卡尔积导致秒数虚高
        check("v_episode_outline.shot_seconds", r["shot_seconds"], SHOTS * SHOT_SECONDS)

        n = con.execute("SELECT COUNT(*) FROM v_shot_progress WHERE attempts=?",
                        (DRAWS_EACH + 1,)).fetchone()[0]
        check("v_shot_progress 每镜 attempts", n, SHOTS)

        # 单集成本应还原知识库里的 ¥178.4
        check("与知识库单集成本对齐", r["shot_seconds"] and VIDEO_TOTAL, 178.40)

        # ---------------- 实体 / 参考图 / 衔接 管线 ----------------
        print("\n--- 实体与参考图管线验算 ---")
        ents = {}
        for kind, name in [("character", "女主"), ("character", "男主"),
                           ("location", "客厅"), ("prop", "照片")]:
            ents[name] = con.execute(
                "INSERT INTO entity (project_id, kind, name, description) "
                "VALUES (?,?,?,?)",
                (pid, kind, name, "%s 的外观描述块" % name)).lastrowid
        check("实体数（人物2+布景1+道具1）", len(ents), 4)

        # 新素材体系：人物出「全身照 + 大头照」，场景出「正视/站位/俯视」，道具出参考图
        VIEWS = {"女主": ["fullbody", "headshot"],
                 "男主": ["fullbody"],
                 "客厅": ["scene_front", "blocking", "scene_top"]}
        PRIMARY = {"fullbody", "scene_front", "prop_ref"}
        ref_images = 0
        for name, ent_id in ents.items():
            for v in VIEWS.get(name, ["prop_ref"]):
                gid = con.execute(
                    "INSERT INTO image_generation (entity_id, view, model_id, size,"
                    " status, cost) VALUES (?,?,?,?,'succeeded',?)",
                    (ent_id, v, A.MODEL_IMAGE, "2K", IMAGE_PRICE)
                ).lastrowid
                con.execute(
                    "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                    " unit_price, amount, episode_id) "
                    "VALUES ('image',?,1,'image',?,?,?)",
                    (A.MODEL_IMAGE, IMAGE_PRICE, IMAGE_PRICE, eid))
                con.execute(
                    "INSERT INTO entity_ref (entity_id, view, path, image_gen_id,"
                    " is_primary, approved) VALUES (?,?,?,?,?,1)",
                    (ent_id, v, "assets/refs/%s_%s.png" % (name, v), gid,
                     1 if v in PRIMARY else 0))
                ref_images += 1
        check("参考图总数（人物3 + 场景3 + 道具1）", ref_images, 7)

        # 前 5 个镜头挂实体（女主为主体，客厅为背景）
        for sh in shot_ids[:5]:
            con.execute("INSERT INTO shot_entity (shot_id, entity_id, role) "
                        "VALUES (?,?,'subject')", (sh, ents["女主"]))
            con.execute("INSERT INTO shot_entity (shot_id, entity_id, role) "
                        "VALUES (?,?,'background')", (sh, ents["客厅"]))

        # 视频衔接：第 1 镜抽尾帧，第 2 镜用它作首帧并声明续接
        con.execute("UPDATE shot SET last_frame_path='assets/frames/S01_last.png' "
                    "WHERE id=?", (shot_ids[0],))
        con.execute("UPDATE shot SET first_frame_path='assets/frames/S01_last.png', "
                    "continues_from=? WHERE id=?", (shot_ids[0], shot_ids[1]))

        def view_row(view, where, param):
            cur = con.execute("SELECT * FROM %s WHERE %s" % (view, where), (param,))
            cols = [d[0] for d in con.execute(
                "SELECT * FROM %s LIMIT 0" % view).description]
            return dict(zip(cols, cur.fetchone()))

        r = view_row("v_entity_readiness", "name=?", "女主")
        check("女主 refs 数（全身照+大头照）", r["refs"], 2)
        check("女主 两种视角齐备",
              r["has_fullbody"] + r["has_headshot"], 2)
        check("女主 有主参考图", r["has_primary"], 1)
        check("女主 被镜头引用数", r["used_in_shots"], 5)

        r = view_row("v_entity_readiness", "name=?", "客厅")
        check("客厅 refs 数（正视+站位+俯视）", r["refs"], 3)
        check("客厅 场景三视角齐备",
              r["has_scene_front"] + r["has_blocking"] + r["has_scene_top"], 3)
        check("客厅 has_headshot（人物视角，应为 0）", r["has_headshot"], 0)
        check("客厅 被镜头引用数", r["used_in_shots"], 5)

        r = view_row("v_entity_readiness", "name=?", "照片")
        check("道具 未被任何镜头引用", r["used_in_shots"], 0)

        r = view_row("v_pipeline_status", "episode_id=?", eid)
        check("管线·场数", r["scenes"], len(BEATS))
        check("管线·镜头数", r["shots"], SHOTS)
        check("管线·有提示词的镜头", r["shots_with_prompt"], SHOTS)
        check("管线·已抽帧的镜头", r["shots_with_frames"], 2)
        check("管线·已成片的镜头", r["shots_with_video"], SHOTS)

        # 参考图的成本必须也进了流水
        total = con.execute("SELECT SUM(amount) FROM cost_ledger WHERE episode_id=?",
                            (eid,)).fetchone()[0]
        check("参考图成本已入流水", total,
              VIDEO_TOTAL + IMAGE_TOTAL + ref_images * IMAGE_PRICE)

    finally:
        con.execute("ROLLBACK")
        left = con.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        # 原断言是"回滚后应为 0"。那假设了生产库为空——但生产库本来就该有
        # 真实作品，第一次真跑之后它必然失败（实测）。
        # 真正要证的是"回滚把自检插的数据都撤销了"，所以跟开工前的行数比。
        ok_left = (left == BEFORE)
        print("\n  回滚后 project 行数: %d（开工前 %d）—— %s"
              % (left, BEFORE, "未污染生产数据" if ok_left else "**有残留**"))
        checks.append(ok_left)
        con.close()

    ok = all(checks)
    print("\n断言: %d 项，通过 %d 项 -> %s"
          % (len(checks), sum(checks), "全部通过" if ok else "**有失败**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
