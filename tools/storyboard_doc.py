# -*- coding: utf-8 -*-
r"""从数据库生成**备案用分镜脚本**（`剧集/EP0NN/分镜.md`）。

## 为什么要有这个工具

备案（红果三类·平台自审）要交 **剧本大纲 + 分镜脚本 + 成片**。
而分镜脚本**必须和成片对得上**——EP001 踩过的坑：
`剧集/EP001/分镜.md` 是**早期手写的 12 镜**，而实际拍出来的是 **14 镜**
（第 5 阶段重跑过，镜号与内容都变了）。**交上去对不上，等于自己给自己找麻烦。**

所以分镜脚本**不再手写**：`shot` 表才是真源，这份文档由它生成。
以后每次分镜重跑，重新生成一次即可（一行命令，不花钱）。

## 与"提示词"的分工

- **提示词**（`shot.prompt`）是给模型的工程文本，几百上千字，**不进备案文档**
- 这里只出**人能读的分镜表**：镜号/景别/运镜/画面/动作/台词/音效/转场/画面文字/连续性锁

用法：
  python tools/storyboard_doc.py --episode-id 1
  python tools/storyboard_doc.py --episode-id 1 --out 剧集/EP001/分镜.md
"""
import argparse
import datetime as dt
import io
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

FIELDS = [
    ("shot_size", "景别"), ("camera_move", "运镜"), ("camera_angle", "机位"),
    ("lens_mm", "焦段"), ("dof", "景深"), ("composition", "构图"),
    ("movement_speed", "运镜速度"),
]
LOCKS = [
    ("screen_direction", "轴线"), ("count_lock", "计数"),
    ("stillness_lock", "静止"), ("positive_locks", "保持"),
]


def g(row, key):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return ""
    return "" if v is None else str(v).strip()


def main():
    ap = argparse.ArgumentParser(description="从库生成备案用分镜脚本")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--out", default=None)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    cols = [r[1] for r in con.execute("PRAGMA table_info(shot)")]
    want = ["seq", "duration_s", "description", "dialogue", "sfx", "transition",
            "on_screen_text", "action", "lighting", "ambient_sound", "entrance",
            "emotion", "color_temp", "material_notes", "fg_mg_bg", "strategy",
            "scene_id"] + [f for f, _ in FIELDS] + [f for f, _ in LOCKS]
    sel = ", ".join([c for c in want if c in cols])
    rows = [dict(r) for r in con.execute(
        "SELECT %s FROM shot WHERE episode_id=? ORDER BY seq" % sel,
        (args.episode_id,))]
    total = sum(float(r.get("duration_s") or 0) for r in rows)
    con.close()

    out = args.out or os.path.join(C.ROOT, "剧集", "EP%03d" % ep["seq"], "分镜.md")
    if os.path.exists(out):                      # 旧版留档，别让手写内容无声消失
        bak = out + ".bak-before-regen-%s" % dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(out, bak)
        print("旧版已备份：", os.path.relpath(bak, C.ROOT))

    L = []
    L.append("# EP%03d 分镜（备案版·由数据库生成）" % ep["seq"])
    L.append("")
    L.append("> 集名：%s ｜ 项目：%s" % (ep["title"] or "-", ep["project"]))
    L.append("> 共 **%d 镜**，总时长 **%.1f 秒**；本文件由 `tools/storyboard_doc.py` "
             "从 `shot` 表生成，**与成片逐镜对应**。" % (len(rows), total))
    L.append("> 生成时间：%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    L.append("")
    L.append("| 镜号 | 时长 | 景别 | 运镜 | 画面内容 |")
    L.append("|---|---|---|---|---|")
    for r in rows:
        L.append("| S%02d | %ss | %s | %s | %s |"
                 % (r["seq"], r["duration_s"], r.get("shot_size") or "-",
                    r.get("camera_move") or "-", (r.get("description") or "-")))
    L.append("")
    L.append("---")
    L.append("")
    for r in rows:
        L.append("## S%02d　%s" % (r["seq"], r.get("description") or ""))
        L.append("")
        meta = " ｜ ".join("%s：%s" % (label, r.get(f)) for f, label in FIELDS
                           if r.get(f))
        if meta:
            L.append("- " + meta)
        if r.get("action"):
            L.append("- **动作**：%s" % r["action"])
        if r.get("lighting"):
            L.append("- **光线**：%s" % r["lighting"])
        if r.get("dialogue"):
            L.append("- **台词**：%s" % r["dialogue"])
        if r.get("sfx"):
            L.append("- **音效**：%s" % r["sfx"])
        if r.get("transition"):
            L.append("- **转场**：%s" % r["transition"])
        if r.get("on_screen_text"):
            L.append("- **画面文字**：%s" % r["on_screen_text"])
        locks = "；".join("%s：%s" % (label, r.get(f)) for f, label in LOCKS
                          if r.get(f))
        if locks:
            L.append("- **连续性锁**：%s" % locks)
        L.append("")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print("已生成 %s（%d 镜，%.1f 秒）" % (os.path.relpath(out, C.ROOT), len(rows), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
