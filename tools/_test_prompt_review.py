# -*- coding: utf-8 -*-
r"""负向测试：把今天真实踩过的坑重新注入，验证 prompt_review 抓得住。

**为什么必须做**：这个审查器改了两版才把误报压下来（30→9）。
压误报的过程很容易**顺手把灵敏度也压掉**——那就变成一个永远报绿灯的废物。
所以拿**今天实际造成损失的五处错误**做样本，一处一处验。

样本（全部来自今天的真实事故）：
  1 把场景道具写进人物参考      -> 塑形器被画成横穿嘴唇的刀片（阻断）
  2 描述里写真实品牌名          -> 模板整段发给模型，等于注入品牌（阻断）
  3 描述里留内部编号            -> F-18 之类会原样发给模型（阻断）
  4 描述里用否定式              -> 项目结论：否定约束对画面无效（提醒）
  5 提到标签/文件却不写"纯色空白" -> 模型自己填内容（踩过：填上真实商标）（提醒）
  6 人物识别锚点在描述里缺失     -> 视觉设定写了手术帽，实体描述没写，图里就没帽（提醒）

做法：临时把样本注入数据库 -> 跑审查 -> 期望命中 -> 还原。
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "drama.db")
PY = sys.executable

# 注入样本：(表列, 实体名, 注入文本, 期望出现的判据关键词)
CASES = [
    ("description", "盛伯渊",
     "口中咬着一枚金属口部塑形器（薄金属边沿自唇间微露）",
     "场景道具"),
    ("description", "沈九",
     "身穿印有「美团」标识的工作服",
     "真实品牌名"),
    ("description", "黄铜签牌",
     "符合 F-18 行业规范",
     "内部编号"),
    ("description", "殡仪馆整容室",
     "严禁摆放遗体冷藏柜，不得出现多余杂物",
     "否定式"),
    ("description", "透明润肤膏",
     "瓶身贴有产品标签",
     "纯色空白"),
    ("description", "主管",
     "中年男性，短袖衬衫外搭深色夹克",
     "像外观"),
]


def run_review():
    p = subprocess.run([PY, os.path.join(ROOT, "tools", "prompt_review.py"),
                        "--episode-id", "1"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def main():
    bak = DB + ".bak-before-reviewtest-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, bak)
    print("已备份 -> %s\n" % bak)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    orig = {}
    for col, name, text, _expect in CASES:
        r = con.execute("select id, %s as v from entity where name=?" % col,
                        (name,)).fetchone()
        if not r:
            print("  ⚠️ 没有实体「%s」" % name)
            continue
        orig[(col, name)] = (r["id"], r["v"] or "")
        new = (r["v"] or "") + "；" + text
        con.execute("update entity set %s=? where id=?" % col, (new, r["id"]))
    con.commit()
    print("已注入 %d 处样本\n" % len(orig))

    out = run_review()
    print("=" * 74)
    print("审查结果（期望每个样本都被命中）")
    print("=" * 74)
    fail = 0
    for col, name, text, expect in CASES:
        # 找到与该实体相关的、且含期望关键词的行
        hit = False
        for ln in out.splitlines():
            if name in ln and expect in ln:
                hit = True
                print("  ✅ %-8s %-12s 期望「%s」→ 命中" % (col, name, expect))
                print("       %s" % ln.strip()[:104])
                break
        if not hit:
            fail += 1
            print("  ❌ %-8s %-12s 期望「%s」→ **没命中**" % (col, name, expect))
    print()
    print("  未命中 %d / %d" % (fail, len(CASES)))

    # 还原
    for (col, name), (eid, v) in orig.items():
        con.execute("update entity set %s=? where id=?" % col, (v, eid))
    con.commit()
    con.close()
    print("\n已还原全部样本")

    out2 = run_review()
    m = re.search(r"阻断\s*(\d+)\s*项，提醒\s*(\d+)\s*项", out2)
    print("还原后：%s" % (m.group(0) if m else "（没读到统计）"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
