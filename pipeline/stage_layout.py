# -*- coding: utf-8 -*-
r"""第 4.5 阶段：正交布局图 —— 在出图之前把几何定下来。

## 为什么要有这个阶段

第 1 集踩的两个坑纯文字防不住：

  1. 冷柜 / 转运推车 / 化妆台被塞进**同一间屋**——空间不成立
  2. 竖屏 9:16 **装不下横躺人物的全长**——用户一眼看出"推车太短了"

而"装不装得下"是**可以算出来的**：焦距 + 距离 + 画幅 → 可见画面尺寸。
这个阶段把算式和平面图都产出，落成 `entity_ref.view='layout'`，与既有素材
体系并列（那套用的是 scene_front / blocking / scene_top）。

## 输入产物（人/agent 写，脚本只算与画）

`剧集/EP###/布局.json`：

```json
{
  "aspect": "9:16",
  "rooms": [{
    "name": "整容室",
    "width_m": 4.0, "depth_m": 3.4,
    "items": [
      {"name": "整容台", "x": 1.2, "y": 0.7, "w": 0.7, "d": 2.0, "pose": "lying",
       "height_m": 0.42, "length_m": 2.0, "depth_m": 0.7}
    ],
    "camera": {"x": 1.55, "y": 3.05, "look": [0, -1],
               "lens_mm": 85, "distance_m": 2.35, "view": "frontal",
               "subject": 0, "shot": "S01"}
  }]
}
```

坐标原点在房间左上角，x 向右、y **向下**（与图像坐标一致）。`look` 是朝向的
单位向量——**不要用角度**，图像坐标里"逆时针为正"极易读反，实测画反过一次。

## 判据

主体在画面上的覆盖率 `worst`：

  <= 1.0   装得下
  > 1.0    **装不下** → 阻断（除非 --allow-tight）

装不下时给三种可执行的替代：**退后到多少米 / 换到多少毫米 / 换哪个机位方向**。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import layout_math as LM  # noqa: E402
from pipeline import layout_render as LR  # noqa: E402

VIEWS = ("frontal", "side", "top")
POSES = ("standing", "lying")


class LayoutSpecError(Exception):
    pass


def load_spec(path):
    if not os.path.exists(path):
        raise LayoutSpecError("布局文件不存在：%s" % path)
    with open(path, encoding="utf-8") as f:
        try:
            spec = json.load(f)
        except ValueError as e:
            raise LayoutSpecError("布局文件不是合法 JSON：%s" % e)
    if not isinstance(spec, dict) or not spec.get("rooms"):
        raise LayoutSpecError("布局文件缺少 rooms 数组")
    spec.setdefault("aspect", "9:16")
    if spec["aspect"] not in LM.ASPECTS:
        raise LayoutSpecError("不支持的画幅 %s" % spec["aspect"])
    for ri, room in enumerate(spec["rooms"], 1):
        for k in ("name", "width_m", "depth_m"):
            if not room.get(k):
                raise LayoutSpecError("第 %d 个房间缺 %s" % (ri, k))
        for it in room.get("items") or []:
            for k in ("name", "x", "y", "w", "d"):
                if it.get(k) is None:
                    raise LayoutSpecError("房间「%s」的物件缺 %s：%s"
                                          % (room["name"], k, it))
            # 越界是**能证明的错**：盒子画到房间外面去了
            if (it["x"] < 0 or it["y"] < 0
                    or it["x"] + it["w"] > room["width_m"] + 1e-6
                    or it["y"] + it["d"] > room["depth_m"] + 1e-6):
                raise LayoutSpecError(
                    "房间「%s」的「%s」越界：盒子 (%.2f,%.2f)-(%.2f,%.2f) "
                    "超出房间 %.2f×%.2f"
                    % (room["name"], it["name"], it["x"], it["y"],
                       it["x"] + it["w"], it["y"] + it["d"],
                       room["width_m"], room["depth_m"]))
        for it in room.get("items") or []:
            if it.get("pose") and it["pose"] not in POSES:
                raise LayoutSpecError("pose 只能是 %s" % "/".join(POSES))
        cam = room.get("camera")
        if cam:
            if cam.get("view") and cam["view"] not in VIEWS:
                raise LayoutSpecError("camera.view 只能是 %s" % "/".join(VIEWS))
            for k in ("x", "y", "lens_mm", "distance_m"):
                if cam.get(k) is None:
                    raise LayoutSpecError("房间「%s」的摄影机缺 %s"
                                          % (room["name"], k))
            if not cam.get("look"):
                raise LayoutSpecError("房间「%s」的摄影机缺 look：[dx,dy]"
                                      % room["name"])
            si = cam.get("subject")
            items = room.get("items") or []
            if si is not None and not (0 <= si < len(items)):
                raise LayoutSpecError("camera.subject=%s 超出物件范围（0..%d）"
                                      % (si, len(items) - 1))
    return spec


def subject_of(room, cam):
    """从房间物件里取出被拍主体的尺寸。"""
    items = room.get("items") or []
    si = cam.get("subject")
    if si is None:
        return None
    it = items[si]
    if not it.get("length_m") or not it.get("depth_m") or not it.get("height_m"):
        raise LayoutSpecError(
            "被拍主体「%s」缺尺寸：判定能否装下需要 length_m/depth_m/height_m"
            % it["name"])
    return {"length_m": it["length_m"], "depth_m": it["depth_m"],
            "height_m": it["height_m"], "pose": it.get("pose") or "standing"}


def evaluate(spec):
    """返回 (results, blocks, warns)。results 逐机位给覆盖率与替代方案。"""
    results, blocks, warns = [], [], []
    for room in spec["rooms"]:
        cam = room.get("camera")
        if not cam:
            continue
        subj = subject_of(room, cam)
        if subj is None:
            warns.append("房间「%s」的摄影机没指定 subject，无法判定能否装下"
                         % room["name"])
            continue
        view = cam.get("view") or "frontal"
        cov = LM.frame_coverage(cam["lens_mm"], cam["distance_m"], subj,
                                view, spec["aspect"])
        sug = LM.suggest(cam["lens_mm"], cam["distance_m"], subj, view,
                         spec["aspect"])
        results.append({"room": room["name"], "shot": cam.get("shot"),
                        "subject": (room["items"][cam["subject"]]["name"]),
                        "coverage": cov, "suggestions": sug})
        if not cov["fits"]:
            blocks.append(
                "房间「%s」%s：主体「%s」在 %s 画幅下占画面 %.2f×（宽 %.2f / 高 %.2f），"
                "**装不下**。\n    可见区域只有 %.2f×%.2fm，主体需要 %.2f×%.2fm\n"
                "    可选：%s"
                % (room["name"],
                   ("（%s）" % cam["shot"]) if cam.get("shot") else "",
                   room["items"][cam["subject"]]["name"], spec["aspect"],
                   cov["worst"], cov["ratio_w"], cov["ratio_h"],
                   cov["visible_w_m"], cov["visible_h_m"],
                   cov["subject_w_m"], cov["subject_h_m"],
                   "；".join(sug) if sug else "换机位或改主体尺寸"))
    return results, blocks, warns


def layout_rel(project, ep_seq, tag="all"):
    p = os.path.join(C.assets_root(), "layouts",
                     "E%02d_%s.png" % (ep_seq, tag))
    return C.asset_rel(p)


def main():
    ap = argparse.ArgumentParser(description="第 4.5 阶段：正交布局图")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--spec", default=None, help="默认 剧集/EP###/布局.json")
    ap.add_argument("--allow-tight", action="store_true",
                    help="装不下也继续（默认阻断）")
    ap.add_argument("--ascii", action="store_true", help="同时在终端打印 ASCII 平面")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rep = C.Report("第 4.5 阶段 · 正交布局图")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    spec_path = args.spec or os.path.join(
        C.ROOT, "剧集", "EP%03d" % ep["seq"], "布局.json")
    try:
        spec = load_spec(spec_path)
    except LayoutSpecError as e:
        rep.add("布局文件可用", False, str(e))
        rep.info("文件：%s" % spec_path)
        con.close()
        return rep.finish()
    rep.add("布局文件可用", True, "%d 个房间 · %s" % (len(spec["rooms"]), spec["aspect"]))

    results, blocks, warns = evaluate(spec)
    for r in results:
        c = r["coverage"]
        rep.add("取景可行性 %s%s" % (r["room"], (" " + r["shot"]) if r["shot"] else ""),
                c["fits"],
                "主体%s 占 %.2f×（宽 %.2f 高 %.2f）｜可见 %.2f×%.2fm"
                % (r["subject"], c["worst"], c["ratio_w"], c["ratio_h"],
                   c["visible_w_m"], c["visible_h_m"]))
        for s in r["suggestions"]:
            rep.info("    可选：%s" % s)
    for w in warns:
        rep.add("待确认（warn）", True, w)

    if blocks and not args.allow_tight:
        for b in blocks:
            rep.add("画幅内装得下", False, b)
        rep.info("这一关的价值：**在图和视频之前**就把取景问题量出来。")
        rep.info("一张首帧 ¥0.30，一个 5 秒镜头视频约 ¥14.5——都比改一行数字贵。")
        rep.info("确实要硬上：加 --allow-tight。")
        con.close()
        return rep.finish()
    rep.add("画幅内装得下", True,
            "全部通过" if not blocks else "%d 处偏紧，已按 --allow-tight 放行"
            % len(blocks))

    if args.ascii:
        for room in spec["rooms"]:
            txt, _ = LR.render_ascii(room)
            print("\n" + txt + "\n")

    # 出图：渲染到 assets（**不在 C 盘**，走 assets_root()）
    rel = layout_rel(ep["project"], ep["seq"])
    if args.dry_run:
        rep.info("dry-run：未渲染（会写到 %s）" % rel)
        con.close()
        return rep.finish()
    try:
        os.makedirs(os.path.dirname(C.abs_path(rel)), exist_ok=True)
        for room in spec["rooms"]:
            cam = room.get("camera")
            if cam:
                subj = subject_of(room, cam)
                if subj:
                    cam["_coverage"] = LM.frame_coverage(
                        cam["lens_mm"], cam["distance_m"], subj,
                        cam.get("view") or "frontal", spec["aspect"])["worst"]
        LR.render_png(spec["rooms"], C.abs_path(rel))
    except (OSError, LR.RenderError) as e:
        rep.add("已渲染布局图", False, str(e)[:90])
        con.close()
        return rep.finish()
    rep.add("已渲染布局图", True, "%s（%.0f KB）"
            % (rel, os.path.getsize(C.abs_path(rel)) / 1024.0))

    # 登记为 primary 场景的 layout 视图，与既有素材体系并列
    prim = con.execute(
        "SELECT e.id, e.name FROM entity e WHERE e.project_id=? AND e.kind='location' "
        "ORDER BY CASE e.role WHEN 'main' THEN 0 ELSE 1 END, e.id LIMIT 1",
        (ep["project_id"],)).fetchone()
    if prim:
        sha = C.file_sha256(C.abs_path(rel))
        con.execute(
            "INSERT INTO entity_ref (entity_id, view, path, is_primary, approved, sha256)"
            " VALUES (?,?,?,0,0,?) ON CONFLICT(entity_id, view) DO UPDATE SET "
            "path=excluded.path, sha256=excluded.sha256",
            (prim["id"], "layout", rel, sha))
        con.commit()
        rep.add("已登记 entity_ref(layout)", True, "%s -> %s" % (prim["name"], rel))
    con.close()
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
