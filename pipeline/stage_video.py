# -*- coding: utf-8 -*-
"""第 8 阶段：视频生成。整条管线的最后一块。

三种模式对应 Draft 工作流的三段：

  --mode draft    给 strategy='draft' 的镜头出 480P 样片，每镜抽 draws_planned 次
  --mode direct   给 strategy='direct' 的镜头直出成片（正片分辨率）
  --mode final    对选中的样片出 1080P 成片（复用样片参数）

为什么要分三段而不是一键到底：样片便宜、成片贵。**把选择留在便宜的那一段。**
draft 阶段可以放心多抽，final 阶段每个镜头只出一次。

出片用的输入图按这个顺序解析：
  1. 该镜 `continues_from` 有值 → 用**上一镜的尾帧**（续接，保证跨镜头连续）
  2. 否则用该镜的 `keyframe_path`（第 7 阶段生成的关键帧）
  3. 都没有 → 纯文生视频（质量最差，会给出提示）

**花钱保护**：`--max-cost` 超过预算直接中止，必须先看 `--dry-run` 的张数与金额。

用法：
  python pipeline/stage_video.py --episode-id 1 --mode draft --dry-run
  python pipeline/stage_video.py --episode-id 1 --mode draft --max-cost 200
  python pipeline/stage_video.py --episode-id 1 --mode direct
  python pipeline/stage_video.py --episode-id 1 --mode final --pick 3:2
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402

# price_book 里的档位名
VARIANT_DRAFT = "Draft 样片"
VARIANT_FINAL = "旗舰"


def video_path(ep, shot_seq, attempt):
    p = os.path.join(C.assets_root(), "videos",
                     "E%02dS%03d_a%02d.mp4" % (ep["seq"], shot_seq, attempt))
    return os.path.relpath(p, C.ROOT).replace("\\", "/")


def price_of(con, model_id, variant):
    r = con.execute("SELECT unit_price FROM price_book WHERE model_id=? AND variant=?",
                    (model_id, variant)).fetchone()
    return r["unit_price"] if r else None


def resolve_input(con, shot):
    """按 续接 > 关键帧 > 无 的顺序解析出片输入图。"""
    if shot["continues_from"]:
        src = con.execute("SELECT seq, last_frame_path FROM shot WHERE id=?",
                          (shot["continues_from"],)).fetchone()
        if src and src["last_frame_path"]:
            if os.path.exists(C.abs_path(src["last_frame_path"])):
                return src["last_frame_path"], "续接 S%02d 的尾帧" % src["seq"]
            return None, "续接源 S%02d 的尾帧文件缺失（%s）" % (src["seq"],
                                                              src["last_frame_path"])
    if shot["keyframe_path"]:
        if os.path.exists(C.abs_path(shot["keyframe_path"])):
            return shot["keyframe_path"], "关键帧"
        return None, "关键帧文件缺失（%s）" % shot["keyframe_path"]
    return None, "无输入图（纯文生视频，一致性最差）"


def pick_draft(con, shot_id, pick):
    """返回要作为成片基准的样片记录。pick 形如 (shot_seq, attempt)。"""
    if pick:
        r = con.execute(
            "SELECT * FROM generation WHERE shot_id=? AND attempt_no=? AND status='succeeded'",
            (shot_id, pick[1])).fetchone()
        return dict(r) if r else None
    r = con.execute(
        "SELECT * FROM generation WHERE shot_id=? AND is_draft=1 AND status='succeeded' "
        "ORDER BY attempt_no LIMIT 1", (shot_id,)).fetchone()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser(description="第 8 阶段：视频生成")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--mode", choices=("draft", "direct", "final"), required=True)
    ap.add_argument("--only", default=None, help="只处理这些镜号，如 3,4,5")
    ap.add_argument("--attempts", type=int, default=None,
                    help="覆盖该镜的计划抽卡次数（默认取 shot.draws_planned）")
    ap.add_argument("--pick", default=None,
                    help="final 模式指定样片，格式 镜号:抽号，如 3:2（默认取第一条成功的样片）")
    ap.add_argument("--resolution", default=None, help="覆盖分辨率（默认 draft=480p、其余=1080p）")
    ap.add_argument("--max-cost", type=float, default=None,
                    help="预估成本超过该金额（元）则中止")
    ap.add_argument("--poll-timeout", type=float, default=600)
    ap.add_argument("--poll-interval", type=float, default=8)
    ap.add_argument("--force", action="store_true", help="已有的也重出")
    A.add_ark_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 8 阶段 · 视频生成（%s）" % args.mode)
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    want_strategy = "draft" if args.mode in ("draft", "final") else "direct"
    shots = [dict(r) for r in con.execute(
        "SELECT id, seq, duration_s, prompt, keyframe_path, continues_from, strategy,"
        " draws_planned FROM shot WHERE episode_id=? ORDER BY seq", (ep["id"],))]
    if not shots:
        rep.add("已有分镜", False, "先跑第 5 阶段")
        con.close()
        return rep.finish()
    if args.only:
        want = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        shots = [s for s in shots if s["seq"] in want]

    sel = [s for s in shots if s["strategy"] == want_strategy]
    rep.add("按策略筛出镜头（strategy=%s）" % want_strategy, bool(sel),
            "%d / %d 个" % (len(sel), len(shots)) if sel else
            "没有该策略的镜头。draft 模式处理 strategy=draft，direct 模式处理 strategy=direct")

    # final 模式必须有样片
    if args.mode == "final":
        with_draft = []
        for s in sel:
            if con.execute("SELECT 1 FROM generation WHERE shot_id=? AND is_draft=1 "
                           "AND status='succeeded' LIMIT 1", (s["id"],)).fetchone():
                with_draft.append(s)
        sel = with_draft
        rep.add("有可用样片的镜头", bool(sel), "%d 个" % len(sel))
    if not sel:
        con.close()
        return rep.finish()

    no_prompt = [s["seq"] for s in sel if not (s["prompt"] or "").strip()]
    rep.add("全部镜头都有定稿提示词", not no_prompt,
            "全部有" if not no_prompt else "缺：%s（先跑第 6 阶段）" % no_prompt)
    if no_prompt:
        con.close()
        return rep.finish()

    try:
        cfg = A.require_key()
    except A.ArkError as e:
        rep.add("Ark 已配置", False, str(e).splitlines()[0])
        print("\n" + str(e))
        con.close()
        return rep.finish()
    cfg["base_url"] = args.base_url or cfg["base_url"]
    cfg["video_model"] = args.video_model or cfg["video_model"]

    res_default = "480p" if args.mode == "draft" else "1080p"
    resolution = args.resolution or res_default
    variant = VARIANT_DRAFT if args.mode == "draft" else VARIANT_FINAL
    pps = price_of(con, cfg["video_model"], variant)

    # ---- 计划与预算 ----
    # --pick 支持多镜：3:2,5:1
    pick_map = {}
    if args.pick:
        for part in args.pick.split(","):
            k, _, v = part.strip().partition(":")
            if k.isdigit() and v.isdigit():
                pick_map[int(k)] = int(v)

    plan = []          # (shot, attempt_no, input_image, input_note, is_draft, 基准样片)
    for s in sel:
        img, note = resolve_input(con, s)
        # 抽号必须接着该镜已有的最大值往后排，否则会撞 generation 的
        # UNIQUE(shot_id, attempt_no)。final 模式尤其容易踩——它前面已经有样片了。
        done = con.execute("SELECT COALESCE(MAX(attempt_no),0) AS n FROM generation "
                           "WHERE shot_id=?", (s["id"],)).fetchone()["n"]
        if args.mode == "final":
            src = pick_draft(con, s["id"],
                             (s["seq"], pick_map[s["seq"]]) if s["seq"] in pick_map else None)
            if not src:
                rep.info("S%02d 跳过：没有可用样片" % s["seq"])
                continue
            plan.append((s, done + 1, img,
                         "%s；基准样片 a%02d" % (note, src["attempt_no"]), 0, src))
        else:
            if done and not args.force:
                rep.info("S%02d 跳过：已有 %d 条生成记录（--force 可重出）" % (s["seq"], done))
                continue
            n = args.attempts if args.attempts is not None else s["draws_planned"]
            for a in range(1, int(n) + 1):
                plan.append((s, done + a, img, note,
                             1 if args.mode == "draft" else 0, None))

    if not plan:
        # ⚠️ 「没事可做」**不是失败**。原来这里记成 False，于是"这一档已经全出完了"
        # 会被当成错误退出——而它其实是正常状态（幂等重跑时就该这样）。
        # 按规则强度三分法，这既不是 block 也不是 warn，只是陈述。
        rep.add("有待生成的条目", True, "没有需要处理的镜头（这一档已出完）")
        con.close()
        return rep.finish()

    total_s = sum(s["duration_s"] for s, _a, _i, _n, _d, _src in plan)
    rep.add("计划生成条数", True, "%d 条，合计 %.0f 秒" % (len(plan), total_s))
    no_img = sum(1 for _s, _a, i, _n, _d, _src in plan if not i)
    if no_img:
        rep.info("其中 %d 条没有输入图（纯文生视频），一致性会明显更差" % no_img)

    if pps is None:
        rep.info("price_book 无 %s / %s 的单价，成本将记 0，请补价目"
                 % (cfg["video_model"], variant))
        estimate = None
    else:
        estimate = total_s * pps
        rep.add("预估成本", True, "约 ¥%.0f（%.0f 秒 × ¥%.2f/秒，%s）"
                % (estimate, total_s, pps, variant))

    if args.max_cost is not None:
        if estimate is None:
            rep.add("[%s] 预算检查" % C.BLOCK, False, "无法估价（price_book 缺价目），拒绝在未知花费下继续")
            con.close()
            return rep.finish()
        if estimate > args.max_cost:
            rep.add("[%s] 预算检查" % C.BLOCK, False, "预估 ¥%.0f 超过 --max-cost ¥%.0f，已中止"
                    % (estimate, args.max_cost))
            con.close()
            return rep.finish()
        rep.add("预算检查通过", True, "¥%.0f ≤ ¥%.0f" % (estimate, args.max_cost))

    if args.dry_run:
        for s, a, img, note, is_d, src in plan[:8]:
            rep.info("S%02d a%s  %ss  %s  %s  %s"
                     % (s["seq"], a or "-", s["duration_s"],
                        "样片" if is_d else "成片", resolution, note))
        if len(plan) > 8:
            rep.info("…… 其余 %d 条略" % (len(plan) - 8))
        rep.info("dry-run：未提交任何任务")
        con.close()
        return rep.finish()

    # ---- 实际提交 ----
    print()
    ok_all, spent = True, 0.0
    for s, attempt, img, note, is_draft, src in plan:
        tag = "S%02d %s" % (s["seq"], "样片 a%s" % attempt if is_draft else "成片")
        try:
            created = A.create_video_task(
                s["prompt"], model=cfg["video_model"], duration=s["duration_s"],
                resolution=resolution, draft=bool(is_draft), reference_image=img,
                sample_task_id=(src or {}).get("task_id") if src else None,
                base_url=cfg["base_url"], api_key=cfg["api_key"])
        except A.ArkError as ex:
            con.execute(
                "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
                " duration_s, is_draft, status, error, cost) "
                "VALUES (?,?,?,?,?,?,'failed',?,0)",
                (s["id"], attempt or 1, cfg["video_model"], resolution,
                 s["duration_s"], 1 if is_draft else 0, str(ex)[:500]))
            con.commit()
            rep.add(tag, False, str(ex).splitlines()[0][:60])
            for line in str(ex).splitlines():
                if line.strip():
                    rep.info(line.strip())
            ok_all = False
            continue

        gid = con.execute(
            "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
            " duration_s, is_draft, ratio, task_id, status, cost) "
            "VALUES (?,?,?,?,?,?,'9:16',?,'running',0)",
            (s["id"], attempt or 1, created["model"], resolution, s["duration_s"],
             1 if is_draft else 0, created["task_id"])).lastrowid
        con.commit()

        try:
            final = A.wait_video_task(created["task_id"], timeout=args.poll_timeout,
                                      interval=args.poll_interval,
                                      base_url=cfg["base_url"], api_key=cfg["api_key"])
        except A.ArkError as ex:
            con.execute("UPDATE generation SET status='failed', error=?, "
                        "finished_at=datetime('now','localtime') WHERE id=?",
                        (str(ex)[:500], gid))
            con.commit()
            rep.add(tag, False, str(ex).splitlines()[0][:60])
            ok_all = False
            continue

        status = str(final.get("status") or "").lower()
        url = A.video_url_of(final)
        cost = (s["duration_s"] * pps) if pps is not None else 0.0
        if status not in ("succeeded", "success") or not url:
            con.execute("UPDATE generation SET status='failed', error=?, cost=0, "
                        "finished_at=datetime('now','localtime') WHERE id=?",
                        ("status=%s 但拿不到 video_url" % status, gid))
            con.commit()
            rep.add(tag, False, "status=%s，无视频地址（原始响应已落盘）" % status)
            ok_all = False
            continue

        rel = video_path(ep, s["seq"], attempt or 1)
        try:
            nbytes = A.download(url, C.abs_path(rel))
        except Exception as ex:
            nbytes = 0
            ok_all = False
            rep.add(tag + " 下载失败", False, str(ex)[:60])

        con.execute("UPDATE generation SET status='succeeded', video_url=?, local_path=?,"
                    " cost=?, finished_at=datetime('now','localtime') WHERE id=?",
                    (url, rel, cost, gid))
        con.execute("INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                    " unit_price, amount, episode_id, shot_id, note) "
                    "VALUES ('video',?,?, 'second',?,?,?,?,?)",
                    (cfg["video_model"], s["duration_s"], pps or 0, cost,
                     ep["id"], s["id"],
                     "%s S%03d a%s" % ("样片" if is_draft else "成片", s["seq"],
                                        attempt or 1)))
        # 没有指定选中项时，把第一条成功的设为默认，避免拼片时无从下手
        cur_sel = con.execute("SELECT selected_generation_id FROM shot WHERE id=?",
                              (s["id"],)).fetchone()["selected_generation_id"]
        if not cur_sel and not is_draft:
            con.execute("UPDATE shot SET selected_generation_id=? WHERE id=?", (gid, s["id"]))
        con.commit()

        spent += cost
        rep.add(tag, True, "%.2f MB  %.0f 秒  ¥%.2f  用时 %ss"
                % (nbytes / 1048576.0, s["duration_s"], cost, final.get("_elapsed", "?")))

    rep.add("本轮实际花费", True, "¥%.2f" % spent)
    rep.info("下一步：" + ("人工挑样片，再用 --mode final 出成片"
                          if args.mode == "draft" else
                          "跑第 9 阶段抽帧与衔接"))
    con.close()
    return rep.finish() if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
