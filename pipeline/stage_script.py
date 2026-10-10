# -*- coding: utf-8 -*-
"""第 1 阶段：生成剧本。

产出写入 script（含版本管理）与 scene 两张表。

三种用法：
  # 调 LLM
  python pipeline/stage_script.py --episode-id 1 --brief "一个关于…的故事"

  # agent 驱动：跳过调用，直接读入外部产出的 JSON
  python pipeline/stage_script.py --episode-id 1 --brief "…" --from-json r.json

  # 只看会发出去的 prompt
  python pipeline/stage_script.py --episode-id 1 --brief "…" --dump-prompt
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

PROMPT = os.path.join(C.ROOT, "pipeline", "prompts", "script.md")

BEATS = {"钩子", "建立", "升级", "高潮", "扣子"}

SPEC = {
    "title": str,
    "logline": str,
    "content": str,
    "scenes": [{
        "seq": int,
        "slug": str,
        "location": str,
        "time_of_day": str,
        "action": str,
        "dialogue": L.OPTIONAL_STR,
        "beat": str,
        "duration_s": float,
    }],
}


def build_prompt(con, ep, brief):
    prev = con.execute(
        "SELECT e.seq, s.logline FROM episode e "
        "JOIN script s ON s.episode_id = e.id AND s.is_current = 1 "
        "WHERE e.project_id = ? AND e.seq < ? ORDER BY e.seq DESC LIMIT 1",
        (ep["project_id"], ep["seq"])).fetchone()
    if prev:
        recap = ("## 上一集回顾\n\n第 %s 集：%s\n请让本集与它衔接，"
                 "但开头仍要有独立成立的钩子（观众可能跳集）。" % (prev["seq"], prev["logline"]))
    else:
        recap = "## 说明\n\n这是全剧第一集，需要建立世界观与主要人物。"
    return L.load_prompt(PROMPT,
                         episode_seq=ep["seq"],
                         episode_seconds=80,
                         aspect="9:16",
                         brief=brief,
                         prev_recap=recap)


def persist(con, ep, data, dry):
    scenes = data["scenes"]
    bad_beats = sorted({s["beat"] for s in scenes} - BEATS)
    if bad_beats:
        raise L.LLMError("beat 取值非法：%s（只允许 %s）"
                         % (bad_beats, "/".join(sorted(BEATS))))
    seqs = [s["seq"] for s in scenes]
    if seqs != list(range(1, len(scenes) + 1)):
        raise L.LLMError("scene.seq 必须从 1 连续编号，实际：%s" % seqs)

    total = sum(float(s["duration_s"]) for s in scenes)
    if dry:
        return {"version": None, "scenes": len(scenes), "seconds": total}

    row = con.execute("SELECT COALESCE(MAX(version), 0) AS v FROM script WHERE episode_id = ?",
                      (ep["id"],)).fetchone()
    version = row["v"] + 1
    con.execute("UPDATE script SET is_current = 0 WHERE episode_id = ?", (ep["id"],))
    sid = con.execute(
        "INSERT INTO script (episode_id, version, title, logline, content,"
        " word_count, is_current) VALUES (?,?,?,?,?,?,1)",
        (ep["id"], version, data["title"], data["logline"], data["content"],
         len(data["content"]))).lastrowid
    for s in scenes:
        con.execute(
            "INSERT INTO scene (script_id, seq, slug, location, time_of_day,"
            " action, dialogue, beat, duration_s) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, s["seq"], s["slug"], s["location"], s["time_of_day"],
             s["action"], s.get("dialogue") or "", s["beat"], float(s["duration_s"])))
    if not ep["title"]:
        con.execute("UPDATE episode SET title = ? WHERE id = ?", (data["title"], ep["id"]))
    con.execute("UPDATE episode SET status = 'scripted' WHERE id = ?", (ep["id"],))
    con.commit()
    return {"version": version, "scenes": len(scenes), "seconds": total}


def main():
    ap = argparse.ArgumentParser(description="第 1 阶段：生成剧本")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--brief", required=True, help="创意梗概：题材、人物关系、核心冲突")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    rep = C.Report("第 1 阶段 · 生成剧本")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    prompt = build_prompt(con, ep, args.brief)
    if args.dump_prompt:
        print(prompt)
        con.close()
        return 0

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            obj = json.load(f)
        rep.info("来源：%s（跳过模型调用）" % args.from_json)
    else:
        obj = L.chat_json([{"role": "user", "content": prompt}], SPEC, "剧本",
                          base_url=args.base_url, model=args.model)
        rep.info("来源：模型调用")

    rep.add("结构校验通过", True, "title/logline/content/scenes")
    info = persist(con, ep, obj, args.dry_run)
    rep.add("场数", True, "%d 场" % info["scenes"])
    rep.add("各场时长合计接近目标 80 秒",
            abs(info["seconds"] - 80) <= 20, "%.0f 秒" % info["seconds"])
    if args.dry_run:
        rep.info("dry-run：未写库")
    else:
        rep.add("已写入 script（版本 v%s）与 scene" % info["version"], True)

    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
