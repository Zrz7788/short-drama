# -*- coding: utf-8 -*-
"""把控制台查到的真实价目写进 price_book。

数据来源：火山方舟控制台「模型广场」各模型的详情页，2026-10-10 截图确认。
全部标 confidence='high'、source 写明出处。

几个要点：
1. **图片按张计费，且参考图也要钱**。Seedream 5.0 pro：输出图 ¥0.30/张，
   输入图 ¥0.02/张（**首张免费**）。我们原来完全没算输入图——
   而派生视角和首帧图都要传参考图。
2. **视频官方是按 token 计费，不是按秒**。控制台给的是「元/百万tokens」，
   且分「包含视频输入 / 不含视频输入」两档，还随输出分辨率变（截图是 480P 档）。
   我们 price_book 里原有的 ¥1.45/秒、¥1.00/秒 是用户从控制台看到的秒单价，
   两者并存：秒单价用于预估，token 单价是官方基准。
3. 旧的那条 doubao-seedream-4-0-250828（¥0.2/张）对应的模型**本账号没开通**，
   保留但标注，避免以后有人误用。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import common as C

SRC = "控制台模型详情页 2026-10-10"

ROWS = [
    # ---- 图片：按张 ----
    # 输出图是主要成本；输入图（参考图）首张免费，之后每张 0.02
    ("doubao-seedream-5-0-pro-260628", "图片·输出图", None, "image", 0.30),
    ("doubao-seedream-5-0-pro-260628", "图片·输入图(首张免费)", None, "image_in", 0.02),
    # ---- 视频：官方 token 计费（480P 档）----
    ("doubao-seedance-2-5-260628", "480P·包含视频输入", "480p", "mtoken", 42.0),
    ("doubao-seedance-2-5-260628", "480P·不含视频输入", "480p", "mtoken", 70.0),
    ("doubao-seedance-2-0-260128", "480P·包含视频输入", "480p", "mtoken", 28.0),
    ("doubao-seedance-2-0-260128", "480P·不含视频输入", "480p", "mtoken", 46.0),
    # ---- 文本：官方 token 计费 ----
    ("doubao-seed-2-1-pro-260915", "推理输入", None, "mtoken", 6.0),
    ("doubao-seed-2-1-pro-260915", "推理输出", None, "mtoken", 30.0),
    ("glm-5-3-flash-260828", "推理输入", None, "mtoken", 0.8),
    ("glm-5-3-flash-260828", "推理输出", None, "mtoken", 2.8),
    ("deepseek-v4-1-flash-260910", "推理输入", None, "mtoken", 1.0),
    ("deepseek-v4-1-flash-260910", "推理输出", None, "mtoken", 4.0),
]


def main():
    con = C.connect(C.DEFAULT_DB)
    added = skipped = 0
    for mid, variant, res, unit, price in ROWS:
        ex = con.execute("SELECT 1 FROM price_book WHERE model_id=? AND variant=? "
                         "AND unit=?", (mid, variant, unit)).fetchone()
        if ex:
            skipped += 1
            continue
        con.execute("INSERT INTO price_book (model_id, variant, resolution, unit,"
                    " unit_price, currency, source, confidence) "
                    "VALUES (?,?,?,?,?,'CNY',?,'high')",
                    (mid, variant, res, unit, price, SRC))
        added += 1
    # 旧模型：标注它在本账号不可用，免得以后被误用
    con.execute("UPDATE price_book SET source=?, confidence='low' "
                "WHERE model_id='doubao-seedream-4-0-250828'",
                ("**该模型本账号未开通**（控制台显示已开通，API 返回 404）。"
                 "留着仅作参考。" + SRC,))
    con.commit()

    print("新增 %d 条，已存在跳过 %d 条\n" % (added, skipped))
    print("%-34s %-24s %-8s %8s  %s" % ("模型", "档位", "单位", "单价", "置信度"))
    print("-" * 96)
    for r in con.execute("SELECT model_id, variant, unit, unit_price, confidence "
                         "FROM price_book ORDER BY model_id, unit"):
        print("%-34s %-24s %-8s %8s  %s" % tuple(r))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
