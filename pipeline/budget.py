# -*- coding: utf-8 -*-
r"""花钱**可见性** —— 每步都报"本集已花多少"，默认**不拦人**。

## 为什么默认不拦（2026-10-15 用户的决定）

第一版做成了硬闸门（超 ¥150 直接拒绝生成）。用户明确要求撤掉：
**"第一次做少不了花冤枉钱"**，真正要的是把流程跑成"实践过、效果差不多"的配方，
而不是被一个数字挡住。

所以这里退回到**只报数**：
- 默认：打印「本集已花 / 本次预估 / 合计」，**放行**；
- **只有显式设了 `DSH_EPISODE_BUDGET` 才拦截**——想收紧的时候自己开。

> 保留它的价值：钱"像流水一样"那次的根因之一，是**每一步都看不见累计**。
> 报数与拦截是两件事，这里只做前者。

用法：

```bash
python pipeline/stage_video.py --episode-id 1 --mode final      # 只报数
set DSH_EPISODE_BUDGET=150                                      # 想拦了再开
```
"""
from __future__ import annotations

import os

DEFAULT_CAP = None          # None = 不拦


def cap():
    """本集上限（元）。**默认 None = 不拦**；设了 DSH_EPISODE_BUDGET 才生效。"""
    v = os.environ.get("DSH_EPISODE_BUDGET")
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def spent(con, episode_id, categories=("video", "image", "llm")):
    """本集**已入账**的累计花费。"""
    marks = ",".join("?" for _ in categories)
    r = con.execute("SELECT COALESCE(SUM(amount),0) AS a FROM cost_ledger "
                    "WHERE episode_id=? AND category IN (%s)" % marks,
                    (episode_id,) + tuple(categories)).fetchone()
    return float(r["a"] if hasattr(r, "keys") else r[0])


def check(con, episode_id, estimate, what="本次生成", hard=True):
    """返回 (是否放行, 说明)。默认放行，只把数报清楚。

    `hard` 参数保留给调用方；真正的拦截只由 `cap()` 是否为 None 决定。
    """
    c = cap()
    used = spent(con, episode_id)
    projected = used + (estimate or 0.0)
    if c is None:
        return True, ("本集已花 ¥%.2f ｜ %s预估 ¥%.2f ｜ 合计 ¥%.2f"
                      "（未设 DSH_EPISODE_BUDGET，**不拦**，只报数）"
                      % (used, what, estimate or 0.0, projected))
    msg = ("本集上限 ¥%.0f ｜ 已花 ¥%.2f ｜ %s预估 ¥%.2f ｜ 合计 ¥%.2f"
           % (c, used, what, estimate or 0.0, projected))
    if projected > c:
        return False, (msg + " —— **超限 ¥%.2f，已拒绝生成**（你设了 "
                             "DSH_EPISODE_BUDGET；清掉它就不拦）"
                       % (projected - c))
    return True, msg


def report_line(con, episode_id):
    """给报表用的一行摘要。"""
    c, used = cap(), spent(con, episode_id)
    if c is None:
        return "本集已花 ¥%.2f（未设上限，只报数）" % used
    return "预算 ¥%.0f ｜ 已花 ¥%.2f ｜ 余 ¥%.2f" % (c, used, c - used)
