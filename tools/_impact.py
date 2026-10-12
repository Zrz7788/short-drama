# -*- coding: utf-8 -*-
r"""剧本换成 v2 之后，下游资产有多少是"对不上"的。

剧本改了（场景从"化妆间"变"整容室"、动作从口红变油彩、道具从腕带变脚趾牌），
但 entity / entity_ref / shot 都还是 v1 时代的产物。这里逐项算清：
**哪些能留、哪些作废、重跑要花多少钱。**
"""
import sqlite3

c = sqlite3.connect("data/drama.db")
c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731

print("=" * 72)
print("剧本版本")
print("=" * 72)
for r in c.execute("select id, version, is_current, title, word_count from script order by version"):
    print("  v%s  current=%s  %s  %d 字" % (r["version"], r["is_current"], r["title"], r["word_count"]))
cur = c.execute("select id from script where is_current=1").fetchone()["id"]
print("  现行稿 script_id = %s" % cur)

print("\n" + "=" * 72)
print("实体（第 2 阶段产物）—— 与新剧本的匹配情况")
print("=" * 72)
rows = list(c.execute("select id, kind, name, role, domain_fact from entity order by kind, id"))
print("  共 %d 个" % len(rows))
bad = []
for r in rows:
    # 新剧本里还提不提这个名字？
    in_new = q("select count(*) from scene where script_id=? and "
               "(action like ? or dialogue like ?)",
               cur, "%%%s%%" % r["name"], "%%%s%%" % r["name"])
    flag = "" if in_new else "  ← 新剧本里**不再出现**"
    if not in_new:
        bad.append(r["name"])
    print("    [%s/%-9s] %-22s %s%s" % (r["kind"], r["role"], r["name"],
                                        r["domain_fact"] or "(无领域事实)", flag))
print("\n  新剧本里已不出现的实体：%d 个" % len(bad))
for n in bad:
    print("    - %s" % n)

print("\n" + "=" * 72)
print("参考图（第 3/4 阶段产物）")
print("=" * 72)
refs = list(c.execute(
    "select er.view, e.name, e.kind, er.path from entity_ref er "
    "join entity e on e.id=er.entity_id order by e.kind, e.name, er.view"))
print("  共 %d 张" % len(refs))
for r in refs:
    print("    %-12s %-24s %s" % (r["view"], r["name"], (r["path"] or "")[-46:]))

print("\n" + "=" * 72)
print("需要重出的图（估算）")
print("=" * 72)
IMG = {"character": {"main": 2, "secondary": 1, "background": 0},
       "location": {"main": 3, "secondary": 2, "background": 0},
       "prop": {"main": 1, "secondary": 1, "background": 0}}
# 人物留、场景与道具重出
keep = sum(IMG[r["kind"]][r["role"]] for r in rows if r["kind"] == "character")
redo = sum(IMG[r["kind"]][r["role"]] for r in rows if r["kind"] in ("location", "prop"))
print("  人物可留            : %d 张" % keep)
print("  场景+道具要重出      : %d 张 × ¥0.30 = ¥%.2f" % (redo, redo * 0.30))

print("\n" + "=" * 72)
print("分镜与关键帧")
print("=" * 72)
print("  镜头 %d 个（基于旧剧本的 12 镜，场景名已变）" % q("select count(*) from shot"))
print("  有提示词 %d 条" % q("select count(*) from shot where prompt is not null and prompt<>''"))
print("  有关键帧 %d 张（S01 动作不专业、S03 盛伯渊姿势错）"
      % q("select count(*) from shot where keyframe_path is not null"))
print("\n  这些都要基于 v2 剧本重跑：5 阶段分镜 → 6 提示词 → 7 关键帧")
