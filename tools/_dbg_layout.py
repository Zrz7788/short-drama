# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, r"C:\Users\zrz\Documents\deepseek-harness\default-workspace")
from pipeline import layout_math as LM  # noqa: E402
from pipeline import layout_render as LR  # noqa: E402

lying = {"length_m": 1.75, "depth_m": 0.45, "height_m": 0.42, "pose": "lying"}
standing = {"length_m": 0.45, "depth_m": 0.26, "height_m": 1.66, "pose": "standing"}

print("=== 量一下第 1 集两场的取景可行性 ===")

print("\n[SC001 化妆间 9:16 竖屏]")
for lens, dist, view in ((50, 2.6, "frontal"), (50, 2.6, "side"),
                         (85, 2.6, "frontal"), (28, 2.6, "frontal"),
                         (35, 3.2, "side")):
    c = LM.frame_coverage(lens, dist, lying, view)
    print("  %2dmm @ %.1fm %-8s 可见 %.2f×%.2f  主体 %.2f×%.2f  占位 w=%.2f h=%.2f -> %s"
          % (lens, dist, view, c["visible_w_m"], c["visible_h_m"],
             c["subject_w_m"], c["subject_h_m"], c["ratio_w"], c["ratio_h"],
             "装得下" if c["fits"] else "**装不下**"))

print("\n[SC001 站着的沈九]")
c = LM.frame_coverage(85, 1.2, standing, "frontal")
print("  85mm @ 1.2m 站立 占位 w=%.2f h=%.2f -> %s"
      % (c["ratio_w"], c["ratio_h"], "装得下" if c["fits"] else "**装不下**"))

print("\n[装不下时给什么替代方案]")
for s in LM.suggest(85, 2.6, lying, "frontal"):
    print("  ·", s)

print("\n\n=== SC001 俯视 ASCII（真实比例，格 0.1m）===")
room1 = {
    "name": "整容室",
    "width_m": 4.0, "depth_m": 3.4,
    "items": [
        {"name": "整容台", "x": 1.0, "y": 1.2, "w": 0.7, "d": 2.0, "pose": "lying"},
        {"name": "化妆台", "x": 3.3, "y": 0.5, "w": 0.6, "d": 1.6},
        {"name": "器械柜", "x": 0.1, "y": 2.4, "w": 0.5, "d": 0.9},
    ],
    "camera": {"x": 1.35, "y": 2.7, "yaw_deg": -90, "lens_mm": 85, "distance_m": 2.6},
}
txt, size = LR.render_ascii(room1)
print(txt)
print("(网格 %d×%d，真实比例 4.0m × 3.4m)" % size)
print("  1=整容台(躺)  2=化妆台  3=器械柜  C=摄影机  .=视线")

print("\n=== SC002 后廊 俯视 ASCII ===")
room2 = {
    "name": "后廊",
    "width_m": 1.8, "depth_m": 8.0,
    "items": [
        {"name": "沈九推车", "x": 1.1, "y": 5.4, "w": 0.65, "d": 1.9, "pose": "lying"},
    ],
    "camera": {"x": 1.5, "y": 6.4, "yaw_deg": -90, "lens_mm": 35, "distance_m": 3.2},
}
txt2, size2 = LR.render_ascii(room2)
print(txt2)
