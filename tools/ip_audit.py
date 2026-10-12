# -*- coding: utf-8 -*-
r"""侵权风险自查：AI 生成素材的三条红线。

用户提醒"注意侵权"。在**素材刚出、还没进视频**的时点查最便宜。

  ① **肖像权**——生成的脸撞没撞真人？（模型可能复现训练数据里的名人脸）
  ② **商标/版权**——画面里有没有真实品牌、logo、受版权保护的图案？
  ②' **反向注入**——提示词模板里**自己**写了品牌名吗？
  ③ **训练数据来源**——模型方有没有官方合规声明？

## 上一版的两个假阳性（都改掉了）

1. 它把**提示词里的防御性教学文字**也算成"出现真实品牌"——
   那段是在讲"我们以前踩过商标的坑"，里面的品牌名是**教训记录**，不是生成指令。
   已改为只检查**指令区**（去掉以 `>`、`**依据`、`我们原来` 等开头的说明段），
   并**把"模板里出现品牌名"单独列为一类真风险**（见 ②'）。
2. 它用 `pipeline/prompts/*.md` 这个 glob，**漏掉了子目录**，于是报
   "没找到纯净度锁定段"。实际在 `pipeline/prompts/portrait.md`，是能匹配的，
   问题是脚本没有 fallback 提示。
"""
import os
import re
import sqlite3

PX_NOTE = "（未联网；如需查模型方协议请手动访问火山方舟文档）"

BRANDS = ["美团", "饿了么", "大众点评", "抖音", "快手", "微信", "支付宝",
          "淘宝", "天猫", "京东", "拼多多", "顺丰", "菜鸟", "肯德基",
          "麦当劳", "星巴克", "华为", "小米", "苹果", "iPhone", "耐克",
          "阿迪达斯", "可口可乐", "百事", "奔驰", "宝马", "丰田"]
CELEB_HINT = ["神似", "撞脸", "长得像", "酷似", "similar to", "looks like",
              "resembling"]

# 说明/注释区的标志：这些行不是发给模型的指令
NOTE_MARKERS = (">", "**依据", "我们原来", "实测它没生效", "教训", "⚠️")


def strip_notes(text):
    r"""剔掉防御性说明段，只留**真正发给模型的指令**。

    做法：按行扫，遇到说明标志就把该段（连续的非空行）整段丢掉。
    这是保守的——宁可漏判说明里的品牌名，也不把它当成生成指令。
    """
    out = []
    skipping = False
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            skipping = False
            continue
        if any(s.startswith(m) or m in s[:14] for m in NOTE_MARKERS):
            skipping = True
            continue
        if skipping:
            continue
        out.append(s)
    return "\n".join(out)


def main():
    print("=" * 74)
    print("① 提示词**指令区**里有没有真实品牌 / 撞脸表述")
    print("=" * 74)
    con = sqlite3.connect("data/drama.db")
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT id, prompt FROM image_generation "
                            "WHERE prompt IS NOT NULL AND prompt <> ''"))
    print("  有提示词的记录 %d 条" % len(rows))
    hit_b, hit_c = [], []
    for r in rows:
        inst = strip_notes(r["prompt"])
        for b in BRANDS:
            if b in inst:
                hit_b.append((r["id"], b))
        for c in CELEB_HINT:
            if c in inst:
                hit_c.append((r["id"], c))
    if hit_b:
        print("  ⚠️ 指令区出现真实品牌 %d 处：%s" % (len(hit_b), hit_b[:10]))
    else:
        print("  ✅ 指令区**没有**真实品牌")
    if hit_c:
        print("  ⚠️ 指令区有撞脸表述 %d 处：%s" % (len(hit_c), hit_c[:10]))
    else:
        print("  ✅ 指令区**没有**『像某人』类表述（未刻意撞脸）")

    print()
    print("=" * 74)
    print("②' 提示词**模板文件**里有没有写真实品牌（反向注入风险）")
    print("=" * 74)
    found = False
    for root, dirs, fs in os.walk("pipeline"):
        for f in fs:
            if not f.endswith((".md", ".py")):
                continue
            p = os.path.join(root, f)
            try:
                t = open(p, encoding="utf-8").read()
            except OSError:
                continue
            for b in BRANDS:
                for m in re.finditer(b, t):
                    line = t[:m.start()].count("\n") + 1
                    print("  ⚠️ %s:%d 出现「%s」" % (relpath(p), line, b))
                    found = True
                    break
    if not found:
        print("  ✅ 模板与代码里**没有**真实品牌名")

    print()
    print("=" * 74)
    print("③ 纯净度锁定块存在吗（肯定式正向锁定）")
    print("=" * 74)
    hits = []
    for root, dirs, fs in os.walk("pipeline"):
        for f in fs:
            if f.endswith((".md", ".py")):
                p = os.path.join(root, f)
                t = open(p, encoding="utf-8", errors="replace").read()
                if "纯色均质背景" in t or "无印刷、无标识" in t:
                    hits.append(p)
    if hits:
        for h in hits:
            print("  ✅ %s" % relpath(h))
    else:
        print("  ⚠️ 没找到纯净度锁定块——商标会漏进画面")

    print()
    print("=" * 74)
    print("④ 项目有没有影射真人的设定")
    print("=" * 74)
    any_hit = False
    for p in ("knowledge/03-项目档案/殡仪馆的阿九.md",
              "knowledge/03-项目档案/README.md"):
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        for kw in ("真人", "原型", "影射", "肖像", "侵权", "名人", "演员"):
            if kw in t:
                print("  [%s] 提及「%s」" % (os.path.basename(p), kw))
                any_hit = True
    if not any_hit:
        print("  ✅ 档案里**没有**影射真人的设定（角色为原创）")

    print()
    print("=" * 74)
    print("⑤ 模型方合规声明" + PX_NOTE)
    print("=" * 74)
    print("  用的是火山方舟 doubao-seedream-5-0-pro-260628。")
    print("  **须自行复核**：该类模型的服务条款通常要求——")
    print("    · 不得用生成内容冒充真人、不得用于诽谤或虚假信息")
    print("    · 生成内容的权利义务归属与商业使用范围（各平台不同）")
    print("    · 是否自建/共享训练数据（影响输出撞脸概率）")

    print()
    print("=" * 74)
    print("⑥ 已落盘人物图（**像不像真人只能人眼看**）")
    print("=" * 74)
    base = "D:/dsh-film/assets/refs/character"
    if os.path.isdir(base):
        for f in sorted(os.listdir(base)):
            if f.split("_")[0] in ("沈九", "盛伯渊", "主管", "中年男主管"):
                print("  %-30s %8d B" % (f, os.path.getsize(os.path.join(base, f))))
    print()
    print("  **请自己看一眼**这 5 张脸：有没有眼熟、像某位演员或公众人物？")
    print("  若有，改面孔描述重出（¥0.30/张）。现在改几块钱，进了视频是几十倍。")


def relpath(p):
    return os.path.relpath(p)


if __name__ == "__main__":
    main()
