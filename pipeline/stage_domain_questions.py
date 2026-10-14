# -*- coding: utf-8 -*-
r"""第 1.5 阶段：从剧本提取「领域问题清单」。

## 为什么要有这个阶段

第 1 集的失败模式很隐蔽：我在第 2 阶段**根本不知道自己不懂**殡仪馆怎么运作，
于是直接编——而且编得很自信、格式很规范，看起来像事实。等看出问题，已经
出了图、出了首帧。

这个阶段把"我不知道"变成**可见的、必须回答的清单**。它不负责查证（查证需要
外部资料），只负责**把该问的问题问出来**，并卡住下游直到有人回答。

## 产物

写进 `knowledge/03-项目档案/<剧名>.md` 的 `## 领域问题` 章：

| 编号 | 问题 | 为什么影响画面 | 回答 | 出处 |
|---|---|---|---|---|
| Q-01 | 遗体化妆间和冷柜间是同一个空间吗？ | 决定场景空间是否成立 | 否，两处分开 | 用户指出 |

**回答留空 = 未解决 → 第 2 阶段阻断。**

## 与门禁的关系

- 问题清单：本阶段产出，`## 领域问题` 章
- 事实表：人把答案整理进 `## 领域事实` 章（带出处与置信度）
- 门禁：第 2 阶段查「有没有未回答的问题」+「已确认事实能否追到某条问题」

这样"编出来的事实"没有来源问题可指，会被结构性挡住。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

PROMPT = os.path.join(C.ROOT, "pipeline", "prompts", "domain_questions.md")
PROJECT_DIR = os.path.join(C.ROOT, "knowledge", "03-项目档案")

SECTION = "领域问题"
QID = re.compile(r"^Q-\d+$")
# 表格里表示"没回答"的写法，都算未解决
UNANSWERED = ("", "-", "—", "待查", "未查", "待确认", "未回答", "?", "？", "TBD")
# 明确声明"与画面无关，不必回答"的写法
WAIVED = ("无关", "不适用", "waive", "n/a", "N/A")

SPEC = {
    "questions": [{
        "question": str,
        "why": str,
        "affects": str,
    }],
}


def brief_path(project):
    return os.path.join(PROJECT_DIR, "%s.md" % project)


def parse_questions(path):
    """解析 `## 领域问题` 章。返回 (rows, ok, why)。

    rows: [{id, question, why, answer, source, answered, waived}]
    """
    if not os.path.exists(path):
        return [], False, "项目档案不存在：%s" % path
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^##\s*%s\s*$(.*?)(?=^##\s|\Z)" % SECTION, text, re.M | re.S)
    if not m:
        return [], False, "项目档案里没有「## %s」这一章" % SECTION
    body = m.group(1)
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # ⚠️ 只要**至少 4 列**（编号/问题/回答/出处）就算一条。
        #    原来是硬要求 5 列（含"为什么影响画面"），结果人手把那一列合并进
        #    问题描述后，整张表就解析不出来了——**报错在解析器，错在解析器太死**。
        #    第 5 列（为什么）当可选。
        if len(cells) < 4 or not QID.match(cells[0]):
            continue
        ans = cells[2]
        src = cells[3]
        waived = any(w.lower() in ans.lower() for w in WAIVED)
        answered = waived or (ans not in UNANSWERED and len(ans) >= 2)
        rows.append({"id": cells[0], "question": cells[1], "why": "",
                     "answer": ans, "source": src,
                     "answered": answered, "waived": waived})
    if not rows:
        return [], False, ("「%s」章里没有可解析的表格行"
                           "（第一列要是 Q-01 这样的编号）" % SECTION)
    return rows, True, "%d 条" % len(rows)


def unanswered(rows):
    return [r for r in rows if not r["answered"]]


# ---------------------------------------------------------------- 写回档案
def write_section(path, questions, dry):
    """把问题清单写进档案的 `## 领域问题` 章。

    **只替换这一章，不碰文件其余部分**——档案里有大量手工写的内容
    （领域事实、决策、立项备忘），整体重写会毁掉它们。
    已存在的编号**保留原回答**，只新增缺失的。
    """
    if not os.path.exists(path):
        return False, "项目档案不存在：%s" % path
    with open(path, encoding="utf-8") as f:
        text = f.read()

    existing, _ok, _why = parse_questions(path)
    by_q = {r["question"].strip(): r for r in existing}

    lines = ["| 编号 | 问题 | 回答 | 出处 |",
             "|---|---|---|---|"]
    added = 0
    for i, q in enumerate(questions, 1):
        qid = "Q-%02d" % i
        old = by_q.get(q["question"].strip())
        if old:                       # 同一个问题：保留人的回答
            ans, src = old["answer"], old["source"]
        else:
            ans, src = "待查", ""
            added += 1
        # "为什么影响画面" 并进问题描述——**不单独占一列**，
        # 因为列越多，人手维护时越容易把表改坏（实测踩过）。
        qtext = q["question"].strip()
        why = (q.get("why") or "").strip()
        if q.get("affects"):
            why = ("%s（%s）" % (why, q["affects"])).strip("（）")
        if why:
            qtext = "%s<br>**为什么**：%s" % (qtext, why)
        lines.append("| %s | %s | %s | %s |" % (qid, qtext, ans, src))
    section = "## %s\n\n%s\n" % (SECTION, "\n".join(lines))

    body = re.sub(r"^##\s*%s\s*$.*?(?=^##\s|\Z)" % SECTION,
                  section, text, flags=re.M | re.S)
    if body == text and ("## %s" % SECTION) not in text:
        # 章不存在：插在「## 领域事实」之前（问题在前、事实在后，读起来顺）
        anchor = re.search(r"^##\s*领域事实\s*$", text, re.M)
        if anchor:
            body = text[:anchor.start()] + section + "\n" + text[anchor.start():]
        else:
            body = text.rstrip() + "\n\n" + section

    if not dry:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    return True, "新增 %d 条问题" % added


def build_prompt(con, ep):
    row = con.execute("SELECT content FROM script WHERE episode_id=? AND is_current=1",
                      (ep["id"],)).fetchone()
    if not row:
        raise L.LLMError("第 %s 集还没有现行剧本，先跑第 1 阶段" % ep["seq"])
    scenes = con.execute(
        "SELECT s.seq, s.slug, s.action, s.dialogue FROM scene s "
        "JOIN script t ON t.id=s.script_id WHERE t.episode_id=? AND t.is_current=1 "
        "ORDER BY s.seq", (ep["id"],)).fetchall()
    digest = "\n".join("- 第%d场 %s：%s%s"
                       % (s["seq"], s["slug"], s["action"] or "",
                          ("  台词：" + s["dialogue"]) if s["dialogue"] else "")
                       for s in scenes)
    return L.load_prompt(PROMPT, script_content=row["content"], scenes_digest=digest)


def main():
    ap = argparse.ArgumentParser(description="第 1.5 阶段：提取领域问题清单")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--write", action="store_true",
                    help="写进项目档案的「领域问题」章（默认只打印）")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 1.5 阶段 · 领域问题清单")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    path = brief_path(ep["project"])
    try:
        prompt = build_prompt(con, ep)
    except L.LLMError as e:
        rep.add("现行剧本存在", False, str(e))
        con.close()
        return rep.finish()

    if args.dump_prompt:
        print(prompt)
        con.close()
        return 0

    try:
        obj = L.chat_json([{"role": "user", "content": prompt}], SPEC, "领域问题",
                          base_url=args.base_url, model=args.model)

        try:
            _n, _amt = L.flush_usage(con, ep["id"])
            if _n:
                rep.info("LLM 用量已记账：%d 次调用 ¥%.2f" % (_n, _amt))
        except Exception as _e:      # 记账失败不影响本阶段结果
            rep.info("LLM 记账失败（不影响结果）：%s" % _e)
    except L.LLMError as e:
        rep.add("产出可用", False, str(e)[:100])
        print("\n" + str(e))
        con.close()
        return rep.finish()

    qs = obj["questions"]
    rep.add("提取到领域问题", bool(qs), "%d 条" % len(qs))
    for i, q in enumerate(qs, 1):
        print("  Q-%02d  %s" % (i, q["question"]))
        print("        为什么：%s" % q["why"])
        if q.get("affects"):
            print("        影响：%s" % q["affects"])

    if args.write:
        ok, why = write_section(path, qs, args.dry_run)
        rep.add("已写进项目档案", ok, why)
        if not ok:
            rep.info("先建档案：knowledge/03-项目档案/%s.md" % ep["project"])
    else:
        rep.info("只打印，未写档案。加 --write 写进「## 领域问题」章。")

    # 现状：还有多少未回答
    rows, rok, rwhy = parse_questions(path)
    if rok:
        left = unanswered(rows)
        rep.add("清单当前状态", not left,
                "全部已答" if not left else "%d 条未回答（第 2 阶段会阻断）" % len(left))
    else:
        rep.add("清单当前状态", False, rwhy)

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
