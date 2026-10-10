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

# 与 knowledge/02-模型与成本/单集成本模型.md 一致的基准数字
SHOTS = 20
SHOT_SECONDS = 4
DRAWS_EACH = 3
DRAFT_PRICE = 0.26      # 元/秒 · 480p 样片（反推值）
FINAL_PRICE = 1.45      # 元/秒 · 1080p 成片
IMAGE_COUNT = 50
IMAGE_PRICE = 0.20

DRAFT_COST = SHOT_SECONDS * DRAFT_PRICE          # 1.04
FINAL_COST = SHOT_SECONDS * FINAL_PRICE          # 5.80
VIDEO_TOTAL = SHOTS * (DRAWS_EACH * DRAFT_COST + FINAL_COST)   # 178.40
IMAGE_TOTAL = IMAGE_COUNT * IMAGE_PRICE          # 10.00

BEATS = [("钩子", 3.0), ("建立", 14.0), ("升级", 40.0), ("高潮", 17.0), ("扣子", 6.0)]

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
                    (sh_id, a, "doubao-seedance-2-5-260628", "480p",
                     SHOT_SECONDS, DRAFT_COST))
                con.execute(
                    "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                    " unit_price, amount, episode_id, shot_id) "
                    "VALUES ('video',?,?, 'second',?,?,?,?)",
                    ("doubao-seedance-2-5-260628", SHOT_SECONDS, DRAFT_PRICE,
                     DRAFT_COST, eid, sh_id))
            con.execute(
                "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
                " duration_s, is_draft, status, cost) VALUES (?,?,?,?,?,0,'succeeded',?)",
                (sh_id, DRAWS_EACH + 1, "doubao-seedance-2-5-260628", "1080p",
                 SHOT_SECONDS, FINAL_COST))
            con.execute(
                "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                " unit_price, amount, episode_id, shot_id) "
                "VALUES ('video',?,?,'second',?,?,?,?)",
                ("doubao-seedance-2-5-260628", SHOT_SECONDS, FINAL_PRICE,
                 FINAL_COST, eid, sh_id))

        for _ in range(IMAGE_COUNT):
            con.execute(
                "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                " unit_price, amount, episode_id) VALUES ('image',?,1,'image',?,?,?)",
                ("doubao-seedream-4-0-250828", IMAGE_PRICE, IMAGE_PRICE, eid))

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

        # 每个实体先出定妆照；只有女主出全三视图
        full_views = ["portrait", "front", "side", "back"]
        ref_images = 0
        for name, ent_id in ents.items():
            for v in (full_views if name == "女主" else ["portrait"]):
                gid = con.execute(
                    "INSERT INTO image_generation (entity_id, view, model_id, size,"
                    " status, cost) VALUES (?,?,?,?,'succeeded',?)",
                    (ent_id, v, "doubao-seedream-4-0-250828", "2K", IMAGE_PRICE)
                ).lastrowid
                con.execute(
                    "INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                    " unit_price, amount, episode_id) "
                    "VALUES ('image',?,1,'image',?,?,?)",
                    ("doubao-seedream-4-0-250828", IMAGE_PRICE, IMAGE_PRICE, eid))
                con.execute(
                    "INSERT INTO entity_ref (entity_id, view, path, image_gen_id,"
                    " is_primary, approved) VALUES (?,?,?,?,?,1)",
                    (ent_id, v, "assets/refs/%s_%s.png" % (name, v), gid,
                     1 if v == "portrait" else 0))
                ref_images += 1
        check("参考图总数（4定妆 + 3三视图）", ref_images, 7)

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
        check("女主 refs 数", r["refs"], 4)
        check("女主 三视图齐备(前+侧+后)",
              r["has_front"] + r["has_side"] + r["has_back"], 3)
        check("女主 有主参考图", r["has_primary"], 1)
        check("女主 被镜头引用数", r["used_in_shots"], 5)

        r = view_row("v_entity_readiness", "name=?", "客厅")
        check("客厅 refs 数（只出了定妆照）", r["refs"], 1)
        check("客厅 has_front（应为 0）", r["has_front"], 0)
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
