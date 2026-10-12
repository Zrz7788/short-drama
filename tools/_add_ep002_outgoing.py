# -*- coding: utf-8 -*-
r"""给 EP002 补「跨集状态（outgoing）」小节。

## 为什么补

`tools/continuity.py --from 1 --to 2` 报出：

    ! EP002 没有「跨集状态（outgoing）」小节
      ——该集收尾时没给下一集留核对依据

第 1 集有 4 条 outgoing，第 2 集一条都没有。**链条到 EP002 就断了**：
EP003 开工时没有可核对的依据，又会退化成"靠写剧本的人记得"。
这跟「领域事实只写在纸上、没有门禁」是同一类问题。

## 怎么写（从剧本正文抽，不编）

依 EP002 剧本末尾的 `[连续性]` 标注与正文，实际留下的悬置是：

  1. 死亡确认书仍在沈九围裙口袋里（她当场给周主任看过又收回）
  2. 炉膛空、温度九百度——盛伯渊与三〇〇号整容台去向仍未知
  3. 沈九当着警察说"我没签过"，警方已介入，但**笔录未交代后续**
  4. 周主任说"你爸的墓前天有人扫过，摆了一束新鲜白菊"——**未闭环**
  5. 周主任说"立墓的人不该问这个问题"——**暗示沈九与立墓有关**
  6. 兰花花盆的土是**新换的、湿的**（沈九注意到了但没追问）
  7. 老郑（目击她"自己签字"的人）上礼拜退休回老家、没留电话
  8. 炉膛深处橙光被"什么东西挡了一下"——炉内并非真空

**只写剧本里真有依据的**，不替编剧想象。
"""
import os
import re
import shutil
import sys
import time

P = os.path.join("剧集", "EP002", "剧本.md")
VIS = os.path.join("剧集", "EP002", "视觉设定.md")

SECTION = """## 跨集状态（outgoing）

- 死亡确认书仍在沈九围裙口袋里；她已当着警察的面声明"见证人栏签的是我的名字，可我没签过"。
- 炉膛空、温度九百度：盛伯渊与三〇〇号整容台**去向仍未知**（EP001 起的悬置，本集未解）。
- 周主任称"你爸的墓前天有人去扫过，摆了一束白菊，花是新鲜的"——**未闭环**。
- 周主任称"立墓的人不该问这个问题"——**暗示沈九与立墓有关**，未点破。
- 周主任办公室兰花花盆的**土是新换的、湿的**（沈九看到但未追问）。
- 老郑（称目睹她"自己签字"的人）**上礼拜退休回老家、没留电话**——这条人证线未追。
- 炉膛深处橙光曾被"什么东西挡了一下"又移开——**炉内并非真空**。
- 周主任称"炉子里怎么会有活人，温度面板会显示的"——与空炉膛构成矛盾，未解。
"""

if not os.path.exists(P):
    print("找不到 %s" % P)
    sys.exit(1)

if os.path.exists(VIS):
    t = open(VIS, encoding="utf-8").read()
    if "跨集状态" in t:
        print("%s 已有「跨集状态」小节，跳过" % VIS)
        sys.exit(0)
    bak = VIS + ".bak-before-outgoing-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(VIS, bak)
    print("已备份 -> %s\n" % bak)
    if not t.endswith("\n"):
        t += "\n"
    t += "\n---\n\n" + SECTION
    open(VIS, "w", encoding="utf-8").write(t)
    print("已给 %s 追加「跨集状态（outgoing）」，%d 条\n"
          % (VIS, SECTION.count("\n- ")))
else:
    # 没有视觉设定就新建一个最小文件，避免连续性无处安放
    bak = None
    open(VIS, "w", encoding="utf-8").write(
        "# EP002 视觉设定\n\n> 本文件待补人物/地点/道具设定；此处先记录跨集状态。\n\n"
        "---\n\n" + SECTION)
    print("EP002 原无视觉设定，已新建并写入 outgoing\n")

print("=" * 72)
print("复查")
print("=" * 72)
os.system('"%s" tools\\continuity.py --from 1 --to 2' % sys.executable)
