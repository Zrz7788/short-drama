# -*- coding: utf-8 -*-
r"""正交布局图 —— 把「空间站不站得下、画框装不装得下」画出来。

## 为什么要有这个

第 1 集踩的两个坑，纯文字描述防不住：

  1. 冷柜 / 转运推车 / 化妆台被塞进**同一间屋**——空间根本不成立
  2. 竖屏 9:16 **装不下横躺人物的全长**——用户一眼看出"推车太短了"

而知识库里本来就指向这条：**"静止图给你一堵墙，缓慢移动的镜头给你几何结构"**。
布局图是这句话的工程化版本：**在出任何图之前，先把几何定下来。**

## 归一化约定（关键）

图按**真实比例**画（米），否则"装不下"这件事看不出来。
但画布尺度各场景差很多（过道 8 米、化妆间 4 米），所以：
**取最长的那个场景定为标准尺度 1.0，其余按真实比例缩放。**
这样既保留了"过道比房间长"的真实关系，又不会被极端尺度压扁。

## 两种输出

  ASCII   文本，可进 Knowledge / 剧本，不花钱，diff 友好
  PNG     Pillow 绘制，直接看图
"""
from __future__ import annotations

import math
import os

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:                                    # pragma: no cover
    HAS_PIL = False

from pipeline import layout_math as LM  # noqa: E402


# 中文字体。找不到就退回默认字体——**默认字体没有中文字形，会画成一排方框**，
# 实测踩过：图上全是豆腐块，看起来像渲染坏了。所以优先找 CJK 字体。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",      # 黑体
    r"C:\Windows\Fonts\simsun.ttc",      # 宋体
    r"C:\Windows\Fonts\Deng.ttf",        # 等线
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def load_font(size):
    """返回一个能画中文的字体对象；找不到就返回 None（调用方退回默认）。"""
    if not HAS_PIL:
        return None
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return None


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- ASCII 平面图
def _blank(w, h, fill=" "):
    return [[fill] * w for _ in range(h)]


def _put(grid, x, y, s):
    """在网格写字，越界忽略。"""
    if y < 0 or y >= len(grid):
        return
    for i, ch in enumerate(s):
        xx = x + i
        if 0 <= xx < len(grid[0]):
            grid[y][xx] = ch


def _hline(grid, x0, x1, y, ch="-", corner_l="+", corner_r="+"):
    for x in range(x0, x1 + 1):
        grid[y][x] = ch
    grid[y][x0] = corner_l
    grid[y][x1] = corner_r


def _vline(grid, x, y0, y1, ch="|"):
    for y in range(y0, y1 + 1):
        grid[y][x] = ch


def _cam_dir(cam):
    """摄影机朝向的单位向量。

    ⚠️ 用 `look: [dx, dy]` 而**不是角度**：图像坐标里 +y 向下，
    "yaw 逆时针为正"这种约定在文本里极易读反，第 1 集实测就画反过一次。
    方向向量没有歧义。兼容旧的 yaw_deg 写法。
    """
    look = cam.get("look")
    if look and len(look) == 2:
        dx, dy = float(look[0]), float(look[1])
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n
    yaw = math.radians(cam.get("yaw_deg") or 0.0)
    return math.cos(yaw), math.sin(yaw)


def _grid_size(room, cell, char_aspect, focus=None):
    x0, y0, w, d = _viewbox(room, focus)
    W = max(8, int(round(w / cell)))
    H = max(6, int(round(d / cell / char_aspect)))
    return x0, y0, w, d, W, H


def _viewbox(room, focus):
    """返回要画的矩形 (x0, y0, w, d)。focus=None 画整间房。"""
    if not focus:
        return 0.0, 0.0, room["width_m"], room["depth_m"]
    cx, cy, half_w = float(focus[0]), float(focus[1]), float(focus[2])
    hw = min(half_w, room["width_m"])
    hd = hw * (room["depth_m"] / room["width_m"]) if room["width_m"] else hw
    x0 = max(0.0, min(cx - hw / 2, room["width_m"] - hw))
    y0 = max(0.0, min(cy - hd / 2, room["depth_m"] - hd))
    return x0, y0, hw, min(hd, room["depth_m"] - y0)


def render_ascii(room, cell=0.1, char_aspect=2.0, focus=None):
    """把一个房间渲染成 ASCII 平面图。

    room = {"name","width_m","depth_m","items":[{name,x,y,w,d,pose}],
            "camera":{x,y,look:[dx,dy],lens_mm,distance_m}}
    坐标原点在房间左上角，x 向右，y **向下**（与图像坐标一致）。
    focus = [cx, cy, 半宽] 时只画这一块——长过道里整间房画出来主体太小。
    """
    x0m, y0m, wm, dm, W, H = _grid_size(room, cell, char_aspect, focus)
    sx = W / wm
    sy = H / dm

    def px(x):
        return int(round((x - x0m) * sx))

    def py(y):
        return int(round((y - y0m) * sy))

    grid = _blank(W + 1, H + 1)
    _hline(grid, 0, W, 0, "=", "+", "+")
    _hline(grid, 0, W, H, "=", "+", "+")
    _vline(grid, 0, 0, H, "|")
    _vline(grid, W, 0, H, "|")

    for i, it in enumerate(room.get("items") or []):
        a = px(it["x"])
        b = px(it["x"] + it["w"])
        c = py(it["y"])
        e = py(it["y"] + it["d"])
        a, b = max(0, min(a, W)), max(0, min(b, W))
        c, e = max(0, min(c, H)), max(0, min(e, H))
        if b > a:
            _hline(grid, a, b, c, "-", "[", "]")
            if e > c:
                _hline(grid, a, b, e, "-", "[", "]")
        if e > c:
            _vline(grid, a, c, e, "|")
            _vline(grid, b, c, e, "|")
        _put(grid, a + 1, c + 1, "[%d]" % (i + 1))

    cam = room.get("camera") or {}
    if cam:
        cx, cy = px(cam["x"]), py(cam["y"])
        _put(grid, cx, cy, "C")
        dx, dy = _cam_dir(cam)
        for t in range(1, 9):
            _put(grid, int(round(cx + dx * t * 2)), int(round(cy + dy * t)), ".")
        # 可见画框宽度：在主体所在距离处画一条横线，'=' 表示边界
        try:
            vis_w, _ = LM.visible_size(cam.get("lens_mm"), cam.get("distance_m"))
            half = vis_w / 2.0
            # 垂直于视线的方向
            perp = (-dy, dx)
            mid = (cam["x"] + dx * cam["distance_m"], cam["y"] + dy * cam["distance_m"])
            for sgn in (-1, 1):
                for t in range(0, int(round(half / cell)) + 1):
                    xx = mid[0] + perp[0] * sgn * t * cell
                    yy = mid[1] + perp[1] * sgn * t * cell
                    if 0 <= xx <= room["width_m"] and 0 <= yy <= room["depth_m"]:
                        X, Y = px(xx), py(yy)
                        if 0 <= X <= W and 0 <= Y <= H and grid[Y][X] == " ":
                            grid[Y][X] = "'"
        except LM.LayoutError:
            pass

    lines = ["".join(r).rstrip() for r in grid]
    return "\n".join(lines), (W, H)


# ---------------------------------------------------------------- PNG 平面图
def render_png(rooms, out_path, pad_m=0.0, scale=None):
    """把若干房间画进一张 PNG（俯视正交）。

    rooms: [{name,width_m,depth_m,items,camera}] —— 顺序即从上到下排列。
    返回落盘路径。
    """
    if not HAS_PIL:
        raise RenderError("Pillow 不可用，无法出 PNG（可用 render_ascii 代替）")

    if scale is None:
        # 归一化：取最长场景定为标准尺度（见文件头说明）
        longest = max(max(r["width_m"], r["depth_m"]) for r in rooms)
        scale = 1600.0 / longest          # 长边 1600 px

    gap = 90
    widths = [int(round(r["width_m"] * scale)) for r in rooms]
    heights = [int(round(r["depth_m"] * scale)) for r in rooms]
    W = max(widths) + 240
    H = sum(heights) + gap * (len(rooms) + 1) + 40

    img = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(img)
    BLUE, DARK, RED, GREY = (70, 110, 190), (40, 44, 52), (200, 60, 60), (150, 155, 165)
    f_txt = load_font(17)
    f_sm = load_font(15)

    def T(xy, s, fill=DARK, font=None):
        # 有 CJK 字体就用；没有就退回默认（会画成方框，但至少不报错）
        d.text(xy, s, fill=fill, font=font or f_txt)

    y = gap
    for r, wpx, hpx in zip(rooms, widths, heights):
        x0 = 170
        y0 = y
        # 房间轮廓
        d.rectangle([x0, y0, x0 + wpx, y0 + hpx], outline=DARK, width=3)
        T((x0, y0 - 26), "%s   %.1f × %.1f m"
          % (r["name"], r["width_m"], r["depth_m"]))

        def P(mx, my):
            return (x0 + mx * scale, y0 + my * scale)

        for i, it in enumerate(r.get("items") or []):
            a = P(it["x"], it["y"])
            b = P(it["x"] + it["w"], it["y"] + it["d"])
            fill = (232, 238, 250) if it.get("pose") != "lying" else (252, 236, 236)
            d.rectangle([a, b], outline=BLUE, width=2, fill=fill)
            T((a[0] + 4, a[1] + 4), "%d %s" % (i + 1, it["name"]), BLUE, f_sm)

        cam = r.get("camera") or {}
        if cam:
            c = P(cam["x"], cam["y"])
            d.ellipse([c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 7], fill=RED)
            dx, dy = _cam_dir(cam)
            d.line([c, (c[0] + dx * 60, c[1] + dy * 60)], fill=RED, width=3)
            # ⚠️ 画框线要画在**主体所在距离处**，不是固定偏移。
            #    画错位置比不画更糟：会让人以为取景范围在别处。
            #    距离直接取 camera.distance_m，主体中心由它推出。
            try:
                vis_w, _ = LM.visible_size(cam.get("lens_mm"), cam.get("distance_m"))
                perp = (-dy, dx)
                mid = (cam["x"] + dx * cam["distance_m"],
                       cam["y"] + dy * cam["distance_m"])
                half = vis_w / 2.0
                left = P(mid[0] + perp[0] * half, mid[1] + perp[1] * half)
                right = P(mid[0] - perp[0] * half, mid[1] - perp[1] * half)
                cov = cam.get("_coverage")
                # **装不下就画红**——颜色本身就是判据，不用读数字
                ok = cov is None or cov <= 1.0
                col = (30, 140, 90) if ok else (205, 50, 50)
                d.line([left, right], fill=col, width=5)
                for pt in (left, right):
                    d.ellipse([pt[0] - 5, pt[1] - 5, pt[0] + 5, pt[1] + 5], fill=col)
                label = "%smm  画框宽 %.2fm%s" % (
                    cam["lens_mm"], vis_w,
                    "" if cov is None else ("  覆盖 %.2f× 装得下" % cov if ok
                                            else "  覆盖 %.2f× **装不下**" % cov))
                # 标签放在画框线下方 22px，并加白底——压在物体名上就读不清了
                lx = min(left[0], right[0])
                ly = max(left[1], right[1]) + 12
                bb = d.textbbox((lx, ly), label, font=f_sm)
                d.rectangle([bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2],
                            fill=(250, 250, 248))
                T((lx, ly), label, col, f_sm)
            except LM.LayoutError:
                pass

        y = y0 + hpx + gap

    T((170, 16), "正交布局图（俯视）· 真实比例 · 绿线=可见画框宽度（画在主体距离处）"
                 "· 红点=摄影机", GREY, f_sm)
    img.save(out_path)
    return out_path
