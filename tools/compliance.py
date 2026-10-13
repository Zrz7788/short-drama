# -*- coding: utf-8 -*-
r"""发布前合规检查：AI 短剧的备案、题材审核与词级违禁词扫描。

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
  3. **词级违禁词扫描**（依《合规词表与台词时长约束.md》四类词典，
     对分镜/视频提示词与剧本正文逐词扫描，命中给出中性词替换方向）
  4. 产出一份**待办清单**，存进项目档案旁，便于交接

## 诚实边界

**我不知道当前具体的备案要求细节**（栏目、时限、所需材料）。
知识库记的只有"须先备案后上线 + 题材三级审核"这一句。
所以本工具**不编造流程**，只把它列成必须人工确认的项，
并明确标出"这条依据的是二手资料、需向官方核实"。

词表同样来自二手资料（桌面技法教程，2026-10 收录），
词级命中是"生成物需要改写"的信号，不是"剧本不能拍"的结论。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PROJECT = "殡仪馆的阿九"

# 词级违禁词表的知识库出处（数据源，词条改动去那里改）
WORDLIST_MD = os.path.join("knowledge", "02-模型与成本",
                           "合规词表与台词时长约束.md")

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

# 词级违禁词词典：从知识库《合规词表与台词时长约束.md》§一装载。
# 为什么放知识库不放代码：词表是**内容数据**，会随平台规则更新；
# 代码里只写装载与匹配逻辑，词的增删改不碰代码。
# 装载失败不阻断本工具（题材/备案检查照跑），只报告词级检查不可用。
WORDLIST = None
WORDLIST_ERROR = None


def load_wordlist():
    """从知识库词表 md 解析四类违禁词条。

    词表格式（§一 各表）：| 子类 | 词条 |
    词条格为顿号/逗号连排；括号内限定语（如"袒胸露背（过度）"）
    取核心词匹配——限定语是给人看的判断边界，匹配按核心词。
    """
    global WORDLIST, WORDLIST_ERROR
    if WORDLIST is not None:
        return WORDLIST
    WORDLIST = {}
    try:
        body = open(WORDLIST_MD, encoding="utf-8").read()
    except OSError as e:
        WORDLIST_ERROR = "词表文件读不到：%s" % e
        return WORDLIST
    # 定位 §一（违禁词替换表）到 §二 之间
    m = re.search(r"^##\s*一、违禁词替换表.*?$(.*?)^##\s*二、", body,
                  re.M | re.S)
    if not m:
        WORDLIST_ERROR = "词表 md 里找不到「## 一、违禁词替换表」章节"
        return WORDLIST
    section = m.group(1)
    # 按小节（### 1. 血腥暴力类…）分组
    for sec in re.split(r"^###\s+", section, flags=re.M):
        if not sec.strip():
            continue
        title = sec.splitlines()[0].strip()
        cat = re.sub(r"[（(].*?$", "", title).strip("—- ").strip()
        words = set()
        for row in re.findall(r"^\|\s*[^|]+\s*\|\s*([^|]+)\|\s*$",
                              sec, re.M):
            # 表分隔行（| --- | --- |）不是词条
            if set(row.strip()) <= set("-— "):
                continue
            # 词条格：顿号/逗号连排
            for w in re.split(r"[、，,]", row):
                w = w.strip()
                # 去掉括号限定语与"（及同类：…）"式注释尾巴（含引号内的）
                w = re.sub(r"[（(][^）)]*[）)]", "", w).strip()
                w = re.sub(r"^(及同类[:：]|含)", "", w).strip()
                # 去掉残留引号与空白
                w = w.strip("“”\"' 　")
                # 表头行与残句过滤：词条必须是 2-8 字的纯词
                if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{2,8}", w):
                    continue
                words.add(w)
        if words:
            WORDLIST[cat] = sorted(words)
    if not WORDLIST:
        WORDLIST_ERROR = "词表 md 解析出 0 条词条（格式可能变了）"
    return WORDLIST


def scan_banned_words():
    """对剧本/分镜/视觉设定正文做词级违禁词扫描。

    返回 {类别: {文件: [命中的词]}}。只扫正文 md，
    词表自身的 md 与 .bak 备份不在扫描范围（它们天然命中）。
    """
    wordlist = load_wordlist()
    if not wordlist:
        return None
    hits = {}
    targets = []
    for root, dirs, fs in os.walk("剧集"):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in fs:
            if f.endswith(".md"):
                targets.append(os.path.join(root, f))
    for t in targets:
        try:
            body = open(t, encoding="utf-8").read()
        except OSError:
            continue
        # 只扫正文：剔掉 [连续性] 等生产标签行，避免拿声明比对声明
        body = re.sub(r"^\[[^\]]+\]\s*[^\n]*$", "", body, flags=re.M)
        for cat, words in wordlist.items():
            got = sorted(w for w in words if w in body)
            if got:
                hits.setdefault(cat, {})[t] = got
    return hits


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
    banned = scan_banned_words()

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

    print("【词级违禁词扫描 —— 命中项须改写后再进生成】")
    print()
    if banned is None:
        print("  -- 词级检查不可用：%s" % (WORDLIST_ERROR or "未知原因"))
        print("     （词表：%s）" % WORDLIST_MD)
    elif not banned:
        print("  ✅ 分镜/视觉设定正文未命中违禁词（词表 %d 类）"
              % len(load_wordlist()))
    else:
        for cat, files in sorted(banned.items()):
            print("  ✗ %s" % cat)
            for f, words in sorted(files.items()):
                print("     %-44s %s" % (f[:44], "、".join(words[:8])))
        print()
        print("     改写方向：词级替换用中性词（如「嘴角流血」→「紧咬下唇，")
        print("     衣领有红痕」）；情节级改写用视觉隐喻（见《漫剧提示词")
        print("     工具箱》§四：暴力→投影/惊鸟，血腥→打翻的红酒，色情→留白）。")
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
                                        for k, v in risks.items()},
                          "banned_hits": (None if banned is None
                                          else {k: {f: w for f, w in v.items()}
                                                for k, v in banned.items()})},
                         ensure_ascii=False, indent=2))
    # 出品前必须人工确认 —— 因此恒以非零退出，提示"还有未确认项"
    return 1


if __name__ == "__main__":
    sys.exit(main())
