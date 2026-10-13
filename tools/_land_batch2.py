# -*- coding: utf-8 -*-
r"""落地第二批 4 项欠账（⑥同性别服装颜色区分 ⑦灰膜 ⑧合规 ⑨光影优先）。

## 逐项

### ⑥ 同性别服装颜色区分（依《4、导演台站位法》）
在 `prompt_review` 新增 C9：**同性别角色若共享两个以上"服装颜色词"就提醒**。
实测发现：沈九与主管共享 黑/白/灰/棕，且两人都写「深灰」——
一个工装一个西装，但**同一场里可能分不清**。
实体表**没有性别字段**，所以用描述里的"男性/女性"判性别。

### ⑦ 灰膜动作复刻（依《10、灰膜动作复刻》）
适用：提示词写不清的复杂动作、特技、武打。本项目第 1 集没有这类镜头，
所以**登记为可用技法**（写进知识库与工具清单），不实现流水线支持。
原表标的是"可选"，登记即为落地。

### ⑧ 合规检查：AI 短剧须先备案后上线
依《拾音人 §7》：2026-04-01 起 AI 短剧须**先备案后上线**，题材三级审核。
这是**法律风险**，不是审美问题，所以做成独立检查工具。

### ⑨ 预算优先砸光影与画面层次（依《6、女频氛围感》）
原话的价值排序：预算吃紧时优先砸**光影与画面层次**，
而非无限追求场景精确度。写进成本模型文档。
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# ---------------------------------------------------------------- ⑥
print("=" * 74)
print("⑥ 同性别服装颜色区分（加进 prompt_review）")
print("=" * 74)
PR = os.path.join(ROOT, "tools", "prompt_review.py")
t = open(PR, encoding="utf-8").read()
if "def check_costume_colors" not in t:
    add = '''

# ---------------------------------------------------------------- 同性别配色
#
# 依《4、导演台站位法》：**同性别角色的服装颜色必须明显区分**。
# 依据是实测：两个同性别角色穿相近色时，模型会把他们的服装**互相串**。
#
# 实体表没有性别字段，所以从描述里的"男性/女性"判。
COSTUME_COLORS = ("藏青", "深灰", "浅灰", "深蓝", "藏蓝", "黑", "白", "米白",
                  "卡其", "棕", "褐", "墨绿", "酒红", "藏青", "灰")


def _gender(desc):
    d = desc or ""
    if "女性" in d or "女" in d[:14]:
        return "女"
    if "男性" in d or "男" in d[:14]:
        return "男"
    return "?"


def _costume_colors(desc):
    """取描述里的服装颜色词（去重，保留最长匹配避免"深灰"与"灰"重复计）。"""
    d = desc or ""
    hits = set()
    for c in COSTUME_COLORS:
        if c in d:
            hits.add(c)
    # 长词优先：有"深灰"就不必再算"灰"
    out = set()
    for c in sorted(hits, key=len, reverse=True):
        if not any(c != o and c in o for o in out):
            out.add(c)
    return out


def check_costume_colors(ents):
    """同性别角色共享多个服装颜色词 -> 提醒。"""
    warns = []
    chars = [e for e in ents if e["kind"] == "character"]
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            a, b = chars[i], chars[j]
            if _gender(a["description"]) != _gender(b["description"]):
                continue
            if _gender(a["description"]) == "?":
                continue
            ca = _costume_colors(a["description"])
            cb = _costume_colors(b["description"])
            shared = ca & cb
            if len(shared) >= 2:
                warns.append(
                    "同性别角色「%s」与「%s」的服装颜色高度重合（共享 %s）——"
                    "**同性别角色的服装颜色必须明显区分**，否则模型会把两人的"
                    "服装互相串。建议把其中一位换成另一色系。"
                    % (a["name"], b["name"], "、".join(sorted(shared))))
    return warns
'''
    # 插到 check_arch_consistency 之前
    anchor = "\ndef check_arch_consistency(ents):"
    if anchor in t:
        t = t.replace(anchor, add + anchor, 1)
    # 在 main 里调用
    anchor2 = "    warns += check_arch_consistency([dict(e) for e in ents])"
    if anchor2 in t and "check_costume_colors" not in t.split(anchor2)[-1]:
        t = t.replace(anchor2, anchor2
                      + "\n    warns += check_costume_colors([dict(e) for e in ents])", 1)
    open(PR, "w", encoding="utf-8").write(t)
    print("  ✓ 已加 C9 同性别配色检查")
else:
    print("  -- 已加过")

# ---------------------------------------------------------------- ⑦⑧⑨ 写文档
print()
print("=" * 74)
print("⑦⑧⑨ 写进知识库")
print("=" * 74)
K = os.path.join(ROOT, "knowledge", "01-制作方法", "实战技法合集.md")
kt = open(K, encoding="utf-8").read()
if "### 落地登记（第二批）" not in kt:
    kt += """

---

## 落地登记（第二批）

第六节那张"应该改成"的表，逐项在代码里核实后的落地结果。
判据：**只认代码，不认文档里写过**。

| 项 | 状态 | 落在哪 |
|---|---|---|
| 同性别服装颜色区分 | ✅ | `prompt_review` C9（同性别角色共享 ≥2 个颜色词即提醒） |
| 不写精确时间码 | ✅ | `stage_prompts` 从未把 `duration_s` 放进模板；时长只用于排片与预算 |
| 灰膜动作复刻 | ✅ 登记 | 本集无复杂动作镜头，**登记为可用技法**，未实现流水线支持 |
| 合规检查（先备案后上线） | ✅ | `tools/compliance.py` |
| 预算优先砸光影与画面层次 | ✅ | `knowledge/02-模型与成本/单集成本模型.md` |

### 灰膜动作复刻的登记内容（备将来）

**用途**：提示词写不清的复杂动作、酷炫特技、武打招式——复刻原视频动作。
**原理**：把原视频转灰膜，**保住元素空间关系、弱化五官与场景细节**；
复刻灰膜能保住动作轨迹与运镜，又降低融合原视频细节的概率。
**已知风险（实测）**：
  · 会把原视频元素融进来（"原视频发饰更高更华丽，非常容易复刻到新视频上"）
  · 性别替换不彻底（"用女角色替换男角色，部分动作胳膊肌肉过于明显"）
**必须写进提示词的约束**：
```
仅参考动作 / 参考动作视频的人物动作、运动轨迹、分镜设计
角色五官、发型发饰、服装等严格使用参考图，禁止与参考视频人物融合
人物动作严格遵循动作轨迹，不要重新设计、跳过或添加动作
```
**关键经验**：AI 无法理解的动作轨迹（如抛剑）必须在提示词里**写清动作过程**。
"""
    open(K, "w", encoding="utf-8").write(kt)
    print("  ✓ 已写入《实战技法合集》落地登记（含灰膜登记）")
else:
    print("  -- 已写过")

# ⑨ 成本模型
C = os.path.join(ROOT, "knowledge", "02-模型与成本", "单集成本模型.md")
if os.path.exists(C):
    ct = open(C, encoding="utf-8").read()
    if "光影优先" not in ct:
        ct += """

---

## ⭐ 预算吃紧时的价值排序（依《6、女频氛围感》）

**优先砸光影与画面层次，而不是无限追求场景精确度。**

原话的价值排序（竖屏短剧）：**光影与画面层次 > 场景精确度 > 道具细节**。
理由：观众对"氛围对不对"极敏感，对"这个柜子是不是那个牌子"几乎无感；
而光影一旦不对，**每一镜都要返工**。

**本项目据此的成本纪律**：
- 出图阶段：**人物（脸与妆造）** 与 **场景的光影基调** 不可省；
  道具按 `main/secondary` 分级出，`background` 级不出图（只在连续性记录里留名）。
- 视频阶段：先出 **Draft 档**验证动作与运镜，确认后才出正式档。
- 宁可少做几个场景，也不要让光影不统一。
"""
        open(C, "w", encoding="utf-8").write(ct)
        print("  ✓ 已写入成本模型「光影优先」")
    else:
        print("  -- 成本模型已写过")
else:
    print("  ⚠️ 找不到 %s" % C)
