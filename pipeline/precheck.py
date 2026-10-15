# -*- coding: utf-8 -*-
r"""成片前的**语义物理闸门** —— 表驱动，一条规则对应一个真实踩过的坑。

## 为什么要有这一层（这是本质问题）

流水线原来只有**字段级**校验：尺寸、时长、画幅、外键、成本、连续性声明。
但模型执行的是**自然语言**，它不认识字段。于是所有翻车都发生在
**"字段都合法、连起来物理上不可能"**的地方：

| 类型 | 例子（都是 EP001 真实踩到的） | 为什么字段级校验拦不住 |
|---|---|---|
| 示范/规格错误 | `on_screen_text` 的**写法示例**带「S07 特写：」，模型照抄并**烧进画面** | 字段值本身"有内容"，格式错不算错 |
| 跨字段物理冲突 | 平躺 × 机位平视 × 近景 → **渲染成站着的人** | 三个字段各自都合法 |
| 实体描述缺三件套 | "金属口部塑形器" → 变成**挡住整张嘴的镀铬巨钩** | 描述"有"，但没说**在哪/多大/什么形态** |
| 跨镜状态未冻结 | 同一具遗体：S01/S03/S07 藏蓝中山装、S02/S04/S06 墨绿；S02 眼睛已睁开（剧透 S05 的睁眼反转） | 每镜的服装词各自"合法"，没人比**
| 锁单边 | 静止锁写满、没人写"要变的量" → **近静帧** | 锁字段非空就算填了 |
| 改动无回归 | 改机位修好了"站着"，**同时把眼睛改成了半睁** | 只复验了改动项，没复验该镜其余锁 |

**本质一句话**：我们缺一层"**实体 × 形态 × 状态 × 跨镜同一性**"的语义校验。
这一层不可能靠"更仔细地写提示词"解决——必须变成**可枚举、可复跑的规则表**，
每条规则都记着它是被哪一集哪一镜的哪次亏教出来的。

## 用法

```bash
python pipeline/precheck.py --episode-id 1          # 出片前跑（不调 API、不花钱）
python pipeline/precheck.py --episode-id 1 --json
```

`stage_prompts.py --audit` 是它的前身（只管两条），规则已统一收到这里。
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

# ---------------------------------------------------------------- 词表
ANNOT = re.compile(r"^\s*S\d{1,3}\s*[·:：、]?\s*(大特写|特写|近景|中景|中近景|全景|远景)")
POS_WORDS = ("平躺", "仰卧", "躺卧", "躺在")
POSE_SHOTS = ("近景", "中景", "中近景")
# "载体"词：文字必须长在这些东西上
CARRIER = ("牌", "纸", "卡", "屏", "面板", "墙", "门", "本", "册", "条", "单",
           "证", "表", "签名栏", "显示屏", "计时", "灯箱", "标签", "刻")
# "尺度/形态"词：器械类道具必须交代清楚
SCALE = ("厘米", "毫米", "cm", "mm", "大小", "长度", "直径", "不超过", "约",
         "细", "窄", "薄", "小", "扁", "夹", "环", "扣", "条", "管", "针")
DEVICE = ("塑形器", "器械", "夹具", "支架", "钩", "钳", "针", "管", "刀", "器")
# "变化量"词：锁必须有配对的进程
CHANGE = ("呼吸", "起伏", "眨", "颤", "抖", "移", "渐", "飘", "闪", "跳", "收紧",
          "滑动", "转动", "流", "滴", "落", "升", "降", "明灭", "频闪", "掠过",
          "按", "推", "抽", "写", "划", "走", "转身", "抬", "落")
COLORS = ("红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "灰", "棕",
          "藏蓝", "墨绿", "深绿", "米色", "粉色", "银", "金", "铜", "褐")
# 血腥／肉体受击词（P09）。**只收多字词**——单字"血"会误伤"血色""血统"。
# 出处：红果审核规则 §四（过度呈现血腥暴力／施虐）+ 打戏规范安全红线。
GORE = ("血腥", "血肉", "血迹", "血污", "鲜血", "流血", "出血", "伤口", "创口",
        "内脏", "断肢", "残肢", "骨裂", "骨折", "肌肉震颤", "皮肤凹陷", "面部扭曲",
        "眼球突出", "血肉模糊", "开膛", "剖腹", "肠子", "尸斑", "尸体", "腐尸",
        "施虐", "虐待", "自残", "自尽")


def _g(row, key, default=""):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


# ---------------------------------------------------------------- 规则表
# level: block = 能证明会出错（拦）/ review = 必须人眼看（不拦、但要有人看）
def r_p01_annot(row):
    ost = str(_g(row, "on_screen_text")).strip()
    if ost and ANNOT.match(ost):
        return "画面文字写成了**分镜批注**：%r —— 提示词那句是「逐字照此」，模型会把整串烧进画面" % ost[:40]
    return None


def r_p02_pose(row):
    blob = " ".join(str(_g(row, k)) for k in
                    ("description", "action", "stillness_lock", "positive_locks"))
    flat = any(w in blob for w in POS_WORDS) or ("遗体" in blob and "台" in blob)
    if flat and "平视" in str(_g(row, "camera_angle")) \
            and any(x in str(_g(row, "shot_size")) for x in POSE_SHOTS):
        return "平躺主体 + 机位平视 + %s —— 实测渲染成『站着的人』；改俯视/侧俯视 + 姿态锁" \
            % _g(row, "shot_size")
    return None


def r_p03_carrier(row):
    ost = str(_g(row, "on_screen_text")).strip()
    if ost and not any(w in ost for w in CARRIER):
        return "画面文字没说**长在什么东西上**：%r —— 模型会做成悬空字幕条（实测 S03）" % ost[:30]
    return None


def r_p04_device_scale(row):
    p = str(_g(row, "prompt"))
    hit = [w for w in DEVICE if w in p]
    if hit and not any(w in p for w in SCALE):
        return "提示词里有器械类道具（%s）却**没写尺度/形态** —— 实测会自由长成巨物（S06 镀铬巨钩）" \
            % "、".join(hit[:3])
    return None


def r_p05_lock_needs_change(row):
    lock = str(_g(row, "stillness_lock")).strip()
    act = str(_g(row, "action")).strip()
    if lock and act and not any(w in act for w in CHANGE):
        return "有静止锁、但【动作】里**没有任何持续变化的量** —— 实测会输出近静帧（S06/S07/S14）"
    return None


def r_p06_lips_closed_object(row):
    p = str(_g(row, "prompt"))
    closed = ("唇线闭合" in p or "嘴唇闭合" in p or "双唇紧闭" in p)
    through = ("从唇间" in p or "穿过嘴唇" in p or "从唇缝" in p or "唇间穿出" in p)
    if closed and through:
        return "嘴唇写「闭合」又要物体「从唇间穿出」—— **物理不成立**（实测 S04 一根细针从无缝唇线穿出）"
    return None


def r_p07_hand_fingers(row):
    blob = str(_g(row, "description")) + str(_g(row, "action"))
    p = str(_g(row, "prompt"))
    if ("手" in blob or "手套" in blob) and "特写" in str(_g(row, "shot_size")):
        if not any(w in p for w in ("五指", "分指", "每只手", "手指分明")):
            return "手部特写没写「五指/分指」 —— 实测会生成无指手套/多指（S08 蓝色无指团块）"
    return None


def r_p09_gore_words(row):
    """血腥／肉体受击词检查（**审核风险**，不是画面风险）。

    出处两条：
    ① 红果《动画微短剧（漫剧）内容创作建议》§四「禁止呈现画风观感不适—过度呈现血腥暴力」
       ——"逼真的血腥场景…如血肉模糊特写、详细断肢过程"；"展示极端施虐的场景"。
       全文存档：`knowledge/02-模型与成本/红果内容审核标准（漫剧版全文）.md`
    ② 外部打戏设计规范（cyberay-fight-design）的**安全红线**：不描写肉体受击细节
       （肌肉震颤、皮肤凹陷、面部扭曲），不出现"血、伤口、内脏"；
       要表现命中就改写为**被震退／踉跄、兵刃脱手、衣袍或护甲被斩裂、撞碎身后器物**。

    ⚠️ 只查**多字词**，不查单字"血"（"血色""血统"会误报）；
    命中只是**提醒**（level=review）——要不要改由人定，因为它是审核口径不是硬错误。
    """
    p = str(_g(row, "prompt")) + str(_g(row, "action")) + str(_g(row, "description"))
    hit = [w for w in GORE if w in p]
    if hit:
        return ("提示词含血腥／肉体受击词（%s）—— 命中红果审核 §四『血腥暴力／施虐』；"
                "打戏规范要求改写成**被震退/踉跄、兵刃脱手、衣袍斩裂、撞碎器物**"
                % "、".join(hit[:4]))
    return None


RULES = [
    ("P01", "block", "示范/规格错误", "EP001-S03（¥5.80 重出）", r_p01_annot),
    ("P02", "review", "跨字段物理冲突", "EP001-S02（¥8.55 重出）", r_p02_pose),
    ("P03", "review", "实体描述缺载体", "EP001-S03（文字浮空，未修）", r_p03_carrier),
    ("P04", "review", "实体描述缺尺度形态", "EP001-S06（塑形器→镀铬巨钩）", r_p04_device_scale),
    ("P05", "review", "锁单边（无变化量）", "EP001-S06/S07/S14（近静帧）", r_p05_lock_needs_change),
    ("P06", "block", "跨字段物理冲突", "EP001-S04（针从闭合唇线穿出）", r_p06_lips_closed_object),
    ("P07", "review", "实体描述缺形态", "EP001-S08（无指手套）", r_p07_hand_fingers),
    ("P09", "review", "审核风险（血腥/受击）",
     "红果审核规则§四 + 打戏规范安全红线", r_p09_gore_words),
]


# ---------------------------------------------------------------- 跨镜状态
def cross_shot_states(rows):
    """**跨镜状态未冻结**（类型 T4）：同一场里同一个主体，服装颜色词必须一致。

    实测：同一具遗体 S01/S03/S07 是藏蓝中山装、S02/S04/S06 变墨绿立领——
    每一镜的服装词各自"合法"，**没有任何一层在比它们**。
    这里做最小版本：抽出「【主体·X】」段落里的颜色词集合，同场同主体必须相同。
    """
    by_scene = {}
    for r in rows:
        p = str(_g(r, "prompt"))
        for m in re.finditer(r"【主体·([^】]{1,12})】([^\n]*)", p):
            name, seg = m.group(1), m.group(2)
            cols = tuple(sorted({c for c in COLORS if c in seg}))
            by_scene.setdefault((_g(r, "scene_id"), name), []).append(
                (_g(r, "seq"), cols))
    out = []
    for (scene, name), items in by_scene.items():
        sets = {c for _s, c in items}
        if len(sets) > 1:
            detail = "；".join("S%02d %s" % (s, "/".join(c) or "（无色词）")
                              for s, c in items)
            out.append("场景 %s 的主体「%s」**服装颜色词跨镜不一致**：%s"
                       " —— 实测同一段动作里换过衣服（藏蓝↔墨绿）" % (scene, name, detail))
    return out


def main():
    ap = argparse.ArgumentParser(description="成片前的语义物理闸门（表驱动）")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    ep = con.execute("SELECT * FROM episode WHERE id=?", (args.episode_id,)).fetchone()
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    rows = list(con.execute("SELECT * FROM shot WHERE episode_id=? ORDER BY seq",
                            (args.episode_id,)))
    con.close()

    blocks, reviews = [], []
    for r in rows:
        seq = _g(r, "seq")
        for rid, level, kind, src, fn in RULES:
            msg = fn(r)
            if msg:
                item = {"rule": rid, "level": level, "kind": kind, "source": src,
                        "shot": seq, "msg": msg}
                (blocks if level == "block" else reviews).append(item)
    for msg in cross_shot_states(rows):
        reviews.append({"rule": "P08", "level": "review", "kind": "跨镜状态未冻结",
                        "source": "EP001（服装跳变）", "shot": "-", "msg": msg})

    if args.json:
        print(json.dumps({"blocks": blocks, "reviews": reviews},
                         ensure_ascii=False, indent=2))
        return 1 if blocks else 0

    print("=" * 88)
    print("语义物理闸门 · EP%03d（%d 镜，%d 条规则）"
          % (ep["seq"], len(rows), len(RULES) + 1))
    print("=" * 88)
    if blocks:
        print("\n⛔ 阻断（能证明模型会照字面执行，**别花这个钱**）")
        for b in blocks:
            print("  [%s] S%s %s" % (b["rule"], b["shot"], b["msg"]))
            print("        出处：%s ｜ 类型：%s" % (b["source"], b["kind"]))
    if reviews:
        print("\n👁 必须人眼看（改不改由人定；规则只把候选挑出来）")
        for b in reviews:
            print("  [%s] S%s %s" % (b["rule"], b["shot"], b["msg"]))
            print("        出处：%s ｜ 类型：%s" % (b["source"], b["kind"]))
    if not blocks and not reviews:
        print("\n✅ 七类已知坑都没命中")
    print("\n阻断 %d 处 ／ 待看 %d 处。" % (len(blocks), len(reviews)))
    print("⚠️ 规则表覆盖的是**已经踩过的坑**，不是全部。新坑进来就加一条，")
    print("   每条都记『出处』——规则没有出处就没人敢删、也没人知道它为什么在。")
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
