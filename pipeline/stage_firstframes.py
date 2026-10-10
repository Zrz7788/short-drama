# -*- coding: utf-8 -*-
"""第 7 阶段：生成首帧图（关键帧）。

**这是成片前最后一个便宜的关卡。** 首帧图约 ¥0.2/张，视频约 ¥4–5.8/条，差着
20–30 倍。所以正确的打法是在首帧图上多抽几次选最好的一张，再去生视频——
而不是把预算烧在视频抽卡上。

每一镜的提示词直接取第 6 阶段定稿的 `shot.prompt`（实体描述已逐字内联），
再把该镜关联实体的参考图作为图生图输入传进去，保证画面里的角色就是定妆照那个。

产出写入 `shot.keyframe_path`。刻意与 `first_frame_path` 分开：后者的语义是
"这一镜实际用的首帧"，会被第 9 阶段的续接逻辑覆盖（续接时等于上一镜的尾帧）。

用法：
  python pipeline/stage_firstframes.py --episode-id 1
  python pipeline/stage_firstframes.py --episode-id 1 --only 3,4,5
  python pipeline/stage_firstframes.py --episode-id 1 --take 2      # 每镜抽 2 张挑
  python pipeline/stage_firstframes.py --episode-id 1 --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402

MAX_REFS = 5          # 参考图上限。官方推荐 4–5 个素材（能力上限是 30，但收多了干扰大）
                      # 主体两张（面部特征 + 妆造）+ 布景 + 道具，正好 4–5


def keyframe_path(ep, shot_seq, take=1):
    """关键帧落盘路径。走 assets_root()，便于自检隔离。"""
    p = os.path.join(C.assets_root(), "keyframes",
                     "E%02dS%03d%s.png" % (ep["seq"], shot_seq,
                                           "" if take == 1 else "_t%d" % take))
    return os.path.relpath(p, C.ROOT).replace("\\", "/")


def shot_refs(con, shot_id):
    """取该镜要传给模型的参考图（**按《拾音人》规范的素材纪律**）。

    规则：

    1. **面部大特写镜只给大头照一张。** `shot.solo_generation=1` 的镜头
       （面部大特写、最大特效镜）**素材越少越稳**——这是规范里反复强调的。
    2. **一般镜**：主体给两张——`headshot`（面部特征）+ `fullbody`（妆造），
       提示词里分工引用（「面部特征参考图片1，妆造参考图片2」）。
       只出过一张的（secondary 角色只有 fullbody）就只给一张。
    3. 道具与布景各一张。
    4. **总数不超过 5 张**（`max_refs`）。官方推荐 4–5 个素材，
       能力上限是 30，但**收多了干扰大、出错率高**——超了按优先级截断，
       并把截断情况报出来。
    """
    order = {"subject": 0, "prop": 1, "background": 2}
    solo = con.execute("SELECT COALESCE(solo_generation,0) FROM shot WHERE id=?",
                       (shot_id,)).fetchone()
    solo = bool(solo and solo[0])

    rows = con.execute(
        "SELECT se.role, e.name, e.kind, r.path, r.view, r.is_primary "
        "FROM shot_entity se JOIN entity e ON e.id = se.entity_id "
        "LEFT JOIN entity_ref r ON r.entity_id = e.id "
        "WHERE se.shot_id = ?", (shot_id,)).fetchall()

    # 每个实体按需要的视角挑图
    by_entity = {}
    for r in rows:
        if not r["path"]:
            continue
        by_entity.setdefault((r["role"], r["name"], r["kind"]), {})[r["view"]] = r["path"]

    picked, seen = [], set()

    def take(p):
        if p and p not in seen and os.path.exists(C.abs_path(p)):
            picked.append(p)
            seen.add(p)
            return True
        return False

    for (role, _name, kind), vs in sorted(
            by_entity.items(), key=lambda kv: order.get(kv[0][0], 9)):
        if kind == "character":
            if solo:
                # 面部大特写：只给大头照；没有大头照才退回主参考图
                take(vs.get("headshot") or vs.get("fullbody"))
            else:
                # 面部特征 + 妆造，分工引用
                take(vs.get("headshot"))
                take(vs.get("fullbody"))
        else:
            # 道具 / 布景：一张即可（优先正视图）
            take(vs.get("prop_ref") or vs.get("scene_front")
                 or vs.get("blocking") or next(iter(vs.values()), None))
        if len(picked) >= MAX_REFS:
            break
    return picked[:MAX_REFS]


def image_unit_price(con, model_id):
    r = con.execute("SELECT unit_price FROM price_book WHERE model_id=? AND unit=?",
                    (model_id, "image")).fetchone()
    return r["unit_price"] if r else None


def main():
    ap = argparse.ArgumentParser(description="第 7 阶段：生成首帧图")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--only", default=None, help="只处理这些镜号，如 3,4,5")
    ap.add_argument("--take", type=int, default=1, help="每镜抽几张（默认 1）")
    ap.add_argument("--force", action="store_true", help="已有首帧也重出")
    ap.add_argument("--size", default="2K")
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
    rep = C.Report("第 7 阶段 · 生成首帧图")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    shots = [dict(r) for r in con.execute(
        "SELECT id, seq, duration_s, prompt, keyframe_path, strategy, draws_planned "
        "FROM shot WHERE episode_id=? ORDER BY seq", (ep["id"],))]
    if not shots:
        rep.add("已有分镜", False, "先跑第 5 阶段")
        con.close()
        return rep.finish()
    if args.only:
        want = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        shots = [s for s in shots if s["seq"] in want]

    no_prompt = [s["seq"] for s in shots if not (s["prompt"] or "").strip()]
    rep.add("全部镜头都有定稿提示词", not no_prompt,
            "全部有" if not no_prompt else "缺：%s（先跑第 6 阶段）" % no_prompt)
    if no_prompt:
        con.close()
        return rep.finish()

    todo = [s for s in shots if args.force or not s["keyframe_path"]]
    rep.add("待生成镜头数", True, "%d / %d" % (len(todo), len(shots)))

    try:
        cfg = A.require_key()
    except A.ArkError as e:
        rep.add("Ark 已配置", False, str(e).splitlines()[0])
        print("\n" + str(e))
        con.close()
        return rep.finish()
    cfg["base_url"] = args.base_url or cfg["base_url"]
    cfg["image_model"] = args.image_model or cfg["image_model"]

    price = image_unit_price(con, cfg["image_model"])
    planned = len(todo) * args.take
    rep.info("模型：%s　size：%s　每镜抽 %d 张" % (cfg["image_model"], args.size, args.take))
    if price is not None:
        rep.add("预估成本", True, "约 ¥%.2f（%d 张 × ¥%.2f）"
                % (planned * price, planned, price))
    elif not args.allow_unknown_price:
        # 与 stage_refs 同一规矩：**拒绝在未知花费下继续**。
        # 按 0 记账会让账本失真——而首帧图是全片抽卡最密集的一步，
        # 这里失真最要命。
        rep.add("价目缺失时拒绝继续", False,
                "price_book 里没有 %s 的图片单价" % cfg["image_model"])
        print()
        print("拒绝在未知花费下继续。补价目：")
        print("  INSERT INTO price_book (model_id, variant, unit, unit_price,"
              " confidence, source)")
        print("    VALUES ('%s', '图片', 'image', <单价>, 'high', '控制台');"
              % cfg["image_model"])
        print("确实要继续（只想跑通流程）：加 --allow-unknown-price")
        con.close()
        return rep.finish()
    else:
        rep.info("已按 --allow-unknown-price 继续，本次成本记 0（账本会失真）")

    if not todo:
        rep.info("没有需要生成的镜头（都已有首帧）。要重出请加 --force")
        con.close()
        return rep.finish()

    ok_all = True
    for s in todo:
        refs = shot_refs(con, s["id"])
        rep.info("S%02d 参考图 %d 张%s"
                 % (s["seq"], len(refs), "：" + ", ".join(os.path.basename(r) for r in refs)
                    if refs else "（无，将走纯文生图）"))
        for take in range(1, args.take + 1):
            rel = keyframe_path(ep, s["seq"], take)
            if args.dry_run:
                rep.info("  计划 -> %s" % rel)
                continue
            try:
                res = A.generate_image(s["prompt"], size=args.size, references=refs,
                                       model=cfg["image_model"], base_url=cfg["base_url"],
                                       api_key=cfg["api_key"])
            except A.ArkError as ex:
                con.execute(
                    "INSERT INTO image_generation (entity_id, view, model_id, prompt,"
                    " size, ref_image_path, status, error, cost, finished_at) "
                    "VALUES (NULL,?,?,?,?,?,'failed',?,0,datetime('now','localtime'))",
                    ("shot:S%03d#%d" % (s["seq"], take), cfg["image_model"],
                     s["prompt"], args.size, ",".join(refs) or None, str(ex)[:500]))
                con.commit()
                rep.add("S%02d 抽 %d" % (s["seq"], take), False,
                        str(ex).splitlines()[0][:60])
                for line in str(ex).splitlines():
                    if line.strip():
                        rep.info(line.strip())
                ok_all = False
                continue

            cost = price if price is not None else 0.0
            try:
                nbytes = A.download(res["urls"][0], C.abs_path(rel))
            except Exception as ex:
                nbytes = 0
                rep.add("S%02d 下载失败" % s["seq"], False, str(ex)[:60])
                ok_all = False

            gid = con.execute(
                "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
                " ref_image_path, status, image_url, local_path, cost, finished_at) "
                "VALUES (NULL,?,?,?,?,?,'succeeded',?,?,?,datetime('now','localtime'))",
                ("shot:S%03d#%d" % (s["seq"], take), res["model"], s["prompt"],
                 args.size, ",".join(refs) or None, res["urls"][0], rel, cost)).lastrowid
            con.execute(
                "INSERT INTO cost_ledger (category, model_id, quantity, unit, unit_price,"
                " amount, episode_id, shot_id, note) VALUES ('image',?,1,'image',?,?,?,?,?)",
                (res["model"], cost, cost, ep["id"], s["id"],
                 "首帧 S%03d#%d" % (s["seq"], take)))
            # 第一张作为该镜的默认关键帧
            if take == 1:
                con.execute("UPDATE shot SET keyframe_path=? WHERE id=?", (rel, s["id"]))
            con.commit()
            rep.add("S%02d 抽 %d" % (s["seq"], take), True,
                    "%.0f KB  参考图 %d 张" % (nbytes / 1024.0, res["n_references"]))

    if not args.dry_run:
        n = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND keyframe_path "
                        "IS NOT NULL", (ep["id"],)).fetchone()[0]
        rep.add("已有首帧的镜头数", True, "%d / %d" % (n, len(shots)))
        if args.take > 1:
            rep.info("抽了多张的镜头：选定后手工把要用的那张路径写回 "
                     "shot.keyframe_path（候选在 assets/keyframes/ 下）")
        rep.info("下一步：人工过一遍首帧图——这是最后一个便宜的关卡，"
                 "首帧不对就别去烧视频了")
    con.close()
    return rep.finish() if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
