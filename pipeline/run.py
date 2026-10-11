# -*- coding: utf-8 -*-
"""流水线统一入口。

在此之前，跑一集要记住八条命令、还要自己记住哪道门批过、从哪接上。
这个文件把那些收成一条：

    python pipeline/run.py --episode-id 1                # 现在走到哪、下一步是什么
    python pipeline/run.py --episode-id 1 --run script   # 跑一个阶段
    python pipeline/run.py --episode-id 1 --auto         # 一路跑到下一道人工门禁
    python pipeline/run.py --episode-id 1 --approve script --note "钩子够狠"
    python pipeline/run.py --pending                     # 有哪些在等人批

门禁是真的：前面有人工门禁没批准，`--run` 会直接报错退出（exit 2），
不是打印一句提醒然后照跑。

**每道门都是单独的。** 之前批过一次，不覆盖后面的门——这是
`skills/meta/checkpoint-protocol.md` 里定的规矩，由 checkpoint.guard 执行。
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C          # noqa: E402
from pipeline import checkpoint as ck     # noqa: E402
from pipeline import manifest as M        # noqa: E402

# 子阶段名 → 脚本的额外参数。stage_refs 用 --phase，stage_video 用 --mode。
PHASE_SUBSTAGES = {"characters", "scenes", "props"}
MODE_SUBSTAGES = {"draft", "direct", "final"}
MARK = {"completed": "[完成]", "in_progress": "[进行]", "awaiting_human": "[等人批]",
        "failed": "[失败]", "未开始": "[  --  ]"}

# run_one 的返回。**跳门禁必须与"正常停在门禁上"分开**——
# 前者是错误（退出码 2），后者是流水线的正常节奏（退出码 0）。
R_CONTINUE = "continue"
R_GATE = "gate"
R_BLOCKED = "blocked"
R_FAILED = "failed"
R_MANUAL = "manual"
R_DRY = "dry"


def command_for(unit, args):
    """这个单元该跑什么命令。没有工具的阶段（如 spec）返回 None，表示靠人做。"""
    tool = unit.tools[0] if unit.tools else None
    if not tool:
        return None
    script = os.path.join(C.ROOT, "pipeline", tool + ".py")
    cmd = [sys.executable, "-X", "utf8", script,
           "--episode-id", str(args.episode_id), "--db", args.db]
    if unit.name in PHASE_SUBSTAGES:
        cmd += ["--phase", unit.name]
    elif unit.name in MODE_SUBSTAGES:
        cmd += ["--mode", unit.name]
    return cmd


def cost_snapshot(con, episode_id):
    """花费快照。门禁摘要里必须带——人在批昂贵下游之前有权知道花了多少。"""
    try:
        r = con.execute("SELECT * FROM v_episode_cost WHERE episode_id=?",
                        (episode_id,)).fetchone()
    except Exception:
        return ""
    if not r:
        return ""
    d = dict(r)
    parts = []
    for k, label in (("video_cost", "视频"), ("image_cost", "图片")):
        if d.get(k) is not None:
            parts.append("%s ¥%.2f" % (label, d[k]))
    if d.get("total_cost") is not None:
        parts.append("合计 ¥%.2f" % d["total_cost"])
    return "  ".join(parts)


def show_status(con, args, m):
    s = ck.summary(con, args.episode_id, m)
    ep = con.execute("SELECT e.*, p.name AS project_name FROM episode e "
                     "JOIN project p ON p.id=e.project_id WHERE e.id=?",
                     (args.episode_id,)).fetchone()
    title = ep["project_name"] if ep else "(未知项目)"
    print("第 %d 集 —— %s" % (args.episode_id, title))
    print("清单：%s v%s   单集预算 ¥%s\n" % (m.name, m.version, m.budget_default))
    print("%-16s %-10s %-8s %s" % ("阶段", "状态", "批准", "产出"))
    print("-" * 72)
    for r in s["rows"]:
        u = r["unit"]
        print("%-16s %-10s %-8s %s"
              % (r["key"], MARK.get(r["status"], r["status"]),
                 "是" if r["approved"] else ("需批" if r["gate"] else "—"),
                 ",".join(u.produces) or "-"))
    print("-" * 72)
    print("进度 %d/%d" % (s["completed"], s["total"]))
    if s["next"]:
        gate = "（有人工门禁，跑完后要你批准）" if s["next_gate"] else "（自动）"
        print("下一步：%s  %s" % (s["next"], gate))
        print("原因：%s" % s["reason"])
        if s["next_gate"]:
            # 明确写给 agent 看的一行。**没有这行的时候，agent 会把
            # "下一步" 当成待办事项直接执行**——而那会绕过人工门禁。
            print("      ⚠️ 这一阶段需要人工批准，**不要自动执行**它。")
        nxt = m.unit(s["next"])
        if nxt.review_focus:
            print("\n这一阶段要审的：")
            for f in nxt.review_focus[:4]:
                print("  · %s" % f)
    else:
        print("十个阶段全部完成。")
    snap = cost_snapshot(con, args.episode_id)
    if snap:
        print("\n花费：%s" % snap)
    for row in ck.pending(con, args.episode_id):
        print("\n⚠ 有东西在等你批准：%s/%s（存档 #%d）"
              % (row["stage"], row["substage"] or "-", row["id"]))


def run_one(con, args, m, key, quiet=False):
    """跑一个单元。返回 R_* 常量。"""
    u = m.unit(key)
    cmd = command_for(u, args)

    if cmd is None:
        print("\n阶段 %s 没有自动化工具——它靠人做。" % key)
        print("做完后用 --approve %s 标记，门禁才会放行。" % key)
        if not quiet:
            print("\n要做的：")
            for f in u.review_focus:
                print("  · %s" % f)
        return R_MANUAL

    print("\n" + "=" * 72)
    print("阶段 %s%s" % (key, "（有人工门禁）" if u.human_approval else ""))
    print("  产出：%s" % (",".join(u.produces) or "-"))
    if u.human_approval:
        # ⚠️ **有门禁的阶段不打印可执行命令。**
        #
        # 依据 Agnes Comic Studio 的行为准则：
        #   「脚本末尾打印的『下一步』是给用户看的操作提示，
        #     **Agent 不应忽略它直接链式执行**。」
        #
        # 我们原来在这里无条件打印 `命令：python …stage_xxx.py …`。
        # 一个 agent 看到这行，完全可能直接执行它——而**门禁检查在 run.py 里，
        # 不在 stage 脚本里**，所以直接跑脚本等于绕过整道门。
        # 实测：本项目的 agent 自己就这么干过好几次。
        print("  执行：本阶段由人工门禁把关，**不在这里给出可执行命令**")
        print("        （门禁检查在 run.py，直接跑 stage 脚本会绕过它）")
        print("        批准上一阶段后再从 run.py 进入，或由操作者手工执行")
    else:
        print("  命令：python %s" % " ".join(cmd[3:]))   # cmd = [python, -X, utf8, 脚本, ...]
        print("        ↑ 供**操作者**手工执行；Agent 不要据此自动链式跑下去")
    print("=" * 72)
    # 门禁先过，**dry-run 也要过**——否则试跑看不出自己会被拦，
    # 而"试跑一下看看"正是最容易跳过门禁的动作。
    try:
        ck.guard(con, args.episode_id, key, m)
    except ck.GateViolation as e:
        print("\n%s" % e)
        return R_BLOCKED
    if args.dry_run:
        print("(dry-run：门禁通过，命令未执行)")
        return R_DRY

    # 再落存档。门禁已在上一步验过，这里不会留下脏的 in_progress。
    cp = ck.begin(con, args.episode_id, key, m)
    print("存档 #%d 已开（in_progress）——中断也能从这里接上。" % cp)

    r = subprocess.run(cmd, cwd=C.ROOT)
    if r.returncode != 0:
        ck.fail(con, cp, "退出码 %d" % r.returncode)
        print("\n阶段 %s 失败（退出码 %d）。存档已标记 failed，修好后重跑会从这里接上。"
              % (key, r.returncode))
        return R_FAILED

    meta = {"cost": cost_snapshot(con, args.episode_id)}
    if u.human_approval:
        ck.await_human(con, cp, meta)
        print("\n" + "-" * 72)
        print("阶段 %s 已完成，**在等你批准**。" % key)
        print("-" * 72)
        print("\n产出：%s" % (",".join(u.produces) or "-"))
        if u.review_focus:
            print("\n要审的：")
            for f in u.review_focus:
                print("  · %s" % f)
        if meta["cost"]:
            print("\n花费：%s" % meta["cost"])
        print("\n批准： python pipeline\\run.py --episode-id %d --approve %s --note \"...\""
              % (args.episode_id, key))
        return R_GATE

    ck.finish(con, cp, metadata=meta)
    print("\n阶段 %s 完成（自动放行）。" % key)
    return R_CONTINUE


def main():
    ap = argparse.ArgumentParser(description="短剧流水线入口")
    ap.add_argument("--episode-id", type=int)
    ap.add_argument("--db", default=C.DEFAULT_DB)
    ap.add_argument("--status", action="store_true", help="看进度（默认行为）")
    ap.add_argument("--run", metavar="阶段", help="跑一个阶段，如 script 或 art/characters")
    ap.add_argument("--auto", action="store_true", help="一路跑到下一道人工门禁")
    ap.add_argument("--approve", metavar="阶段", help="批准一道门")
    ap.add_argument("--note", help="批准备注")
    ap.add_argument("--pending", action="store_true", help="列出所有在等人批的")
    ap.add_argument("--dry-run", action="store_true",
                    help="只过门禁、只打印命令，不真跑")
    args = ap.parse_args()

    con = C.connect(args.db)
    m = M.load()

    probs, warns = m.validate()
    if probs:
        print("清单有问题，先修：")
        for p in probs:
            print("  · %s" % p)
        return 1

    if args.pending:
        rows = ck.pending(con)
        if not rows:
            print("没有在等人批的东西。")
        for r in rows:
            print("第 %d 集  %s/%s  存档 #%d  起于 %s"
                  % (r["episode_seq"], r["stage"], r["substage"] or "-",
                     r["id"], r["started_at"]))
        return 0

    if args.episode_id is None:
        ap.error("需要 --episode-id（或用 --pending）")

    if args.approve:
        ck.approve(con, args.episode_id, args.approve, args.note, m)
        print("已批准：%s" % args.approve)
        if args.note:
            print("备注已记入 decision_log：%s" % args.note)
        print()
        show_status(con, args, m)
        return 0

    if args.run:
        try:
            m.unit(args.run)
        except KeyError as e:
            print(e)
            return 1
        r = run_one(con, args, m, args.run)
        if r == R_BLOCKED:
            return 2
        if r == R_FAILED:
            return 1
        return 0

    if args.auto:
        while True:
            nxt = ck.next_unit(con, args.episode_id, m)
            if nxt is None:
                print("\n全部完成。")
                return 0
            r = run_one(con, args, m, nxt["key"])
            if r == R_CONTINUE:
                continue
            print()
            show_status(con, args, m)
            if r == R_BLOCKED:
                return 2
            if r == R_FAILED:
                return 1
            return 0

    show_status(con, args, m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
