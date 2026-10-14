# -*- coding: utf-8 -*-
r"""**总账**：把三笔钱放一张表上——这是本项目一直缺的东西。

## 为什么缺

`cost_ledger` 只记**图片/视频生成**。而实际花钱的地方有三处：

| 流 | 记在哪 | 以前能不能看到 |
|---|---|---|
| ① 图片/视频生成（Ark） | 项目 `cost_ledger` | ✅ 能 |
| ② 文本 LLM（第 1/2/5/6 阶段） | 项目 `cost_ledger`（category='llm'） | ❌ **以前完全不记**（2026-10-15 才补上钩子） |
| ③ DSH 对话 token | `~/.dsh/.dshw-usage.json`（宿主的挂件在记） | ⚠️ 只有挂件里能看到，**不进项目账本** |

于是出现"**项目账本 ¥197，用户账单 ¥400，差的钱没人说得清**"。
总账工具就是为了让这句话以后不成立。

## 用法

```bash
python tools/cost_report.py                # 全部
python tools/cost_report.py --days 7       # 只看最近 7 天
```
"""
import argparse
import io
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

DSHW = os.path.join(os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh"),
                    ".dshw-usage.json")


def project_ledger(db):
    con = sqlite3.connect(db)
    by_cat, by_day = defaultdict(float), defaultdict(float)
    for cat, amt, ts in con.execute("SELECT category, amount, ts FROM cost_ledger"):
        by_cat[cat or "?"] += amt or 0
        by_day[(ts or "?")[:10]] += amt or 0
    n = con.execute("SELECT COUNT(*) FROM cost_ledger").fetchone()[0]
    con.close()
    return by_cat, by_day, n


def dsh_dialog():
    """宿主挂件记的对话花费（④ 不在项目库里）。"""
    if not os.path.exists(DSHW):
        return {}, 0
    try:
        d = json.load(io.open(DSHW, encoding="utf-8"))
    except (ValueError, OSError):
        return {}, 0
    by_day, n = defaultdict(float), 0
    for e in d.get("events") or []:
        by_day[e.get("day") or "?"] += e.get("cost") or 0
        n += 1
    return by_day, n


def main():
    ap = argparse.ArgumentParser(description="总账：生成 + 文本 LLM + 对话")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天")
    C.add_common_args(ap)
    args = ap.parse_args()

    by_cat, gen_day, n_gen = project_ledger(args.db)
    dia_day, n_dia = dsh_dialog()

    gen_total = sum(by_cat.values())
    dia_total = sum(dia_day.values())

    print("=" * 78)
    print("总账（三笔钱）")
    print("=" * 78)
    print("\n① 项目账本 cost_ledger（图片/视频/文本 LLM，共 %d 笔）" % n_gen)
    for cat in sorted(by_cat, key=lambda c: -by_cat[c]):
        label = {"image": "图片生成", "video": "视频生成", "llm": "文本 LLM",
                 "audio": "音频"}.get(cat, cat)
        print("    %-10s ¥%8.2f" % (label, by_cat[cat]))
    print("    %-10s ¥%8.2f" % ("小计", gen_total))
    if not by_cat.get("llm"):
        print("    ⚠️ 没有 `llm` 这一类 = **文本 LLM 一分钱都没记过**（钩子 2026-10-15 才加）")

    print("\n② DSH 对话（不在项目库里，宿主挂件在记，共 %d 轮）" % n_dia)
    print("    %-10s ¥%8.2f" % ("小计", dia_total))
    if not n_dia:
        print("    （读不到 %s）" % DSHW)

    print("\n" + "-" * 78)
    print("合计 ¥%.2f  =  生成 ¥%.2f  +  对话 ¥%.2f"
          % (gen_total + dia_total, gen_total, dia_total))

    days = sorted(set(list(gen_day) + list(dia_day)))
    if args.days:
        days = days[-args.days:]
    if days:
        print("\n按日（项目账本 ／ 对话）")
        for d in days:
            print("    %s   ¥%7.2f  ／ ¥%7.2f   = ¥%7.2f"
                  % (d, gen_day.get(d, 0), dia_day.get(d, 0),
                     gen_day.get(d, 0) + dia_day.get(d, 0)))

    # Ark 余额（挂件记的），只作参考
    try:
        d = json.load(io.open(DSHW, encoding="utf-8"))
        if d.get("lastBalance") is not None:
            print("\n方舟账户余额（挂件读数）：¥%.2f" % d["lastBalance"])
    except (ValueError, OSError, KeyError):
        pass

    print("\n⚠️ 口径说明：`cost_ledger` 记的是**按单价估算**的应付；")
    print("   平台账单可能因**失败任务 / 未登记价格 / 四舍五入**而有出入。")
    print("   真正的对账依据是平台后台的消费明细，本表用来**定位差额在哪一类**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
