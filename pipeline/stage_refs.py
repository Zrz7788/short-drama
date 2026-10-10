# -*- coding: utf-8 -*-
"""第 3 / 4 阶段：生成参考图（定妆照 与 三视图）。

两个阶段共用一个脚本，用 --phase 区分——它们的流程完全相同，只是
「给谁出图」和「出哪些视角」不同：

  第 3 阶段  --phase portrait    定妆照。给 main + secondary 出，视角 portrait
  第 4 阶段  --phase turnaround  三视图。**只给 main 出**，视角 front/side/back

分级的成本含义：main 出 4 张、secondary 出 1 张、background 出 0 张。

**三视图以定妆照为参考图生成（图生图）**，这是刻意的：纯文生图无法保证
三个角度是同一个实体。定妆照是锚点，三视图从锚点长出来。

用法：
  python pipeline/stage_refs.py --episode-id 1 --phase portrait
  python pipeline/stage_refs.py --episode-id 1 --phase turnaround
  python pipeline/stage_refs.py --episode-id 1 --phase portrait --dry-run
  python pipeline/stage_refs.py --episode-id 1 --phase portrait --only 林薇 --force
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

PORTRAIT_TPL = os.path.join(C.ROOT, "pipeline", "prompts", "portrait.md")
TURNAROUND_TPL = os.path.join(C.ROOT, "pipeline", "prompts", "turnaround.md")

TURNAROUND_VIEWS = [
    ("front", "正面", "正对镜头的正面视角，左右对称，双眼平视前方。"),
    ("side", "正侧面", "严格 90 度侧视角，鼻尖朝向画面一侧，只见单侧脸。"),
    ("back", "正背面", "背对镜头的正背面视角，只见后脑与背部。"),
]

# 各阶段给哪些 tier 出图（与 knowledge/01-制作方法 的分级一致）
PHASE_TIERS = {"portrait": ("main", "secondary"), "turnaround": ("main",)}


def entity_block(e):
    body = (e["description"] or "").strip()
    if e["style_note"] and e["style_note"].strip():
        body += "（%s）" % e["style_note"].strip()
    return "【%s·%s】%s" % (e["kind"], e["name"], body)


def build_portrait_prompt(e):
    return L.load_prompt(PORTRAIT_TPL, entity_block=entity_block(e))


def build_turnaround_prompt(e, view_name, view_desc):
    return L.load_prompt(TURNAROUND_TPL, entity_block=entity_block(e),
                         view_name=view_name, view_desc=view_desc)


def image_unit_price(con, model_id):
    r = con.execute("SELECT unit_price FROM price_book WHERE model_id=? AND unit=?",
                    (model_id, "image")).fetchone()
    return r["unit_price"] if r else None


def out_path(ep, e, view):
    """参考图落盘路径。走 assets_root()，便于自检用 DSH_ASSETS_ROOT 隔离。"""
    safe = "".join(ch for ch in e["name"] if ch not in '\\/:*?"<>|').strip() or "entity"
    p = os.path.join(C.assets_root(), "refs", e["kind"], "%s_%s.png" % (safe, view))
    return os.path.relpath(p, C.ROOT).replace("\\", "/")


def do_one(con, cfg, ep, e, view, prompt, reference, size, dry, rep, tag):
    rel = out_path(ep, e, view)
    if dry:
        rep.info("计划：%s  %s  ->  %s%s"
                 % (tag, e["name"], rel, "（参考：%s）" % reference if reference else ""))
        return True

    try:
        res = A.generate_image(prompt, size=size, reference=reference,
                               model=cfg["image_model"], base_url=cfg["base_url"],
                               api_key=cfg["api_key"])
    except A.ArkError as ex:
        gid = con.execute(
            "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
            " ref_image_path, status, error, cost, finished_at) "
            "VALUES (?,?,?,?,?,?,'failed',?,0,datetime('now','localtime'))",
            (e["id"], view, cfg["image_model"], prompt, size, reference, str(ex)[:500])
        ).lastrowid
        con.commit()
        msg = str(ex)
        rep.add("%s %s" % (tag, e["name"]), False, msg.splitlines()[0][:64])
        # 完整错误必须全部可见：可操作的下一步和落盘的原始响应路径都在里面，
        # 而报告行会截断到 64 字。实测踩过两次——先是第二行被吞，再是第一行被截。
        for line in msg.splitlines():
            if line.strip():
                rep.info(line.strip())
        return False

    url = res["urls"][0]
    price = image_unit_price(con, res["model"])
    cost = price if price is not None else 0.0
    try:
        nbytes = A.download(url, C.abs_path(rel))
    except Exception as ex:
        nbytes, rel = 0, None
        rep.add("%s %s 下载失败" % (tag, e["name"]), False, str(ex)[:60])

    gid = con.execute(
        "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
        " ref_image_path, status, image_url, local_path, cost, finished_at) "
        "VALUES (?,?,?,?,?,?,'succeeded',?,?,?,datetime('now','localtime'))",
        (e["id"], view, res["model"], prompt, size, reference, url, rel, cost)
    ).lastrowid
    con.execute(
        "INSERT INTO entity_ref (entity_id, view, path, image_gen_id, is_primary, approved)"
        " VALUES (?,?,?,?,?,0) "
        "ON CONFLICT(entity_id, view) DO UPDATE SET path=excluded.path, "
        "image_gen_id=excluded.image_gen_id",
        (e["id"], view, rel, gid, 1 if view == "portrait" else 0))
    con.execute(
        "INSERT INTO cost_ledger (category, model_id, quantity, unit, unit_price,"
        " amount, note) VALUES ('image',?,1,'image',?,?,?)",
        (res["model"], cost, cost, "%s %s" % (e["name"], view)))
    con.commit()

    detail = "%.0f KB" % (nbytes / 1024.0) if nbytes else rel
    if price is None:
        detail += "  ⚠ price_book 里没有该模型单价，本次成本记 0"
    rep.add("%s %s" % (tag, e["name"]), True, detail)
    return True


def main():
    ap = argparse.ArgumentParser(description="第 3/4 阶段：生成参考图")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--phase", choices=("portrait", "turnaround"), required=True)
    ap.add_argument("--only", default=None, help="只处理某个实体名")
    ap.add_argument("--include-background", action="store_true",
                    help="连 background 级实体也出图（默认不出）")
    ap.add_argument("--force", action="store_true", help="已存在的视角也重新生成")
    ap.add_argument("--size", default="2K", help="1K/2K/4K 或 '宽x高'（默认 2K）")
    ap.add_argument("--allow-unknown-price", action="store_true",
                    help="价目缺失时也继续（账本会失真，仅用于跑通流程）")
    A.add_ark_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    label = "第 3 阶段 · 定妆照" if args.phase == "portrait" else "第 4 阶段 · 三视图"
    rep = C.Report(label)
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    tiers = list(PHASE_TIERS[args.phase])
    if args.include_background:
        tiers.append("background")
    q = ("SELECT * FROM entity WHERE project_id=? AND role IN (%s)"
         % ",".join("?" * len(tiers)))
    ents = [dict(r) for r in con.execute(q, [ep["project_id"]] + tiers)]
    if args.only:
        ents = [e for e in ents if e["name"] == args.only]
    if not ents:
        rep.add("有可处理的实体", False,
                "本项目 role ∈ %s 的实体为空，先跑第 2 阶段" % tiers)
        con.close()
        return rep.finish()

    try:
        cfg = A.require_key()
    except A.ArkError as e:
        rep.add("Ark 已配置", False, str(e).splitlines()[0])
        print("\n" + str(e))
        con.close()
        return rep.finish()
    # 命令行可覆盖端点与模型
    cfg["base_url"] = args.base_url or cfg["base_url"]
    cfg["image_model"] = args.image_model or cfg["image_model"]

    views = [("portrait",)] if args.phase == "portrait" else TURNAROUND_VIEWS
    rep.info("模型：%s　size：%s　tier：%s　实体数：%d"
             % (cfg["image_model"], args.size, "/".join(tiers), len(ents)))

    # 已有参考图检查
    have = {}
    for r in con.execute("SELECT entity_id, view, path FROM entity_ref"):
        have[(r["entity_id"], r["view"])] = r["path"]

    price = image_unit_price(con, cfg["image_model"])
    planned = 0
    for e in ents:
        need = []
        if args.phase == "portrait":
            if args.force or not have.get((e["id"], "portrait")):
                need.append("portrait")
        else:
            if not have.get((e["id"], "portrait")):
                rep.info("跳过 %s：没有定妆照，三视图缺锚点，先跑 --phase portrait"
                         % e["name"])
                continue
            for v, _n, _d in TURNAROUND_VIEWS:
                if args.force or not have.get((e["id"], v)):
                    need.append(v)
        planned += len(need)

    rep.add("待生成张数", True, "%d 张%s" % (planned, "（dry-run）" if args.dry_run else ""))
    if price is not None:
        rep.add("预估成本", True, "约 ¥%.2f（¥%.2f/张）" % (planned * price, price))
    elif not args.allow_unknown_price:
        # **拒绝在未知花费下继续。**
        # 原来这里只打一句"成本将记 0"就接着跑。后果是：跑 80 集、生成几千张图，
        # cost_ledger 上写着 ¥0，你完全不知道花了多少。
        # 这与 cost-governor 里给视频定的规矩应当一致——当时只对图片手软了。
        rep.add("价目缺失时拒绝继续", False,
                "price_book 里没有 %s 的图片单价" % cfg["image_model"])
        print()
        print("拒绝在未知花费下继续——按 0 记账会让账本彻底失真。")
        print("补价目（去火山方舟控制台的模型计费页查单价）：")
        print("  INSERT INTO price_book (model_id, variant, unit, unit_price,"
              " confidence, source)")
        print("    VALUES ('%s', '图片', 'image', <单价>, 'high', '控制台');"
              % cfg["image_model"])
        print("确实要在未知花费下继续（比如只想跑通流程）：加 --allow-unknown-price")
        con.close()
        return rep.finish()
    else:
        rep.info("已按 --allow-unknown-price 继续，本次成本记 0（账本会失真）")

    if not planned:
        rep.info("没有需要生成的视角（都已存在）。要重出请加 --force")
        con.close()
        return rep.finish()

    ok = True
    for e in ents:
        if args.phase == "portrait":
            if args.force or not have.get((e["id"], "portrait")):
                ok &= do_one(con, cfg, ep, e, "portrait",
                             build_portrait_prompt(e), None, args.size,
                             args.dry_run, rep, "定妆照")
        else:
            if not have.get((e["id"], "portrait")):
                continue
            ref = have[(e["id"], "portrait")]
            if not os.path.exists(C.abs_path(ref)):
                rep.add("三视图 %s" % e["name"], False,
                        "定妆照文件不存在：%s" % ref)
                ok = False
                continue
            for v, vn, vd in TURNAROUND_VIEWS:
                if args.force or not have.get((e["id"], v)):
                    ok &= do_one(con, cfg, ep, e, v,
                                 build_turnaround_prompt(e, vn, vd),
                                 ref, args.size, args.dry_run, rep, "三视图·" + vn)

    if not args.dry_run:
        n = con.execute(
            "SELECT COUNT(*) FROM entity_ref r JOIN entity e ON e.id=r.entity_id "
            "WHERE e.project_id=?", (ep["project_id"],)).fetchone()[0]
        rep.add("entity_ref 累计参考图数", True, "%d 张" % n)
        rep.info("下一步：人工审图，选定后用 entity_ref.approved=1 冻结")
    con.close()
    return rep.finish() if ok else 1


if __name__ == "__main__":
    sys.exit(main())
