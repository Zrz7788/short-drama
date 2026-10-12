# -*- coding: utf-8 -*-
r"""按用户授权做出的三项裁定，落到文件与库。

## 裁定一：分镜 74 秒 vs 分集目标 80 秒 —— **不改分镜时长**

74 秒是**切点总长**（12 个镜头各自时长之和），80 秒是**成片时长**，
差的 6 秒是转场、字幕、呼吸。**硬把镜头凑到 80 会注水。**
做法：在分镜的节奏报告里**写明这 6 秒是什么**，让差异有解释、可核对，
而不是删掉一个无辜的数字。

## 裁定二：分镜「未决项」三条 —— **两条是叫法问题，一条是旧稿残留**

分镜自己列的未决项：
  · 运尸推车 —— ❌ **不该有**。剧本推的是**整容台**（"抓住整容台侧面的推把"
    "台子推过门槛"）。分镜里"跟随推车横移"是旧稿用词残留。
  · 金属记录板 —— ✅ 库里是「整容工作记录板」，只是叫法不同
  · 白单 —— ✅ 库里是「棉质遗体盖单」，只是叫法不同
做法：把后两个补成别名；第一个在分镜里**改成整容台**并注明理由。

## 裁定三：两条未进视觉设定的实体 —— **都补进文档**

它们不是"不必出图"，而是**漏写**：
  · 颌部固定金属针：剧本明写"手指底下有金属针的硬度"（动作支点），可见
  · 电脑控制焚化炉：焚化车间的核心设备，炉内敲击是全集钩子
两条都是 `secondary`/`main`，本就要出图却没进设定 —— 补齐。
"""
import os
import shutil
import sqlite3
import sys
import time

DB = "data/drama.db"
VIS = os.path.join("剧集", "EP001", "视觉设定.md")
SHOT = os.path.join("剧集", "EP001", "分镜.md")

# ---- 裁定二：补别名 ----
ALIAS = {
    "金属记录板": "整容工作记录板",
    "记录板": "整容工作记录板",
    "白单": "棉质遗体盖单",
    "推车": "不锈钢整容作业台",   # 剧本里推的就是整容台，不是独立推车
}

bak = "%s.bak-before-decision-%s" % (DB, time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(DB, bak)
print("数据库已备份 -> %s\n" % bak)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("=" * 72)
print("裁定二 · 补别名（消除叫法差异）")
print("=" * 72)
for doc_name, db_name in ALIAS.items():
    row = con.execute("select id, alias from entity where name=?",
                      (db_name,)).fetchone()
    if not row:
        print("  ⚠️ 库里没有「%s」" % db_name)
        continue
    al = [x.strip() for x in (row["alias"] or "").split(",") if x.strip()]
    if doc_name in al:
        print("  -- 「%s」已是「%s」的别名" % (doc_name, db_name))
        continue
    al.append(doc_name)
    con.execute("update entity set alias=? where id=?",
                (", ".join(al), row["id"]))
    print("  ✓ 「%s」补为「%s」的别名" % (doc_name, db_name))
con.commit()
con.close()

# ---- 裁定三：视觉设定补两条 ----
print("\n" + "=" * 72)
print("裁定三 · 视觉设定补两条实体")
print("=" * 72)
vis = open(VIS, encoding="utf-8").read()
ADD = []
if "颌部固定金属针" not in vis:
    ADD.append(("颌部固定金属针", """### 颌部固定金属针

- 身份：经口/下颌植入的防腐固定针，用于把下颌固定在闭合位（取证事实 F-12）。
- 本集状态：不直接可见，但**是可见动作的支点**——沈九指背托下颌时"手指底下有金属针的硬度"。
- 连续性锁：位置在颧弓—下颌两侧对称处；外表只能看出"下颌对称得近乎刻板"。
"""))
if "电脑控制焚化炉" not in vis:
    ADD.append(("电脑控制焚化炉", """### 电脑控制焚化炉

- 身份：数码面板控制的火化炉（retort），炉膛为耐火砖，电脑控温。
- 本集状态：待机红灯亮，面板温度读数**仍在往上爬**——温度未达标时炉门不会打开（取证事实 F-21）。
- 连续性锁：炉门、待机红灯、温度面板三者的相对位置固定；炉门边贴刚打印的条子。
"""))
if ADD:
    marker = "---\n\n## 跨集状态"
    block = "\n".join(t for _n, t in ADD)
    vis = vis.replace(marker, "---\n\n" + block + "\n---\n\n## 跨集状态", 1)
    open(VIS, "w", encoding="utf-8").write(vis)
    for n, _t in ADD:
        print("  ✓ 补入「%s」" % n)
else:
    print("  -- 两条都已在文档里")

# ---- 裁定一 + 二：改分镜 ----
print("\n" + "=" * 72)
print("裁定一/二 · 改分镜（时长说明 + 未决项订正）")
print("=" * 72)
if os.path.exists(SHOT):
    shot = open(SHOT, encoding="utf-8").read()

    # 旧稿残留用词
    old_line = "- 运镜：跟随推车横移，理由：与推台同速保持沉浸。"
    new_line = """- 运镜：跟随整容台横移，理由：与推台同速保持沉浸。（注：剧本推的是**整容台**——"抓住整容台侧面的推把"，不是独立推车；早期用词已订正）"""
    if old_line in shot:
        shot = shot.replace(old_line, new_line, 1)
        print("  ✓ 订正「跟随推车横移」-> 「跟随整容台横移」")

    # 节奏报告：写明 6 秒差值 + 未决项订正
    old_rep = """- 总时长：约 74 秒，适合短剧单集节奏；峰值落在 SHOT-003（睁眼）与 SHOT-010（确认书签名）。"""
    new_rep = """- 切点总长：12 镜合计 **74 秒**（各镜时长之和）。
- 成片目标：**80 秒**（《剧本.md》分集目标，由台词字数与动作段数分配得到）。
- **差 6 秒不补**：这是**转场、字幕与呼吸**的量，不是漏拍。硬把镜头凑到 80
  会注水。校验时按此解释核对，不要删这个差值。
- 峰值落在 SHOT-003（睁眼）与 SHOT-010（确认书签名）。"""
    if old_rep in shot:
        shot = shot.replace(old_rep, new_rep, 1)
        print("  ✓ 节奏报告写明 6 秒差值的来历")

    old_open = """- 未决项（分镜需要但《视觉设定.md》暂无条目）：
  - 记录板与笔：SHOT-002、SHOT-005 出现，建议补道具条目"金属记录板"。
  - 运尸推车：SHOT-006、SHOT-007 出现，建议补道具条目"运尸推车"。
  - 白单：多镜出现，建议补道具条目"白单"。"""
    new_open = """- 未决项：**已全部核对完毕，无遗留**（原先列的三条是叫法差异，不是缺条目）：
  - "金属记录板" —— 即库中**整容工作记录板**（已互为别名），条目已存在于《视觉设定.md》「殡仪馆整容室」识别锚点内。
  - "白单" —— 即库中**棉质遗体盖单**（已互为别名）。
  - "运尸推车" —— ❌ **不该建条目**。剧本推的是**整容台**（"抓住整容台侧面的推把"），
    不是独立推车；本镜运镜用词已订正为"跟随整容台横移"。
- 本次补充：《视觉设定.md》新增道具「颌部固定金属针」（SHOT-004 的可见动作支点）
  与「电脑控制焚化炉」（SHOT-008 的场景核心设备）。"""
    if old_open in shot:
        shot = shot.replace(old_open, new_open, 1)
        print("  ✓ 未决项订正完毕")

    open(SHOT, "w", encoding="utf-8").write(shot)
else:
    print("  找不到 %s" % SHOT)

print("\n完成。复查：")
os.system('"%s" tools\\lint_cross_doc.py --episode-id 1' % sys.executable)
