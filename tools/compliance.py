# -*- coding: utf-8 -*-
r"""发布前合规检查：AI 短剧的备案与题材审核。

## 为什么是独立一关（而不是塞进别的检查里）

依《拾音人范本与工艺规范》§7 记下的技法资料：
**AI 短剧须先备案后上线（2026-04-01 起），题材三级审核。**

这是**法律风险**，不是审美问题：
  · 备案没做 -> 上线即违规，可能被下架、罚款
  · 题材没审 -> 拍到禁止或需特批的题材，整个项目可能要重做

**所以它必须是一道能拦住"发布"的关，而不是一条备注。**

## 本工具做什么

它**不替你备案**（那要人去官方渠道办）。它做的是：
  1. **在发布前强制提醒**这件事还没做，并给出**要填什么**
  2. 扫剧本与档案里的**高风险题材信号**，提示需要三级审核
  3. 产出一份**待办清单**，存进项目档案旁，便于交接

## 诚实边界

**我不知道当前具体的备案要求细节**（栏目、时限、所需材料）。
知识库记的只有"须先备案后上线 + 题材三级审核"这一句。
所以本工具**不编造流程**，只把它列成必须人工确认的项，
并明确标出"这条依据的是二手资料、需向官方核实"。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PROJECT = "殡仪馆的阿九"

# 高风险题材信号：命中即提示需要三级审核／可能不可拍
RISK = {
    "涉政/涉军": ("政府", "军队", "警察", "公安", "官员", "领导", "纪委",
                  "上访", "游行", "示威"),
    "涉宗教": ("寺庙", "和尚", "道士", "教堂", "牧师", "经书", "法事",
               "驱邪", "符咒"),
    "涉迷信": ("鬼", "魂", "转世", "托梦", "阴阳", "算命", "看相", "风水"),
    "血腥/尸体": ("尸", "遗体", "尸体", "血", "内脏", "腐", "骨灰",
                  "焚化", "火化", "整容", "防腐"),
    "自杀/自残": ("自杀", "割腕", "跳楼", "上吊", "自残"),
    "犯罪细节": ("伪造", "造假", "行贿", "受贿", "走私", "贩毒", "绑架",
                 "杀人", "灭口"),
    "未成年": ("未成年", "童工", "学生", "校园"),
    "真实性争议": ("真实事件", "真人真事", "改编自", "原型", "实名"),
}

# 发布前必须人工确认的项（**不编造流程，只列待确认**）
CHECKLIST = [
    ("备案", "AI 短剧备案已办理（2026-04-01 起须先备案后上线）",
     "**须向官方渠道核实**：受理单位、所需材料、时限。"
     "知识库只记了「须先备案后上线」这一句，来自二手资料。"),
    ("题材", "题材已过三级审核（自审 / 平台 / 主管部门）",
     "三级的具体划分同样需向官方核实。"),
    ("权利", "剧本为原创；素材不含未授权商标、肖像、音乐",
     "本项目已有 tools/ip_audit.py 覆盖生成素材部分；"
     "配乐与字体需另外确认。"),
    ("标注", "AI 生成内容已按规定标注",
     "各地要求不同，需核实本地上线平台的具体规则。"),
    ("版权", "成片权利归属与授权链清晰（含甲方/平台合同）",
     "涉及商务，非技术项。"),
]


def scan_risks():
    """扫剧本与项目档案里的高风险题材信号。"""
    hits = {}
    targets = []
    for root, dirs, fs in os.walk("剧集"):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in fs:
            if f.endswith(".md"):
                targets.append(os.path.join(root, f))
    p = os.path.join("knowledge", "03-项目档案", "%s.md" % PROJECT)
    if os.path.exists(p):
        targets.append(p)
    for t in targets:
        try:
            body = open(t, encoding="utf-8").read()
        except OSError:
            continue
        for cat, words in RISK.items():
            got = [w for w in words if w in body]
            if got:
                hits.setdefault(cat, {})[t] = got
    return hits


def main():
    ap = argparse.ArgumentParser(description="发布前合规检查")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    risks = scan_risks()

    print("=" * 74)
    print("发布前合规检查 · %s" % PROJECT)
    print("=" * 74)
    print()
    print("【必办事项 —— 未确认则不应上线】")
    print()
    for i, (cat, item, note) in enumerate(CHECKLIST, 1):
        print("  %d. [%s] %s" % (i, cat, item))
        print("       %s" % note)
    print()

    print("【题材风险信号 —— 命中项需过三级审核】")
    print()
    if not risks:
        print("  未命中高风险信号")
    else:
        for cat, files in sorted(risks.items()):
            print("  ⚠️ %s" % cat)
            for f, words in sorted(files.items()):
                print("     %-44s %s" % (f[:44], "、".join(words[:6])))
    print()

    print("【本项目实际情况（供判断，不是结论）】")
    print("  · 题材：殡仪馆遗体整容师 + 悬疑。殡葬与遗体是**明确命中**的敏感面，")
    print("    应当准备题材说明。")
    print("  · 内容：无涉政涉军主线；有「伪造死亡证明」情节，属犯罪描写，")
    print("    但为揭露性叙事、非教程性描写。")
    print("  · 角色：档案注明**角色为原创、不影射真人**。")
    print()
    print("=" * 74)
    print("  ⚠️ 本工具的合规依据来自**二手资料**（《拾音人》技法文档），")
    print("     **具体的备案单位、材料、时限须向官方渠道核实**。")
    print("     工具不编造流程，只保证这件事在发布前被挡一次。")
    print("=" * 74)

    if args.json:
        print(json.dumps({"checklist": CHECKLIST,
                          "risk_hits": {k: {f: w for f, w in v.items()}
                                        for k, v in risks.items()}},
                         ensure_ascii=False, indent=2))
    # 出品前必须人工确认 —— 因此恒以非零退出，提示"还有未确认项"
    return 1


if __name__ == "__main__":
    sys.exit(main())
