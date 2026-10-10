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

    finally:
        con.execute("ROLLBACK")
        left = con.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        print("\n  回滚后 project 行数: %d（应为 0，证明未污染生产数据）" % left)
        checks.append(left == 0)
        con.close()

    ok = all(checks)
    print("\n断言: %d 项，通过 %d 项 -> %s"
          % (len(checks), sum(checks), "全部通过" if ok else "**有失败**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
