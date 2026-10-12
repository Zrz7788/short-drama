# -*- coding: utf-8 -*-
r"""剧本升到 v2 后的下游清理与领域事实补齐（**不花钱，只动数据库**）。

## 要解决什么

剧本改了：化妆间→整容室、口红→油彩、腕带→脚趾牌、推车→整容台。
库里还留着 v1 时代的 17 个实体、12 张参考图、12 镜分镜、2 张关键帧。
其中 **11 个实体在新剧本里已不再出现**，而且全部 `domain_fact` 为空
（第 2 阶段的门禁会拦）。

## 做什么

1. 删掉已作废的实体与它们的参考图记录、删掉旧分镜与提示词
2. 给保留的实体补 `domain_fact`（按取证到的领域事实编号）
3. 列出「新剧本需要、但库里没有」的实体 —— 那是第 2 阶段要产出的
4. **不删磁盘上的图**（只删库记录），也不动剧本

⚠️ 会先备份数据库。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"

# 保留的实体 -> 领域事实编号
# 依据 knowledge/03-项目档案/殡仪馆的阿九.md 里已取证的事实表
KEEP = {
    # 人物：剧情设定，不需要领域事实
    "沈九": "story",
    "盛伯渊": "story",
    "主管": "story",
    # 道具 —— 必须回一条已确认事实
    "死亡确认书": "F-22",      # 死亡证明由医师/户籍机关签发
    "金属倒计时牌": "F-16",    # 由 preparation room 作业环境推出（设备类）
    "黄铜签牌": "F-16",        # 同上，属作业区设备/标识
}
# 明确作废（新剧本已不出现，或与取证冲突）
DROP_NOTE = {
    "殡仪馆三号化妆间": "F-01/F-02：存放与整容是分开的空间，且现场没有冷柜",
    "殡仪馆焚化车间": "新剧本里不再出现（并入了炉子与后廊）",
    "殡仪馆后廊": "新剧本里不再出现（场景名改为整容室/后廊，待第 2 阶段重抽）",
    "不锈钢遗体推车": "F-16：作业面是整容台，推车只用于转运",
    "玫瑰色口红": "F-14：唇部用唇油/膏体，不用口红描画",
    "黄色遗体标带": "F-20：主流是脚趾牌（toe tag）",
    "化妆台紧急按钮": "检索无命中，疑似虚构",
    "遗体盖单": "动作改为油彩，盖单不再承担道具职责",
    "焚化炉状态标识条": "新剧本用『炉门边的条子』，待重抽",
    "文件夹签字板": "改称『签字板』并入场景，待重抽",
    "化妆师专用化妆箱": "新剧本用调色板/垃圾桶，待重抽",
}

if not os.path.exists(DB):
    print("找不到 %s" % DB)
    sys.exit(1)
bak = "%s.bak-before-v2-cleanup-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("已备份 -> %s\n" % bak)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731
cur = c.execute("select id from script where is_current=1").fetchone()["id"]

print("=" * 72)
print("1) 删除已作废的实体（含其参考图记录）")
print("=" * 72)
ents = list(c.execute("select id, kind, name from entity"))
dropped, kept = [], []
for e in ents:
    if e["name"] in KEEP:
        kept.append(e)
        continue
    n = q("select count(*) from entity_ref where entity_id=?", e["id"])
    c.execute("delete from entity_ref where entity_id=?", (e["id"],))
    c.execute("delete from image_generation where entity_id=?", (e["id"],))
    c.execute("delete from entity where id=?", (e["id"],))
    dropped.append((e["name"], n, DROP_NOTE.get(e["name"], "新剧本里不再出现")))
for name, n, why in dropped:
    print("  ✗ %-22s 删掉 %d 张图记录   %s" % (name, n, why))
print("\n  删除 %d 个，保留 %d 个" % (len(dropped), len(kept)))

print("\n" + "=" * 72)
print("2) 给保留的实体补 domain_fact")
print("=" * 72)
for e in kept:
    df = KEEP[e["name"]]
    c.execute("update entity set domain_fact=? where id=?", (df, e["id"]))
    print("  ✓ %-22s -> %s" % (e["name"], df))

print("\n" + "=" * 72)
print("3) 删除旧分镜与提示词（基于 v1 剧本）")
print("=" * 72)
n_shot = q("select count(*) from shot")
n_prompt = q("select count(*) from shot where prompt is not null and prompt<>''")
n_kf = q("select count(*) from shot where keyframe_path is not null")
c.execute("delete from shot_entity")
c.execute("delete from generation")
c.execute("delete from shot")
print("  ✗ 删除 %d 个镜头（含 %d 条提示词、%d 张关键帧记录）" % (n_shot, n_prompt, n_kf))
print("    磁盘上的图没删，只是库里不再引用")

# ⚠️ 必须紧接着清掉 cost_ledger 里指向已删镜头的指针，否则外键悬挂。
#    （实测踩过：漏了这步，验收的「外键完整性」失败。花费记录要保留——那是账。）
from pipeline import db_hygiene as H  # noqa: E402
n_fk = H.null_orphan_cost_refs(c, verbose=True)
print("  清掉 %d 条悬挂的花费指针（钱保留，只是不再指向已删镜头）" % n_fk)

c.commit()

print("\n" + "=" * 72)
print("4) 清理后的库状态")
print("=" * 72)
print("  实体        : %d 个" % q("select count(*) from entity"))
print("  参考图记录  : %d 张" % q("select count(*) from entity_ref"))
print("  镜头        : %d 个" % q("select count(*) from shot"))
print("  有领域事实  : %d / %d"
      % (q("select count(*) from entity where domain_fact is not null and domain_fact<>''"),
         q("select count(*) from entity")))

print("\n" + "=" * 72)
print("5) 新剧本需要、但库里还没有的实体（第 2 阶段要产出）")
print("=" * 72)
have = {r["name"] for r in c.execute("select name from entity")}
# 从新剧本正文里找关键名词
text = c.execute("select content from script where id=?", (cur,)).fetchone()["content"]
for name in ("整容室", "整容台", "油彩", "海绵", "塑形器", "唇油", "棉签",
             "记录板", "调色板", "脚趾牌", "纸牌", "倒计时牌", "焚化炉",
             "签字板", "死亡确认书", "黄铜签牌", "白单", "手套", "垃圾桶"):
    mark = "已有" if name in have else "**缺**"
    print("  %-10s %s" % (name, mark))
c.close()
print("\n完成。接着跑第 2 阶段（会调用模型，产出新实体清单）。")
