# -*- coding: utf-8 -*-
"""第 1 阶段：生成剧本。

产出写入 script（含版本管理）与 scene 两张表。

**两条通道**：

  ① 调 LLM 吐 JSON（旧通道，本文件内的 build_prompt/persist）。
     依赖手写的 pipeline/prompts/script.md，它第 6 行把「场」定义成 4–15 秒的
     「镜头」，是剧本被切成碎片的根因（见 HANDOFF §四）。保留只为兼容自检。

  ② `--from-md`：**agent 按 short-drama-write 技能写 Markdown，本阶段只导入**。
     剧本真源是 `剧集/EP###/剧本.md`，解析、校验、写库在 pipeline/md_import.py。
     创作类阶段要判断和迭代，不能靠一次 LLM 调用；生成类阶段才保持纯脚本。

用法：
  # ② agent + skill（推荐）
  python pipeline/stage_script.py --episode-id 1 --from-md 剧集/EP001/剧本.md
  python pipeline/stage_script.py --episode-id 1 --from-md 剧集/EP001/剧本.md --dry-run
  python pipeline/stage_script.py --episode-id 1 --from-md 剧集/EP001/剧本.md --beat 钩子,扣子

  # ① 调 LLM（旧）
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
from pipeline import md_import as MDI  # noqa: E402

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
    ap.add_argument("--brief", default=None,
                    help="创意梗概。不传则取 project.logline（真源）")
    ap.add_argument("--from-md", dest="from_md", default=None,
                    help="② agent + skill 通道：导入技能格式的剧本 Markdown")
    ap.add_argument("--beat", default=None,
                    help="覆盖导入时推断的 beat，逗号分隔按场序，如 钩子,扣子")
    L.add_llm_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)

    # ② agent + skill 通道：剧本真源是 Markdown 文件，本阶段只导入。
    #    放在取 brief 之前——导入不需要 brief，logline 反而该由剧本反填。
    if args.from_md:
        ep = C.episode_info(con, args.episode_id)
        if not ep:
            print("找不到集:", args.episode_id)
            con.close()
            return 2
        beats = [b.strip() for b in args.beat.split(",")] if args.beat else None
        try:
            return MDI.run(con, ep, args.from_md, args.dry_run, beats)
        finally:
            con.close()

    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    rep = C.Report("第 1 阶段 · 生成剧本")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    # brief 不传就从 project.logline 取——数据库本来就是真源，
    # 而 logline 在 spec 阶段就写好了。这样 run.py 之类的编排层不必
    # 知道每个阶段有哪些参数（原来必须传 --brief，编排直接跑不起来）。
    brief = args.brief or (ep["logline"] or "").strip()
    if not brief:
        print("没有创意梗概：--brief 没传，project.logline 也是空的。")
        print("  去填 project.logline（真源），或直接传 --brief。")
        con.close()
        return 2
    if not args.brief:
        rep.info("brief 取自 project.logline：%s" % brief[:60])

    prompt = build_prompt(con, ep, brief)
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

        try:
            _n, _amt = L.flush_usage(con, ep["id"])
            if _n:
                rep.info("LLM 用量已记账：%d 次调用 ¥%.2f" % (_n, _amt))
        except Exception as _e:      # 记账失败不影响本阶段结果
            rep.info("LLM 记账失败（不影响结果）：%s" % _e)
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
