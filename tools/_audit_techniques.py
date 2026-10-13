# -*- coding: utf-8 -*-
r"""盘点：《实战技法合集》第六节列的 24 项改动，**哪些真落地了**。

## 为什么要盘

用户指出桌面 `1` 里的技法文档有用。这些文档确实已经抄进
`knowledge/01-制作方法/实战技法合集.md`，但**"抄进知识库"不等于"落地到管线"**。
而用户今天问的三间房不像一栋楼、要不要出全图——**答案就在其中一条**：
「场景资产是"一张正视图" → 应改：子空间先行，再扩图到大场景」。
那条**列在表里但没实现**。

所以逐条核实：**在代码里找得到吗？** 找不到的就是欠账。

判据：在 `pipeline/` 与 `tools/` 里搜关键实现词。**只认代码，不认文档里写过。**
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# (改动项, 判据关键词列表——任一命中算落地, 说明)
ITEMS = [
    ("人物出场设计（8 种）", ["出场", "entrance"], "分镜要求里加入场设计"),
    ("同框 ≤4 人硬约束", ["同框", "max_characters", "人数"], "分镜门禁"),
    ("正反打要两张站位图", ["正反打", "overshoulder", "反打"], "站位图产物"),
    ("站位图产物类型", ["blocking"], "已有 ✅"),
    ("脸部视图单独且高清", ["headshot"], "已有 ✅"),
    ("限制传入的场景图数量", ["参考图最多", "14", "n_references"], "ark 侧有 14 张上限"),
    ("台词加情绪标注", ["情绪", "说话动词", "标点"], "剧本/分镜侧"),
    ("单镜时长倾向短", ["duration", "时长"], "默认 8 秒"),
    ("同性别服装颜色区分", ["颜色必须", "服装颜色"], "提示词约束"),
    ("禁用三视图", ["三视图"], "已有结论 ✅"),
    ("不写精确时间码", ["时间码", "0-2s"], "视频提示词侧"),
    ("9:16 vs 16:9 裁剪对比", ["16:9", "裁剪"], "出图/出视频侧"),
    ("每段参考图 ≤4-5 个", ["≤4", "4-5", "最多"], "提示词侧"),
    ("站位图/俯视图/线稿动线图三类", ["scene_top", "线稿", "动线"], "场景产物"),
    ("子空间先行，再扩图", ["子空间", "扩图", "outpaint"], "**今天缺的就是这条**"),
    ("前景+后景描写（70/30）", ["前景", "后景", "70/30", "70%"], "提示词侧"),
    ("同场景不同时段光影版本", ["时段", "黄昏", "清晨"], "场景产物"),
    ("避免日夜混光检查", ["混光"], "场景门禁"),
    ("预算优先砸光影与画面层次", ["情绪价值", "光影优先"], "成本策略"),
    ("补镜头语言字段", ["机位角度", "焦段", "景深", "色温"], "分镜表字段"),
    ("镜头语法判据（近景≥68% 等）", ["近景", "68%", "3-15-45"], "分镜门禁"),
    ("合规检查（先备案后上线）", ["备案", "三级审核"], "发布前检查"),
    ("灰膜动作复刻", ["灰膜", "动作复刻"], "可选技法"),
    ("显式空间表达（站位图/3D预演）", ["站位图", "3D", "预演"], "空间调度"),
]

# 搜代码（不含 knowledge/，那是文档）
#
# ⚠️ **必须排除本脚本自己。** 踩过：本文件在 tools/ 下，于是被当成"代码证据"，
#    而**判据关键词就写在本文件里**（下面 ITEMS 的 kws），当然 100% 命中，
#    报出"落地 24 项，欠账 0 项"这种荒谬结论。
#    这和跨集连续性那次的错误同源——**拿声明比对声明**。
#    同类教训累计三次：为剧本去视觉设定找登记句、为提示词去模板找品牌名、
#    以及这次。**审计工具的判据文本绝不能被算作证据。**
SELF = {os.path.basename(__file__), "_test_prompt_review.py",
        "_pre_ref_audit.py", "_fix_ref_scope.py", "_clean_stylenote.py",
        "_fix_ip_terms.py", "_fix_guanzhu.py", "_check_10repos.py",
        "_check_5plugins.py", "_check_5plugins.py", "_audit_techniques.py"}
SRC = []
for base in ("pipeline", "tools"):
    for cur, dirs, fs in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "prompts")]
        for f in fs:
            if f.endswith((".py", ".mjs", ".js")) and f not in SELF:
                SRC.append(os.path.join(cur, f))
BLANK = "\n".join(
    open(p, encoding="utf-8", errors="replace").read() for p in SRC)
print("扫描代码文件 %d 个，共 %d KB\n" % (len(SRC), len(BLANK) // 1024))

print("=" * 74)
print("落地盘点（只认代码，不认文档）")
print("=" * 74)
done, todo = [], []
for name, kws, note in ITEMS:
    hit = [k for k in kws if k in BLANK]
    if hit:
        done.append((name, note, hit))
        print("  ✅ %-30s %s" % (name, note))
    else:
        todo.append((name, note))
        print("  ❌ %-30s %s" % (name, note))

print()
print("=" * 74)
print("欠账（文档列了、代码里没有）")
print("=" * 74)
for name, note in todo:
    print("  · %-30s %s" % (name, note))
print()
print("  落地 %d 项，欠账 %d 项" % (len(done), len(todo)))
