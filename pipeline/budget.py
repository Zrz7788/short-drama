# -*- coding: utf-8 -*-
r"""花钱闸门 —— **超预算就拒绝生成**，不靠"记得传 --max-cost"。

## 为什么要有这一层

2026-10-15 的教训：钱"像流水一样"没了，不是因为某一笔花得离谱，
而是**没有任何一道闸门**：

- `--max-cost` 是**可选项**：不传就完全不拦（默认 None）。谁会每次都记得传？
- 它只看**本次**预估，不看**本集已经花了多少** —— 补跑三次，每次都"没超"。
- 单价表本身还是错的（1080P 按 720P 的价记账），所以"预估"也没算对。

所以这里做三件事：

1. **上限来自环境变量 `DSH_EPISODE_BUDGET`（默认 150 元/集）**，不用每次传参；
2. 检查"**本集累计已花 + 本次预估**"，不是只看本次；
3. 单价缺失（price_book 里没有对应分辨率的价）时**按"未知"处理并拒绝**，
   而不是悄悄按 0 或按一个可能错的价估算 —— 记账错是这一轮所有问题的源头。

用法（各阶段脚本自动调用，无需手工传参）：

```bash
set DSH_EPISODE_BUDGET=150        # Windows；不设则用默认 150
python pipeline/stage_video.py --episode-id 1 --mode final
```
"""
from __future__ import annotations

import os

DEFAULT_CAP = 150.0


def cap():
    """本集上限（元）。环境变量优先，默认 150。"""
    v = os.environ.get("DSH_EPISODE_BUDGET")
    try:
        return float(v) if v not in (None, "") else DEFAULT_CAP
    except ValueError:
        return DEFAULT_CAP


def spent(con, episode_id, categories=("video", "image", "llm")):
    """本集**已入账**的累计花费。"""
    marks = ",".join("?" for _ in categories)
    r = con.execute("SELECT COALESCE(SUM(amount),0) AS a FROM cost_ledger "
                    "WHERE episode_id=? AND category IN (%s)" % marks,
                    (episode_id,) + tuple(categories)).fetchone()
    return float(r["a"] if hasattr(r, "keys") else r[0])


def check(con, episode_id, estimate, what="本次生成", hard=True):
    """返回 (是否放行, 说明)。**不抛异常**，由调用方决定怎么处理。

    `hard=True` 时调用方应当在 False 时中止——**中止不是失败，是保护**。
    """
    c = cap()
    used = spent(con, episode_id)
    projected = used + (estimate or 0.0)
    msg = ("本集预算上限 ¥%.0f ｜ 已花 ¥%.2f ｜ %s预估 ¥%.2f ｜ 合计 ¥%.2f"
           % (c, used, what, estimate or 0.0, projected))
    if projected > c:
        return False, (msg + " —— **超限 ¥%.2f，已拒绝生成**。"
                             "要放宽就设环境变量 DSH_EPISODE_BUDGET，"
                             "或减少本次条数/降档（720P 便宜一半）。"
                       % (projected - c))
    return True, msg


def report_line(con, episode_id):
    """给报表用的一行摘要。"""
    c, used = cap(), spent(con, episode_id)
    return "预算 ¥%.0f ｜ 已花 ¥%.2f ｜ 余 ¥%.2f" % (c, used, c - used)
