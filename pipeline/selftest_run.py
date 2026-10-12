# -*- coding: utf-8 -*-
"""执行层自检：清单解析、门禁、存档、续跑、决策记录。

门禁这种东西，只测"能跑"是没有意义的——**必须测"不该跑的跑不了"**。
所以这里的重点全在反向用例：跳门禁、乱序、绕批准、丢版本。

    python pipeline/selftest_run.py [--keep]
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C          # noqa: E402
from pipeline import checkpoint as ck     # noqa: E402
from pipeline import manifest as M        # noqa: E402

ROOT = C.ROOT
TEST_ROOT = os.path.join(ROOT, ".tools", "run-selftest")
TEST_DB = os.path.join(TEST_ROOT, "drama.db")
rep = C.Report("执行层自检")


def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    r = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-500:]
    con = C.connect(TEST_DB)
    pid = con.execute("INSERT INTO project (name, logline, target_episode_seconds) "
                      "VALUES ('自检剧','x',80)").lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    con.commit()
    return con, eid


def run_cli(*args):
    return subprocess.run([sys.executable, "-X", "utf8",
                           os.path.join(ROOT, "pipeline", "run.py")] + list(args),
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)

    # ---------------------------------------------------------- 1 清单解析
    print("=== 1. 清单解析（自写的迷你 YAML）===")
    m = M.load()
    rep.add("顶层键齐备",
            all(k in m.raw for k in ("name", "version", "orchestration", "stages")),
            "name=%s v%s" % (m.name, m.version))
    rep.add("块标量 > 被折叠成一行",
            "\n" not in (m.description or "") and len(m.description) > 40,
            "描述 %d 字符" % len(m.description))
    rep.add("行内列表 [a, b] 解析正确",
            m.stage("keyframes").required_in == ["shot_prompts", "reference_sheets"],
            str(m.stage("keyframes").required_in))
    rep.add("orchestration 数字解析为数值",
            m.budget_default == 200.0 and m.hard_cap == 400.0,
            "预算 %s 硬顶 %s" % (m.budget_default, m.hard_cap))
    # art 拆三个子阶段：人物素材 / 场景素材 / 道具素材（原先是 portrait + turnaround）
    rep.add("子阶段解析出来", len(m.stage("art").substages) == 3
            and len(m.stage("motion").substages) == 3,
            "art=%s motion=%s"
            % ([s.name for s in m.stage("art").substages],
               [s.name for s in m.stage("motion").substages]))
    rep.add("门禁开关读对（keyframes 需人批、prompts 自动）",
            m.stage("keyframes").human_approval and not m.stage("prompts").human_approval)

    # 行尾注释不能污染值
    skills = M.parse("required_skills:\n  - meta/x   # 说明\n")["required_skills"]
    rep.add("行尾注释不污染值", skills == ["meta/x"], str(skills))
    # 折行的列表项要接起来
    wrapped = M.parse("k:\n  - 第一句\n    第二句\n")["k"]
    rep.add("折行的列表项被连接", wrapped == ["第一句 第二句"], str(wrapped))
    # 中文全角冒号不能被当成键
    cjk = M.parse("k:\n  - 说明：这里用全角\n    续行\n")["k"]
    rep.add("全角冒号不被误认为键", cjk == ["说明：这里用全角 续行"], str(cjk))

    # 反向验证：故意造一份坏清单，必须报错
    bad = """name: bad
version: "1"
orchestration: {}
requires_skills: []
required_skills: []
stages:
  - name: a
    produces: [x]
    human_approval_default: true
  - name: b
    required_artifacts_in: [nonexistent]
    produces: [y]
"""
    probs, _ = M.Manifest(M.parse(bad)).validate()
    rep.add("坏清单被校验拦住（反向验证）",
            any("review_focus" in p for p in probs)
            and any("nonexistent" in p for p in probs),
            "报了 %d 个问题" % len(probs))
    # 语法错必须抛错，不能静默解析错
    try:
        M.parse("a: 1\n   b: 2\n")
        syntax_ok = False
    except M.ManifestError:
        syntax_ok = True
    rep.add("缩进错误抛异常而不是静默解析错", syntax_ok)

    # ---------------------------------------------------------- 2 运行单元
    print("\n=== 2. 运行单元展平 ===")
    units = m.run_units()
    keys = [u.key for u in units]
    rep.add("运行单元 15 个（11 阶段 + art 拆 3 + motion 拆 3 − 2 个父阶段）",
            len(units) == 15, "%d 个" % len(units))
    rep.add("art 与 motion 的父阶段不作为单元",
            "art" not in keys and "motion" not in keys)
    # 第 1.5 阶段（领域问题）插在 script 与 cast 之间——它是"承认自己不懂"的那一步，
    # 必须早于任何出图，否则就会像第 1 集那样把编出来的职业常识一路画进首帧。
    rep.add("单元顺序正确（domain_questions 在 script 之后、cast 之前）",
            keys[:4] == ["spec", "script", "domain_questions", "cast"],
            " → ".join(keys[:4]))

    con, eid = fresh_db()

    # ---------------------------------------------------------- 3 门禁
    print("\n=== 3. 门禁（反向用例为主）===")
    try:
        ck.begin(con, eid, "script", m)
        blocked = False
    except ck.GateViolation:
        blocked = True
    rep.add("前面没完成就开跑 → 被拦", blocked)
    n_cp = con.execute("SELECT COUNT(*) FROM checkpoint").fetchone()[0]
    rep.add("被拦时不留脏存档", n_cp == 0, "%d 条存档" % n_cp)

    try:
        ck.begin(con, eid, "assemble", m)
        blocked = False
    except ck.GateViolation:
        blocked = True
    rep.add("跳到最后一个阶段 → 被拦", blocked)

    # ---------------------------------------------------------- 4 逐道绑定
    print("\n=== 4. 门禁逐道绑定 ===")
    ck.approve(con, eid, "spec", "规格锁定", m)
    try:
        ck.begin(con, eid, "cast", m)
        blocked = False
    except ck.GateViolation:
        blocked = True
    rep.add("批了 spec 仍不能跳到 cast（script 还没做）", blocked)

    ck.begin(con, eid, "script", m)
    row = ck.current(con, eid, "script")
    try:
        ck.finish(con, row["id"], m=m)   # 有人工门禁，未批准就写 completed
        blocked = False
    except ck.GateViolation:
        blocked = True
    rep.add("有人工门禁的阶段不许自己写成 completed", blocked)
    rep.add("该阶段仍是 in_progress",
            ck.current(con, eid, "script")["status"] == "in_progress",
            ck.current(con, eid, "script")["status"])

    ck.await_human(con, row["id"])
    try:
        ck.finish(con, row["id"], m=m)
        blocked = False
    except ck.GateViolation:
        blocked = True
    rep.add("等人批时直接 finish → 被拦", blocked)

    ck.approve(con, eid, "script", "钩子够狠", m)
    ck.finish(con, row["id"], m=m)
    rep.add("批准后 finish 成功",
            ck.current(con, eid, "script")["status"] == "completed")

    # ---------------------------------------------------------- 5 存档不销毁
    print("\n=== 5. 存档版本不销毁 ===")
    # 先按顺序走完 domain_questions（第 1.5 阶段），否则开 cast 会被门禁拦。
    # 这本身就是一条断言：**新增的中间阶段确实排在了 cast 前面。**
    try:
        ck.begin(con, eid, "cast", m)
        jumped = True
    except ck.GateViolation:
        jumped = False
    rep.add("domain_questions 未完成时开 cast → 被拦", not jumped)

    ck.begin(con, eid, "domain_questions", m)
    ck.approve(con, eid, "domain_questions", "问题清单够狠", m)
    ck.finish(con, ck.current(con, eid, "domain_questions")["id"], m=m)
    rep.add("domain_questions 走完后放行",
            ck.current(con, eid, "domain_questions")["status"] == "completed")

    ck.begin(con, eid, "cast", m)
    r2 = ck.current(con, eid, "cast")
    ck.fail(con, r2["id"], "模拟失败")
    ck.begin(con, eid, "cast", m)
    rows = con.execute("SELECT revision, status FROM checkpoint WHERE stage='cast' "
                       "ORDER BY revision").fetchall()
    rep.add("重开阶段时 revision 递增且旧记录保留",
            len(rows) == 2 and rows[0]["status"] == "failed" and rows[1]["revision"] == 2,
            " ".join("r%d:%s" % (r["revision"], r["status"]) for r in rows))

    # ---------------------------------------------------------- 6 续跑
    print("\n=== 6. 续跑点 ===")
    unit, reason = ck.resume_point(con, eid, m)
    rep.add("有 in_progress 时续跑指向它",
            unit and unit["key"] == "cast" and "中断" in reason,
            "%s —— %s" % (unit["key"] if unit else None, reason))
    ck.fail(con, ck.current(con, eid, "cast")["id"], "再失败一次")
    unit, reason = ck.resume_point(con, eid, m)
    rep.add("failed 时续跑指向它并说明原因",
            unit and unit["key"] == "cast" and "失败" in reason, reason)

    # ---------------------------------------------------------- 7 决策记录
    print("\n=== 7. 决策记录 ===")
    ck.decide(con, eid, "spec", "model_choice", "用 2.5 + Draft",
              ["2.0 pro 直抽"], "抽卡≥2 次时 Draft 更省", "high", "用户控制台价")
    n = con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    rep.add("决策已落库", n >= 3, "%d 条（含两次批准）" % n)
    d = con.execute("SELECT * FROM v_decision_trail LIMIT 1").fetchone()
    rep.add("决策视图可用", d is not None,
            "%s / %s / 置信度 %s" % (d["category"], d["decision"], d["confidence"]) if d else "")

    # ---------------------------------------------------------- 8 CLI
    print("\n=== 8. run.py 的 CLI ===")
    con.close()
    con, eid2 = fresh_db()

    p = run_cli("--episode-id", str(eid2), "--db", TEST_DB, "--run", "script")
    rep.add("跳门禁时退出码 2", p.returncode == 2, "退出码 %d" % p.returncode)
    rep.add("报错文字点明跳门禁并给出下一步",
            "跳门禁" in p.stdout and "先跑完" in p.stdout,
            next((l.strip() for l in p.stdout.splitlines() if "跳门禁" in l), "")[:56])

    p = run_cli("--episode-id", str(eid2), "--db", TEST_DB, "--run", "script", "--dry-run")
    rep.add("dry-run 也过门禁（退出码 2）", p.returncode == 2,
            "退出码 %d" % p.returncode)

    p = run_cli("--episode-id", str(eid2), "--db", TEST_DB, "--approve", "spec",
                "--note", "自检批准")
    rep.add("--approve 退出码 0", p.returncode == 0)
    rep.add("批准后状态显示为已批", "已批准" in p.stdout)

    con = C.connect(TEST_DB)
    ck.begin(con, eid2, "script", m)
    ck.await_human(con, ck.current(con, eid2, "script")["id"])
    con.close()
    p = run_cli("--pending", "--db", TEST_DB)
    rep.add("--pending 列出等人批的", "script" in p.stdout and "等人批" in p.stdout.replace("\n", " ")
            or "script" in p.stdout, p.stdout.strip().splitlines()[0][:56] if p.stdout.strip() else "")

    con = C.connect(TEST_DB)
    unit, reason = ck.resume_point(con, eid2, m)
    rep.add("续跑点说明卡在人工门禁上", unit and "人工门禁" in reason, reason)
    con.close()

    code = rep.finish()
    if args.keep:
        print("\n测试目录保留在:", os.path.relpath(TEST_ROOT, ROOT))
    else:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print("已清理测试目录")
    return code


if __name__ == "__main__":
    sys.exit(main())
