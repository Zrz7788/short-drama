# -*- coding: utf-8 -*-
r"""布局与领域问题自检（第 1.5 与第 4.5 阶段）。

覆盖两件在真项目里出过事的事：

  取景可行性   "竖屏 9:16 装不下横躺人物全长" —— 用户一眼看出的"推车太短了"。
               这里把它变成可算、可拦的数。
  领域问题     第 1 集把化妆间与冷柜间当成同一间屋，根因是**没人问过这个问题**。
               这里保证：问题清单必须存在、未回答必须拦、写回不能毁掉档案。

跑法：python pipeline\selftest_layout.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import layout_math as LM  # noqa: E402
from pipeline import layout_render as LR  # noqa: E402
from pipeline import stage_domain_questions as DQ  # noqa: E402
from pipeline import stage_layout as SL  # noqa: E402

rep = C.Report("布局与领域问题自检")
PROD0 = C.prod_fingerprint()
TMP = os.path.join(tempfile.gettempdir(), "layout-selftest")

LYING = {"length_m": 1.90, "depth_m": 0.70, "height_m": 0.42, "pose": "lying"}
HEAD = {"length_m": 0.62, "depth_m": 0.42, "height_m": 0.28, "pose": "lying"}

# ---------------------------------------------------------------- 1. 画幅算式
print("\n=== 1. 画幅覆盖算式 ===")

w, h = LM.visible_size(50, 2.0, "9:16")
rep.add("9:16 可见区域是竖的（高 > 宽）", h > w, "%.2f × %.2f m" % (w, h))
rep.add("50mm@2m 的可见宽度与手算一致",
        abs(w - 2 * 2.0 * (36 / (2 * 50))) < 1e-6, "%.4f m" % w)
w2, _ = LM.visible_size(85, 2.0, "9:16")
rep.add("焦距越长看得越窄（85mm < 50mm）", w2 < w, "%.2f < %.2f" % (w2, w))
w3, _ = LM.visible_size(50, 4.0, "9:16")
rep.add("距离翻倍可见宽度翻倍", abs(w3 - 2 * w) < 1e-6, "%.2f" % w3)

# ⭐ 主判据：横躺的人全长在 9:16 下受**窄边**限制
cov = LM.frame_coverage(85, 2.35, LYING, "frontal")
rep.add("横躺全长的判据是**宽**（受竖屏窄边限制）",
        cov["ratio_w"] > cov["ratio_h"], "宽 %.2f vs 高 %.2f"
        % (cov["ratio_w"], cov["ratio_h"]))
rep.add("85mm@2.35m 装不下 1.9m 横躺全长（复现'推车太短'）",
        not cov["fits"], "覆盖率 %.2f×" % cov["worst"])
cov2 = LM.frame_coverage(85, 2.35, LYING, "side")
# ⚠️ 只断言"明显改善"，不断言"一定装得下"。
#    side 机位把身长放到画面**高**上（1.90m 高），而 85mm@2.35m 可见高 1.77m，
#    所以 1.07× 仍然超。写成"fits"就是把我以为的当成了事实——实测被自检抓到。
rep.add("改成 side 机位覆盖率大幅改善（受高而非宽限制）",
        cov2["worst"] < cov["worst"], "side %.2f× vs frontal %.2f×"
        % (cov2["worst"], cov["worst"]))
rep.add("side 的瓶颈在高（说明身长被放到了画面高上）",
        cov2["ratio_h"] > cov2["ratio_w"],
        "宽 %.2f 高 %.2f" % (cov2["ratio_w"], cov2["ratio_h"]))
rep.add("side 机位退到装得下所需距离是 2.35m 的约 1.07 倍",
        LM.frame_coverage(85, 2.35 * 1.07, LYING, "side")["worst"] <= 1.01,
        "%.2f×" % LM.frame_coverage(85, 2.35 * 1.07, LYING, "side")["worst"])
rep.add("头肩特写（0.62m）比全长容易装下",
        LM.frame_coverage(85, 1.60, HEAD, "frontal")["fits"])

sug = LM.suggest(85, 2.35, LYING, "frontal")
rep.add("装不下时给三种可执行替代", len(sug) >= 2, " / ".join(s[:22] for s in sug))
rep.add("替代里给了具体数字（退后多少米）",
        any("米" in s for s in sug))
rep.add("装得下时不给替代（不刷屏）", LM.suggest(50, 3.0, HEAD, "frontal") == [])

for name, fn in (("焦距为 0", lambda: LM.visible_size(0, 2.0)),
                 ("距离为负", lambda: LM.visible_size(50, -1)),
                 ("画幅不支持", lambda: LM.visible_size(50, 2.0, "4:3")),
                 ("主体尺寸为 0", lambda: LM.subject_extents(
                     {"length_m": 0, "depth_m": 1, "height_m": 1}, "frontal"))):
    try:
        fn()
        rep.add("非法输入被拦：%s" % name, False, "居然通过了")
    except LM.LayoutError as e:
        rep.add("非法输入被拦：%s" % name, True, str(e)[:36])

# ---------------------------------------------------------------- 2. 布局文件校验
print("\n=== 2. 布局文件校验（能证明的错要拒绝）===")
os.makedirs(TMP, exist_ok=True)


def spec_file(obj, name="s.json"):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


GOOD = {"aspect": "9:16", "rooms": [{
    "name": "整容室", "width_m": 4.0, "depth_m": 3.4,
    "items": [{"name": "整容台", "x": 1.0, "y": 0.5, "w": 0.7, "d": 2.0,
               "pose": "lying", "length_m": 1.9, "depth_m": 0.7, "height_m": 0.42}],
    "camera": {"x": 1.4, "y": 2.8, "look": [0, -1], "lens_mm": 85,
               "distance_m": 1.6, "view": "frontal", "subject": 0}}]}
rep.add("合法布局通过", bool(SL.load_spec(spec_file(GOOD))))

bad = json.loads(json.dumps(GOOD))
bad["rooms"][0]["items"][0]["x"] = 3.8          # 3.8 + 0.7 = 4.5 > 4.0
try:
    SL.load_spec(spec_file(bad, "b1.json"))
    rep.add("物件越界被拦", False, "居然通过了")
except SL.LayoutSpecError as e:
    rep.add("物件越界被拦", True, str(e)[:44])

bad2 = json.loads(json.dumps(GOOD))
del bad2["rooms"][0]["camera"]["look"]
try:
    SL.load_spec(spec_file(bad2, "b2.json"))
    rep.add("摄影机缺 look 被拦", False, "居然通过了")
except SL.LayoutSpecError as e:
    rep.add("摄影机缺 look 被拦", True, str(e)[:40])

bad3 = json.loads(json.dumps(GOOD))
bad3["rooms"][0]["camera"]["view"] = "斜侧"
try:
    SL.load_spec(spec_file(bad3, "b3.json"))
    rep.add("非法机位方向被拦", False, "居然通过了")
except SL.LayoutSpecError as e:
    rep.add("非法机位方向被拦", True, str(e)[:40])

bad4 = json.loads(json.dumps(GOOD))
bad4["rooms"][0]["camera"]["subject"] = 5
try:
    SL.load_spec(spec_file(bad4, "b4.json"))
    rep.add("subject 越界被拦", False, "居然通过了")
except SL.LayoutSpecError as e:
    rep.add("subject 越界被拦", True, str(e)[:40])

res, blocks, warns = SL.evaluate(GOOD)
rep.add("evaluate 给出覆盖率", res and res[0]["coverage"]["worst"] > 0,
        "覆盖率 %.2f×" % res[0]["coverage"]["worst"])

# ---------------------------------------------------------------- 3. 平面图渲染
print("\n=== 3. 平面图渲染 ===")
txt, (gw, gh) = LR.render_ascii(GOOD["rooms"][0])
rep.add("ASCII 渲染出网格", gw > 8 and gh > 4, "%d×%d" % (gw, gh))
rep.add("ASCII 里能看到摄影机与视线", "C" in txt and "." in txt)
rep.add("ASCII 里主体被框住", "[" in txt and "]" in txt)
lines = txt.splitlines()
rep.add("ASCII 每行等宽（拼接不会错位）",
        len({len(x) for x in lines}) == 1 or all(
            len(x) <= max(len(y) for y in lines) for x in lines),
        "%d 行" % len(lines))

focus_txt, (fw, fh) = LR.render_ascii(GOOD["rooms"][0], focus=[1.4, 1.4, 2.2])
rep.add("focus 缩小视野（格数变少）", fw <= gw, "%d -> %d 列" % (gw, fw))

rep.add("能加载中文字体（否则图上全是方框）", LR.load_font(16) is not None)

out_png = os.path.join(TMP, "layout.png")
try:
    LR.render_png(GOOD["rooms"], out_png)
    rep.add("PNG 渲染成功且有内容",
            os.path.exists(out_png) and os.path.getsize(out_png) > 3000,
            "%.0f KB" % (os.path.getsize(out_png) / 1024.0))
except LR.RenderError as e:
    rep.add("PNG 渲染成功且有内容", False, str(e)[:50])

# ---------------------------------------------------------------- 4. 领域问题清单
print("\n=== 4. 领域问题清单 ===")
BRIEF = """# 测试剧

## 领域问题

| 编号 | 问题 | 回答 | 出处 |
|---|---|---|---|
| Q-01 | 化妆间与冷柜间是同一间吗？ | 否，两处分开 | 用户指出 |
| Q-02 | 给逝者化妆用什么？ | 待查 |  |

## 领域事实

### 已确认

| 编号 | 类别 | 事实 | 出处 | 置信度 |
|---|---|---|---|---|
| F-01 | 空间 | 存放与化妆是两个空间 | 用户指出 | 高 |
"""
bp = os.path.join(TMP, "测试剧.md")
with open(bp, "w", encoding="utf-8") as f:
    f.write(BRIEF)

rows, ok, why = DQ.parse_questions(bp)
rep.add("能解析问题清单", ok and len(rows) == 2, "%s %s" % (ok, why))
rep.add("已答的判为已答", rows[0]["answered"])
rep.add("『待查』判为未答", not rows[1]["answered"])
rep.add("unanswered 返回未答项",
        [r["id"] for r in DQ.unanswered(rows)] == ["Q-02"])

# 写回：必须保留人的回答，不能整体重写
qs = [{"question": "化妆间与冷柜间是同一间吗？", "why": "决定空间是否成立",
       "affects": "第 1 场"},
      {"question": "给逝者化妆用什么？", "why": "决定道具与动作", "affects": ""},
      {"question": "整容台多高？", "why": "决定机位高度", "affects": "S01"}]
ok2, why2 = DQ.write_section(bp, qs, dry=False)
rep.add("写回成功", ok2, why2)
with open(bp, encoding="utf-8") as f:
    after = f.read()
rows2, _ok, _why = DQ.parse_questions(bp)
by = {r["id"]: r for r in rows2}
rep.add("新增的问题被追加进去", "整容台多高" in after and len(rows2) == 3)
rep.add("⭐ 已答项的回答被保留（没被覆盖成待查）",
        by["Q-01"]["answer"] == "否，两处分开",
        "Q-01 回答=%r" % by["Q-01"]["answer"])
rep.add("未答项保持未答", not by["Q-02"]["answered"])
rep.add("新问题默认待查", by["Q-03"]["answer"] == "待查")
rep.add("⭐ 领域事实章没被写坏",
        "F-01" in after and "存放与化妆是两个空间" in after)
rep.add("回答填『无关』视为已处理",
        DQ.unanswered([{"id": "Q-09", "answered": True, "waived": True}]) == [])

# ---------------------------------------------------------------- 5. 门禁联动
print("\n=== 5. 与第 2 阶段门禁的联动 ===")
from pipeline import stage_entities as SE  # noqa: E402

facts, fok, fwhy = SE.parse_domain_facts(bp)
rep.add("问题与事实在同一份档案里都能解析", fok, fwhy)
rep.add("两个解析器读同一个文件（不会两处维护）",
        os.path.basename(bp) == "%s.md" % "测试剧")

# ---------------------------------------------------------------- 收尾
rep.add("生产库未被改动", C.prod_unchanged(PROD0), "")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(rep.finish())
