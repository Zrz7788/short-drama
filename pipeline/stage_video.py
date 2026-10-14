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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402

# price_book 里的档位名
VARIANT_DRAFT = "Draft 样片"
VARIANT_FINAL = "旗舰"

# 按 strategy 映射视频模型（2026-10-14 与用户定的混合策略）：
#   strategy='direct'（空镜/背影/手部特写等简单镜）→ Seedance 2.0 标准（¥1.00/秒），
#     它完全够用，省 31%；
#   strategy='draft'（正脸/特写/对手戏等难镜）→ Seedance 2.5 旗舰（¥1.45/秒），
#     脸崩一镜重抽一次就吃掉全部差价。
# 依据《单集成本模型.md》：「视频抽卡占 91%，一次多余抽卡（4 秒 ≈ ¥5.8）就能吃掉
# 换成便宜模型省下的全部差价」——难点用贵模型是为了一次过，不是浪费。
# 注意：Draft 样片档只有 2.5 有价（2.0 无 Draft 价），所以 draft 模式全走 2.5。
# direct 模式（direct 镜直出成片）也映射 2.0——它同样是"简单镜"。
MODEL_BY_STRATEGY = {
    ("draft", "draft"): "doubao-seedance-2-5-260628",     # 样片：统一 2.5（只有它有 Draft 价）
    ("final", "draft"): "doubao-seedance-2-5-260628",     # 难镜成片：2.5
    ("direct", "direct"): "doubao-seedance-2-0-260128",   # 简单镜直出：2.0 省 31%
    ("final", "direct"): "doubao-seedance-2-0-260128",    # 简单镜成片：2.0 省 31%
}

# 生成画幅 16:9（2026-10-14 用户拍板）：横屏生成、后期统一裁 9:16。
# 依据《拾音人范本与工艺规范》§一.3——竖屏直出爱烧字幕（首帧 14 张 3 张中招实测），
# 且横屏构图容错高、多平台分发一份素材两用。剪辑阶段用 ffmpeg crop 裁 9:16。
# 规格门仍按项目 aspect_ratio 校验——所以生成时 ratio 用 16:9，但 spec_gate
# 的 target_aspect 传生成画幅，裁剪在拼片阶段做。


def video_path(ep, shot_seq, attempt):
    p = os.path.join(C.assets_root(), "videos",
                     "E%02dS%03d_a%02d.mp4" % (ep["seq"], shot_seq, attempt))
    return C.asset_rel(p)


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


def do_select(con, ep, spec):
    """`--select 2:5,3:4`（镜号:抽号）—— 把"正片用哪条"显式改掉。

    ⚠️ 为什么必须有这个开关：`--mode final` 出了新成片之后**不会自动改选中项**
    （原逻辑是"没有选中项时才设默认"），于是会出现
    **"重出了、钱花了、正片里还是旧那条"**——看着像做了，其实没做（实测踩到）。
    选定是**人的决定**，所以留成显式的一步，但要能一键做。
    """
    ok = True
    for item in spec.replace("，", ",").split(","):
        if not item.strip():
            continue
        seq_s, _, att_s = item.strip().partition(":")
        if not (seq_s.strip().isdigit() and att_s.strip().isdigit()):
            print("看不懂的 --select 项：%s（要写 镜号:抽号，如 2:5）" % item)
            ok = False
            continue
        seq, att = int(seq_s), int(att_s)
        g = con.execute(
            "SELECT g.id, g.local_path, g.is_draft FROM generation g "
            "JOIN shot s ON s.id=g.shot_id "
            "WHERE s.episode_id=? AND s.seq=? AND g.attempt_no=? "
            "AND g.status='succeeded'", (ep["id"], seq, att)).fetchone()
        if not g:
            print("S%02d a%s：找不到成功的生成记录" % (seq, att))
            ok = False
            continue
        if g["is_draft"]:
            print("S%02d a%s：这是**样片**（480P），不能当交付选中项" % (seq, att))
            ok = False
            continue
        con.execute("UPDATE shot SET selected_generation_id=? "
                    "WHERE episode_id=? AND seq=?", (g["id"], ep["id"], seq))
        print("S%02d 交付选中项 → a%s（%s）"
              % (seq, att, os.path.basename(g["local_path"] or "")))
    con.commit()
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description="第 8 阶段：视频生成")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--mode", choices=("draft", "direct", "final"), default=None,
                    help="draft=出样片 / direct=简单镜直出成片 / final=按样片出成片；"
                         "只在用 --select 改选中项时可以省略")
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
    ap.add_argument("--select", default=None,
                    help="改交付选中项（镜号:抽号，可多个，如 2:5,3:4）；"
                         "不调 API、不花钱。**重出成片后必须显式选定，"
                         "否则正片里还是旧那条**")
    A.add_ark_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    if not args.mode and not args.select:
        ap.error("要么给 --mode（生成），要么给 --select（改选中项）")
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

    if args.select:
        code = do_select(con, ep, args.select)
        con.close()
        return code

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

    # 生成画幅：16:9 横屏（见文件头 MODEL_BY_STRATEGY 上方的说明）。
    # 项目 aspect_ratio 仍是 9:16——那是**交付画幅**，裁剪在拼片阶段做。
    GEN_RATIO = "16:9"

    # ---- 计划与预算 ----
    # --pick 支持多镜：3:2,5:1
    pick_map = {}
    if args.pick:
        for part in args.pick.split(","):
            k, _, v = part.strip().partition(":")
            if k.isdigit() and v.isdigit():
                pick_map[int(k)] = int(v)

    plan = []          # (shot, attempt_no, input_image, input_note, is_draft, 基准样片, model_id)
    for s in sel:
        img, note = resolve_input(con, s)
        # 抽号必须接着该镜已有的最大值往后排，否则会撞 generation 的
        # UNIQUE(shot_id, attempt_no)。final 模式尤其容易踩——它前面已经有样片了。
        done = con.execute("SELECT COALESCE(MAX(attempt_no),0) AS n FROM generation "
                           "WHERE shot_id=?", (s["id"],)).fetchone()["n"]
        # 模型按 strategy 映射（MODEL_BY_STRATEGY）；--video-model 显式传参则整体覆盖
        s_model = cfg["video_model"] if args.video_model \
            else MODEL_BY_STRATEGY.get((args.mode, s["strategy"]),
                                       cfg["video_model"])
        if args.mode == "final":
            src = pick_draft(con, s["id"],
                             (s["seq"], pick_map[s["seq"]]) if s["seq"] in pick_map else None)
            if not src:
                rep.info("S%02d 跳过：没有可用样片" % s["seq"])
                continue
            plan.append((s, done + 1, img,
                         "%s；基准样片 a%02d [%s]" % (note, src["attempt_no"],
                                                      s_model.rsplit("-", 1)[0]),
                         0, src, s_model))
        else:
            if done and not args.force:
                rep.info("S%02d 跳过：已有 %d 条生成记录（--force 可重出）" % (s["seq"], done))
                continue
            n = args.attempts if args.attempts is not None else s["draws_planned"]
            for a in range(1, int(n) + 1):
                plan.append((s, done + a, img, note,
                             1 if args.mode == "draft" else 0, None, s_model))

    if not plan:
        # ⚠️ 「没事可做」**不是失败**。原来这里记成 False，于是"这一档已经全出完了"
        # 会被当成错误退出——而它其实是正常状态（幂等重跑时就该这样）。
        # 按规则强度三分法，这既不是 block 也不是 warn，只是陈述。
        rep.add("有待生成的条目", True, "没有需要处理的镜头（这一档已出完）")
        con.close()
        return rep.finish()

    total_s = sum(s["duration_s"] for s, _a, _i, _n, _d, _src, _m in plan)
    rep.add("计划生成条数", True, "%d 条，合计 %.0f 秒" % (len(plan), total_s))
    no_img = sum(1 for _s, _a, i, _n, _d, _src, _m in plan if not i)
    if no_img:
        rep.info("其中 %d 条没有输入图（纯文生视频），一致性会明显更差" % no_img)

    # 分模型估价：2.0 与 2.5 单价不同，plan 里可能混两档
    by_model = {}
    for s, _a, _i, _n, _d, _src, m in plan:
        by_model.setdefault(m, 0.0)
        by_model[m] += s["duration_s"]
    estimate, est_parts = 0.0, []
    for m, secs in sorted(by_model.items()):
        p = price_of(con, m, variant)
        if p is None:
            rep.info("price_book 无 %s / %s 的单价，该档成本记 0，请补价目" % (m, variant))
            est_parts.append("%s: ?" % m.rsplit("-", 1)[0])
            continue
        estimate += secs * p
        est_parts.append("%s: %.0fs×¥%.2f=¥%.0f" % (m.rsplit("-", 1)[0], secs, p, secs * p))
    rep.add("预估成本", True, "约 ¥%.0f（%s；%s）"
            % (estimate, " + ".join(est_parts), variant))

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

    # ---- 全局花钱闸门（不看有没有传 --max-cost）----
    #
    # 显式传的 --max-cost 只管"本次"，而且**不传就完全不拦**。
    # 这里加一道**始终生效**的闸门：上限取 DSH_EPISODE_BUDGET（默认 150 元/集），
    # 比的是 **本集累计已花 + 本次预估**。
    # 依据：2026-10-15，钱"像流水一样"，就是因为没有任何一道闸门——
    # 补跑三次，每次都"没超"。
    import budget as BG  # noqa: E402  （pipeline/ 已在 sys.path）
    ok_b, why_b = BG.check(con, ep["id"], estimate, "本次视频")
    rep.add("本集预算闸门", ok_b, why_b)
    if not ok_b:
        con.close()
        return rep.finish()

    if args.dry_run:
        for s, a, img, note, is_d, src, m in plan[:8]:
            rep.info("S%02d a%s  %ss  %s  %s  [%s]  %s"
                     % (s["seq"], a or "-", s["duration_s"],
                        "样片" if is_d else "成片", resolution,
                        m.rsplit("-", 1)[0], note))
        if len(plan) > 8:
            rep.info("…… 其余 %d 条略" % (len(plan) - 8))
        rep.info("dry-run：未提交任何任务")
        con.close()
        return rep.finish()

    # ---- 实际提交 ----
    print()
    ok_all, spent = True, 0.0
    for s, attempt, img, note, is_draft, src, s_model in plan:
        tag = "S%02d %s" % (s["seq"], "样片 a%s" % attempt if is_draft else "成片")
        m_pps = price_of(con, s_model, variant)
        try:
            created = A.create_video_task(
                s["prompt"], model=s_model, duration=s["duration_s"],
                resolution=resolution, draft=bool(is_draft), reference_image=img,
                ratio=GEN_RATIO,
                sample_task_id=(src or {}).get("task_id") if src else None,
                base_url=cfg["base_url"], api_key=cfg["api_key"])
        except A.ArkError as ex:
            con.execute(
                "INSERT INTO generation (shot_id, attempt_no, model_id, resolution,"
                " duration_s, is_draft, status, error, cost) "
                "VALUES (?,?,?,?,?,?,'failed',?,0)",
                (s["id"], attempt or 1, s_model, resolution,
                 s["duration_s"], 1 if is_draft else 0, str(ex)[:500]))
            con.commit()
            rep.add(tag, False, str(ex).splitlines()[0][:60])
            for line in str(ex).splitlines():
                if line.strip():
                    rep.info(line.strip())
            ok_all = False
            continue

        # 具名参数：28 列这种规模的 INSERT 数占位符必炸（复盘 §六），一律 :key 绑定
        gvals = {"shot_id": s["id"], "attempt_no": attempt or 1,
                 "model_id": s_model, "resolution": resolution,
                 "duration_s": s["duration_s"],
                 "is_draft": 1 if is_draft else 0,
                 "ratio": GEN_RATIO, "task_id": created["task_id"]}
        gcols = ", ".join(gvals)
        gmarks = ", ".join(":%s" % k for k in gvals)
        gid = con.execute(
            "INSERT INTO generation (%s, status, cost) VALUES (%s, 'running', 0)"
            % (gcols, gmarks), gvals).lastrowid
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
        cost = (s["duration_s"] * m_pps) if m_pps is not None else 0.0
        if status not in ("succeeded", "success") or not url:
            # ⚠️ 失败任务的计费是盲区：任务真实跑完了（轮询数分钟），Ark 对
            # "跑完但失败"是否计费无文档。按 0 记账是赌——2026-10-14 对账发现
            # 账本 ¥110.99 vs 实扣 ¥130+，差额主要在这类记录（3 笔 ≈¥14.7）。
            # 标 cost_uncertain，对账脚本按此补记，不装作它是零。
            est = s["duration_s"] * (m_pps or 0)
            con.execute("UPDATE generation SET status='failed', error=?, "
                        "cost=0, finished_at=datetime('now','localtime') WHERE id=?",
                        ("status=%s 但拿不到 video_url；若账单有对应计费，应记 ¥%.2f（cost_uncertain）"
                         % (status, est), gid))
            con.commit()
            rep.add(tag, False, "status=%s，无视频地址（原始响应已落盘）；"
                    "若账单有计费应补记 ¥%.2f" % (status, est))
            ok_all = False
            continue

        rel = video_path(ep, s["seq"], attempt or 1)
        try:
            nbytes = A.download(url, C.abs_path(rel))
        except Exception as ex:
            nbytes = 0
            ok_all = False
            rep.add(tag + " 下载失败", False, str(ex)[:60])

        # ---- U0 规格门 ----
        # 在**下载下来的文件**上量硬规格，不信请求参数。
        # 依据 Muse 流水线评审：画幅已知会跑偏（720×1280、768×1152、784×1088、
        # 1280×720、640×480 都出现过），而当时没有任何一步能拦住。
        # **不合格就直接判失败、计入本次尝试——不写 local_path、不进语义检查。**
        # 先保证它是合法产物，再谈它好不好；顺序不能反。
        # 画幅校验按**生成画幅 16:9**（GEN_RATIO）——交付的 9:16 是拼片阶段
        # 裁剪出来的，此处生成物是横屏。
        try:
            _aw, _ah = [float(x) for x in re.split(r"[:x×/]", GEN_RATIO)[:2]]
            target_aspect = _aw / _ah if _ah else None
        except Exception:
            target_aspect = None
        short_min = {"480p": 480, "720p": 720, "1080p": 1080}.get(resolution)
        gok, gwhy, gdet = C.spec_gate(
            C.abs_path(rel), target_aspect=target_aspect,
            min_short_side=short_min,
            min_seconds=float(s["duration_s"]) - 1,
            max_seconds=float(s["duration_s"]) + 1)
        if not gok:
            ok_all = False
            con.execute("UPDATE generation SET status='spec_failed', error=?,"
                        " video_url=?, local_path=NULL, ratio=?, cost=?,"
                        " finished_at=datetime('now','localtime') WHERE id=?",
                        ("规格门不过：%s" % gwhy, url, gdet.get("aspect"), cost, gid))
            con.execute("INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                        " unit_price, amount, episode_id, shot_id, note) "
                        "VALUES ('video',?,?, 'second',?,?,?,?,?)",
                        (s_model, float(s["duration_s"]), m_pps or 0.0,
                         cost, ep["id"], s["id"],
                         "S%03d 规格门不过（%s）" % (s["seq"], gwhy[:60])))
            con.commit()
            rep.add(tag + " 规格门", False, gwhy[:80])
            continue

        con.execute("UPDATE generation SET status='succeeded', video_url=?, local_path=?,"
                    " ratio=?, cost=?, finished_at=datetime('now','localtime') WHERE id=?",
                    (url, rel, gdet.get("aspect"), cost, gid))
        # ---- 记账：优先记**真实 token**，记不到才退回"秒 × 单价" ----
        #
        # ⚠️ 为什么：Seedance 在控制台是**按 token 计价**、且**按分辨率分档**的
        # （480P 不含视频输入 ¥70/百万 token、含视频输入 ¥42/百万 token）。
        # 我们原来的账一律是 `秒数 × ¥1.45`，而 ¥1.45 被标成 1080P 单价、
        # 实际连 720P 的公开价（≈¥1.51/秒）都不到 → **1080P 的账严重偏低**，
        # 直接造成"账本 ¥197 vs 充值账单 ¥400"（2026-10-15 对账）。
        # 现在把响应里的 usage token 存下来（unit='mtoken'），
        # 就算单价表还没补齐，**token 数是真实的**，补上单价即可重算历史账。
        utok = A.usage_tokens_of(final)
        if utok:
            con.execute("INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                        " unit_price, amount, episode_id, shot_id, note) "
                        "VALUES ('video',?,?, 'mtoken',?,?,?,?,?)",
                        (s_model, utok, 0.0, cost, ep["id"], s["id"],
                         "%s S%03d a%s（**真实 token %d；token 单价表待补，"
                         "此金额仍按秒估算**）"
                         % ("样片" if is_draft else "成片", s["seq"],
                            attempt or 1, utok)))
        else:
            con.execute("INSERT INTO cost_ledger (category, model_id, quantity, unit,"
                        " unit_price, amount, episode_id, shot_id, note) "
                        "VALUES ('video',?,?, 'second',?,?,?,?,?)",
                        (s_model, s["duration_s"], m_pps or 0, cost,
                         ep["id"], s["id"],
                         "%s S%03d a%s（响应无 usage，按秒估算）"
                         % ("样片" if is_draft else "成片", s["seq"], attempt or 1)))
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
    if args.mode == "final" and spent:
        # 重出成片**不会自动进正片**，这句话必须打出来，否则就是"看着做了"
        rep.info("⚠️ 新成片不会自动成为正片：确认画面后跑 "
                 "--select <镜号>:<抽号>（例如 --select 2:5）才会进拼片")
    rep.info("下一步：" + ("人工挑样片，再用 --mode final 出成片"
                          if args.mode == "draft" else
                          "跑第 9 阶段抽帧与衔接"))
    con.close()
    return rep.finish() if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
