# -*- coding: utf-8 -*-
r"""画幅覆盖量算 —— 「装不装得下」是可算的，不用猜。

## 为什么要有这个模块

第 1 集的首帧里，用户一眼看出「推车太短了」。根因不是模型偷懒，是
**竖屏 9:16 装不下一个横躺的人的全长**，而当时没有任何一步能量出这件事。

纯靠看首帧图来发现取景问题，代价是：一张图 ¥0.30，一个镜头视频 ¥14.5。
所以把"装不装得下"变成**出图之前就能算出来的数**。

## 算法

1. 由焦距与传感器尺寸求水平视场角：
   `h_fov = 2 * atan(sensor_width / (2 * f))`
2. 在距离 `d` 处的可见宽度：`W = 2 * d * tan(h_fov / 2)`
3. 竖屏 9:16 时可见高度：`H = W * (aspect_h / aspect_w)`（9:16 → `H = W * 16/9`）
4. 主体的**画面宽高**由外接盒与视线方向求出（见 `subject_extents`）
5. 覆盖率 = 主体画面尺寸 / 可见画面尺寸。**>1 就是装不下。**

机位方向决定了哪个维度是"宽"：
  - `frontal`（正对）：主体横向占画面**宽** → 横躺的人受 9:16 的窄边限制
  - `side`（侧对，长边朝深度）：长边沿视线缩短，只受**高**限制
  - `top`（俯拍）：长边横穿画面宽
"""
from __future__ import annotations

import math

# 传感器宽（毫米）。方舟/大多数视频模型对竖屏是**裁切**而非改变传感器，
# 所以这里用统一的 36mm 宽做视场角计算，再用画幅比推可见高度。
# ⚠️ 这是一个**约定**，不是厂商文档确认值。它影响的是覆盖率绝对值，
# 不影响"哪个维度先装不下"的结论——结论对约定不敏感。
SENSOR_WIDTH_MM = 36.0

ASPECTS = {"9:16": (9.0, 16.0), "16:9": (16.0, 9.0), "1:1": (1.0, 1.0)}


class LayoutError(Exception):
    """布局数据本身不合法——能证明它错，所以直接拒绝。"""


def visible_size(lens_mm, distance_m, aspect="9:16"):
    """返回 (可见宽, 可见高)，单位米。"""
    if not lens_mm or lens_mm <= 0:
        raise LayoutError("lens_mm 必须为正数，实际 %s" % lens_mm)
    if not distance_m or distance_m <= 0:
        raise LayoutError("distance_m 必须为正数，实际 %s" % distance_m)
    if aspect not in ASPECTS:
        raise LayoutError("不支持的画幅 %s（支持 %s）"
                          % (aspect, "/".join(sorted(ASPECTS))))
    aw, ah = ASPECTS[aspect]
    h_fov = 2.0 * math.atan(SENSOR_WIDTH_MM / (2.0 * float(lens_mm)))
    w = 2.0 * float(distance_m) * math.tan(h_fov / 2.0)
    h = w * (ah / aw)
    return w, h


def subject_extents(subject, view):
    """主体在**画面**上的宽与高（米）。

    subject: {"length_m", "depth_m", "height_m", "pose"}
      pose = "standing" | "lying"
    view:    "frontal" | "side" | "top"

    这是保守的**外接盒**估计，不含透视缩短的细致建模——目的是判断
    "大致装不装得下"，不是做精确预演。宁可高估（判装不下）也不要低估。
    """
    L = float(subject.get("length_m") or 0)
    D = float(subject.get("depth_m") or 0)
    H = float(subject.get("height_m") or 0)
    pose = subject.get("pose") or "standing"
    if min(L, D, H) <= 0:
        raise LayoutError("主体尺寸必须为正：length=%s depth=%s height=%s" % (L, D, H))

    if pose == "lying":
        if view == "frontal":
            # 头尾横穿画面：宽 = 身长，高 = 体厚
            return L, max(D, H)
        if view == "side":
            # 长边沿视线（缩短），画面宽 = 体厚，高 = 身长
            return max(D, H), L
        # top：俯拍，宽 = 身长，高 = 体宽
        return L, max(D, H)

    # standing
    if view == "top":
        return max(L, D), min(L, D)
    if view == "side":
        return D, H
    return L, H                      # frontal：宽 = 肩宽，高 = 身高


def frame_coverage(lens_mm, distance_m, subject, view="frontal", aspect="9:16"):
    """返回覆盖率字典。

    fits    主体宽高都在可见范围内
    ratio_w / ratio_h   各维度占用率，>1 即溢出
    worst   两者较大者——它是"装不装得下"的判据
    """
    vis_w, vis_h = visible_size(lens_mm, distance_m, aspect)
    sub_w, sub_h = subject_extents(subject, view)
    rw, rh = sub_w / vis_w, sub_h / vis_h
    return {
        "visible_w_m": round(vis_w, 3),
        "visible_h_m": round(vis_h, 3),
        "subject_w_m": round(sub_w, 3),
        "subject_h_m": round(sub_h, 3),
        "ratio_w": round(rw, 3),
        "ratio_h": round(rh, 3),
        "worst": round(max(rw, rh), 3),
        "fits": max(rw, rh) <= 1.0,
        "aspect": aspect,
        "view": view,
        "lens_mm": lens_mm,
        "distance_m": distance_m,
    }


def suggest(lens_mm, distance_m, subject, view="frontal", aspect="9:16"):
    """装不下时给可执行的替代方案，不是只说"不行"。

    可动的三个量：**退后**、**换广角**、**换机位方向**。分别给出所需数值。
    """
    out = []
    cov = frame_coverage(lens_mm, distance_m, subject, view, aspect)
    if cov["fits"]:
        return out
    sub_w, sub_h = subject_extents(subject, view)
    vis_w, vis_h = visible_size(lens_mm, distance_m, aspect)

    # 1) 退后到装得下
    if cov["ratio_w"] > 1 and vis_w > 0 and cov["ratio_h"] <= 1:
        need = distance_m * cov["ratio_w"] * 1.05
        out.append("退后到约 %.2f 米（现在 %.2f 米）" % (need, distance_m))
    elif cov["ratio_h"] > 1 and vis_h > 0 and cov["ratio_w"] <= 1:
        need = distance_m * cov["ratio_h"] * 1.05
        out.append("退后到约 %.2f 米（现在 %.2f 米）" % (need, distance_m))
    elif cov["worst"] > 1:
        need = distance_m * cov["worst"] * 1.05
        out.append("退后到约 %.2f 米，或同时换广角（两个维度都溢出）"
                   % need)

    # 2) 换广角：需要多大视场角
    aw, ah = ASPECTS[aspect]
    req_w = max(sub_w, sub_h * (aw / ah)) * 1.05
    if req_w > 0 and distance_m > 0:
        need_fov = 2.0 * math.atan(req_w / (2.0 * distance_m))
        need_lens = SENSOR_WIDTH_MM / (2.0 * math.tan(need_fov / 2.0))
        if need_lens < lens_mm:
            out.append("或换到约 %.0fmm 以下（现在 %.0fmm）"
                       % (need_lens, lens_mm))

    # 3) 换机位方向
    for alt in ("side", "top", "frontal"):
        if alt == view:
            continue
        c2 = frame_coverage(lens_mm, distance_m, subject, alt, aspect)
        if c2["fits"]:
            out.append("或改成「%s」机位（该方向覆盖率 %.2f，装得下）"
                       % (alt, c2["worst"]))
            break
    return out
