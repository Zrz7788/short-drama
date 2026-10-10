# -*- coding: utf-8 -*-
"""门禁、存档与决策记录 —— 流水线的执行层。

在此之前，`pipeline_defs/short-drama.yaml` 里的 `human_approval_default: true`
只是一行文档：没有任何东西会因为跳过它而报错。这个模块让那些声明**变成约束**。

三件事：

  1. **存档**（checkpoint）—— 每阶段的状态落库，支持从失败点续跑
  2. **门禁**（gate）—— 前面有人工门禁的阶段没批准，就不许开始下一个；
     需要人批的阶段不许自己写成 completed
  3. **决策记录**（decision_log）—— 每次选择的备选与理由

关键设计：
  - **被取代的存档保留不删**（revision 递增）。"门禁转移与阶段版本永不销毁"
    必须是真的，否则审计无从谈起。
  - **门禁逐道绑定**：批准是逐阶段的。之前批过一次，不覆盖后面的门。
  - **进入阶段先写 in_progress**：这是告诉人能续跑、也是告诉看板"还活着"。
"""
import json
from datetime import datetime

from pipeline import manifest as M


class GateViolation(Exception):
    """跳门禁。这是一条硬错误，不是一个警告。"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------- 存档

def current(con, episode_id, unit_key):
    """某单元的最新一条存档。没有则 None。"""
    stage, sub = _split(unit_key)
    return con.execute(
        "SELECT * FROM checkpoint WHERE episode_id=? AND stage=? AND substage=? "
        "ORDER BY revision DESC LIMIT 1", (episode_id, stage, sub)).fetchone()


def _split(key):
    if "/" in key:
        a, b = key.split("/", 1)
        return a, b
    return key, ""


def _next_revision(con, episode_id, unit_key):
    stage, sub = _split(unit_key)
    r = con.execute("SELECT COALESCE(MAX(revision),0) AS n FROM checkpoint "
                    "WHERE episode_id=? AND stage=? AND substage=?",
                    (episode_id, stage, sub)).fetchone()
    return r["n"] + 1


def begin(con, episode_id, unit_key, m=None, metadata=None, force=False):
    """进入一个阶段。**先过门禁，再落存档。**

    返回新的 checkpoint id。
    """
    m = m or M.load()
    if not force:
        guard(con, episode_id, unit_key, m)
    stage, sub = _split(unit_key)
    rev = _next_revision(con, episode_id, unit_key)
    cur = con.execute(
        "INSERT INTO checkpoint (episode_id, stage, substage, status, revision, "
        "metadata, started_at) VALUES (?,?,?,?,?,?,?)",
        (episode_id, stage, sub, "in_progress", rev,
         json.dumps(metadata or {}, ensure_ascii=False), _now()))
    con.commit()
    return cur.lastrowid


def finish(con, cp_id, artifacts=None, metadata=None, approved=False, m=None):
    """把存档置为 completed。

    **有人工门禁的阶段，未获批准时拒绝写成 completed。**

    ⚠️ 这条一开始写错了：只在状态是 awaiting_human 时才拦。结果一个有人工门禁、
    但还停在 in_progress 的存档能被直接标成 completed——**批准被整个绕过**。
    自检逮住了。判据必须来自清单的声明，而不是存档碰巧处于哪个状态。
    """
    row = con.execute("SELECT * FROM checkpoint WHERE id=?", (cp_id,)).fetchone()
    if row is None:
        raise GateViolation("没有这条存档：%s" % cp_id)

    m = m or M.load()
    key = row["stage"] + ("/" + row["substage"] if row["substage"] else "")
    try:
        needs_approval = m.unit(key).human_approval
    except KeyError:
        needs_approval = False
    if needs_approval and not (approved or row["human_approved"]):
        raise GateViolation(
            "存档 #%d（%s）带人工门禁，还没被批准，不能写成 completed。\n"
            "  批准： python pipeline\\run.py --episode-id %d --approve %s --note \"...\"\n"
            "  （或显式传 approved=True 表示你本人此刻就是在批准）"
            % (cp_id, key, row["episode_id"], key))

    con.execute(
        "UPDATE checkpoint SET status='completed', artifacts=?, metadata=?, "
        "finished_at=?, human_approved=MAX(human_approved, ?) WHERE id=?",
        (json.dumps(artifacts or {}, ensure_ascii=False),
         json.dumps(metadata or {}, ensure_ascii=False),
         _now(), 1 if approved else 0, cp_id))
    con.commit()


def await_human(con, cp_id, metadata=None):
    """置为等人批准。**这是停下等人的那个动作。**"""
    con.execute("UPDATE checkpoint SET status='awaiting_human', metadata=?, "
                "finished_at=? WHERE id=?",
                (json.dumps(metadata or {}, ensure_ascii=False), _now(), cp_id))
    con.commit()


def fail(con, cp_id, error):
    con.execute("UPDATE checkpoint SET status='failed', metadata=?, finished_at=? "
                "WHERE id=?",
                (json.dumps({"error": str(error)[:2000]}, ensure_ascii=False),
                 _now(), cp_id))
    con.commit()


def approve(con, episode_id, unit_key, note=None, m=None):
    """人批准一道门。当前存档不存在时先补一条。"""
    m = m or M.load()
    stage, sub = _split(unit_key)
    row = current(con, episode_id, unit_key)
    if row is None:
        rev = _next_revision(con, episode_id, unit_key)
        con.execute(
            "INSERT INTO checkpoint (episode_id, stage, substage, status, revision, "
            "started_at) VALUES (?,?,?,?,?,?)",
            (episode_id, stage, sub, "completed", rev, _now()))
        row = current(con, episode_id, unit_key)
    con.execute(
        "UPDATE checkpoint SET human_approved=1, approved_at=?, approved_note=?, "
        "status=CASE WHEN status='awaiting_human' THEN 'completed' ELSE status END "
        "WHERE id=?", (_now(), note, row["id"]))
    con.commit()
    decide(con, episode_id, unit_key, "approval", "人工批准",
           rationale=note or "无备注", confidence="high", source="人工")
    return row["id"]


# ----------------------------------------------------------------- 门禁

def guard(con, episode_id, unit_key, m):
    """能不能开始这个阶段。不能就抛 GateViolation。

    两条判据，都是清单里的声明：
      1. 排在它前面的单元必须都已 completed
      2. 前面带人工门禁的单元必须已 human_approved
    """
    units = m.run_units()
    keys = [u.key for u in units]
    if unit_key not in keys:
        raise GateViolation("清单里没有这个阶段：%s" % unit_key)
    idx = keys.index(unit_key)

    for u in units[:idx]:
        row = current(con, episode_id, u.key)
        if row is None or row["status"] != "completed":
            raise GateViolation(
                "跳门禁：要开始 %s，但它前面的 %s 还没完成（当前状态：%s）。\n"
                "  流水线必须按顺序走；先跑完 %s。"
                % (unit_key, u.key, row["status"] if row else "未开始", u.key))
    for u in units[:idx]:
        if not u.human_approval:
            continue
        row = current(con, episode_id, u.key)
        if not row["human_approved"]:
            raise GateViolation(
                "跳门禁：%s 有人工门禁，但还没被批准。\n"
                "  门禁是逐道绑定的——之前批过别的门不覆盖这一道。\n"
                "  批准： python pipeline\\run.py --episode-id %d --approve %s --note \"...\""
                % (u.key, episode_id, u.key))


def status(con, episode_id, m=None):
    """每个运行单元的当前状态，按执行顺序。"""
    m = m or M.load()
    out = []
    for u in m.run_units():
        row = current(con, episode_id, u.key)
        out.append({
            "unit": u,
            "key": u.key,
            "status": row["status"] if row else "未开始",
            "approved": bool(row["human_approved"]) if row else False,
            "revision": row["revision"] if row else 0,
            "finished_at": row["finished_at"] if row else None,
            "gate": u.human_approval,
        })
    return out


def next_unit(con, episode_id, m=None):
    """下一个该跑的单元。全都完成了则 None。"""
    m = m or M.load()
    for row in status(con, episode_id, m):
        if row["status"] != "completed":
            return row
    return None


def pending(con, episode_id=None):
    """正在等人批准的。执行层每次开工前该先看这个。"""
    q = ("SELECT e.seq AS episode_seq, c.* FROM checkpoint c "
         "JOIN episode e ON e.id=c.episode_id "
         "WHERE c.status='awaiting_human' AND c.human_approved=0")
    args = ()
    if episode_id is not None:
        q += " AND c.episode_id=?"
        args = (episode_id,)
    return con.execute(q + " ORDER BY c.episode_id, c.id", args).fetchall()


# ----------------------------------------------------------------- 决策记录

def decide(con, episode_id, stage, category, decision, alternatives=None,
           rationale=None, confidence="medium", source=None):
    """记一条决策。备选与理由比结论本身更重要——半年后回看，你要的是"当时为什么"。"""
    con.execute(
        "INSERT INTO decision_log (episode_id, stage, category, decision, "
        "alternatives, rationale, confidence, source, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (episode_id, stage, category, decision,
         json.dumps(alternatives or [], ensure_ascii=False),
         rationale, confidence, source, _now()))
    con.commit()


# ----------------------------------------------------------------- 续跑

def resume_point(con, episode_id, m=None):
    """这一集该从哪继续。

    返回 (unit, reason)。全完成时 unit 为 None。
    这是"存档是为了续跑"那条原则的落地点：崩在 motion，就从 motion 接上，
    不是从 script 重来。
    """
    m = m or M.load()
    rows = status(con, episode_id, m)
    for r in rows:
        if r["status"] == "completed":
            continue
        if r["status"] == "awaiting_human" and not r["approved"]:
            return r, "卡在人工门禁上，等人批准"
        if r["status"] == "in_progress":
            return r, "上次跑到这里中断了（有 in_progress 存档）"
        if r["status"] == "failed":
            return r, "上次在这里失败"
        return r, "还没开始"
    return None, "全部完成"


def summary(con, episode_id, m=None):
    """给人看的一行式进度摘要。"""
    m = m or M.load()
    rows = status(con, episode_id, m)
    done = sum(1 for r in rows if r["status"] == "completed")
    unit, reason = resume_point(con, episode_id, m)
    return {
        "total": len(rows),
        "completed": done,
        "next": unit["key"] if unit else None,
        "next_gate": bool(unit and unit["gate"]) if unit else False,
        "reason": reason,
        "rows": rows,
    }
