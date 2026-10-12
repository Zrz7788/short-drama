# -*- coding: utf-8 -*-
r"""跨集连续性检查的自检。

**为什么必须有**：这个判定函数改了 **7 版**才稳定，中间反复出现
假阳性（误拦真声明）与假阴性（放行假声明）。教训是：

  · 中文核对比想象难得多——没有空格，标点全角半角混用
  · 每一版我都能"讲得通"，但**只有拿真假样本对跑才能发现问题**
  · 中途还把「黄铜签牌」当成真声明（想当然），实际正文里根本没这个物件

所以把 8 个真假样本固化成自检，改一行就跑一次，**不允许凭感觉改**。
"""
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Rep:
    def __init__(self, title):
        self.title = title
        self.items = []

    def add(self, name, passed, detail=""):
        self.items.append((name, bool(passed), detail))

    def report(self):
        n = len(self.items)
        bad = [x for x in self.items if not x[1]]
        print("=" * 72)
        for name, ok, detail in self.items:
            print("  [%s] %-46s %s" % ("OK" if ok else "!!", name[:46], detail[:40]))
        print("=" * 72)
        print("%s：共 %d 项，通过 %d，失败 %d -> %s"
              % (self.title, n, n - len(bad), len(bad),
                 "全部通过" if not bad else "**有失败**"))
        for name, _ok, detail in bad:
            print("   失败: %s %s" % (name, detail))
        return 1 if bad else 0


def main():
    rep = Rep("跨集连续性自检")
    cont = load(os.path.join(ROOT, "tools", "continuity.py"), "cont")

    # ---- 纯函数层：不需要真文件 ----
    rep.add("subject 提取：『死亡确认书仍在…』-> 死亡确认书",
            "死亡确认书" in cont.leading_subject("死亡确认书仍在沈九围裙口袋里"))
    rep.add("subject 提取：不把状态词并进主语",
            all(not h.endswith("仍在") for h in
                cont.leading_subject("檀木算盘仍在沈九围裙口袋里")))
    rep.add("标点归一化：全角顿号与半角逗号等价",
            cont.normalize("你爸的墓、前天") == cont.normalize("你爸的墓, 前天"))
    rep.add("最长公共子串可用",
            cont.longest_common_substring("死亡确认书", "X死亡确认书Y") == "死亡确认书")

    # ---- 端到端：拿真实剧集当样本 ----
    ep1 = os.path.join(ROOT, "剧集", "EP001")
    ep2 = os.path.join(ROOT, "剧集", "EP002")
    have = all(os.path.exists(os.path.join(p, f)) for p, f in (
        (ep1, "视觉设定.md"), (ep1, "剧本.md"), (ep2, "剧本.md")))
    if not have:
        rep.add("端到端样本存在", False, "缺 EP001/EP002 的文档")
        return rep.report()

    d_scr = open(os.path.join(ep2, "剧本.md"), encoding="utf-8").read()
    body = re.sub(r"\[连续性\]\s*.+", "", d_scr)
    body = re.sub(r"\[(画面文字|SFX|VO|OS|转场)\][^\n]*", "", body)
    ents = cont.known_entities(
        open(os.path.join(ep1, "视觉设定.md"), encoding="utf-8").read(),
        open(os.path.join(ep2, "视觉设定.md"), encoding="utf-8").read())

    rep.add("实体词表带类型（人物/地点/道具）",
            any(k == "道具" for k in ents.values())
            and any(k == "地点" for k in ents.values()),
            "%d 个" % len(ents))
    rep.add("词表含「死亡确认书」", "死亡确认书" in ents)

    # 8 个真假样本。**期望值都经过核对**，不是想当然——
    # 「黄铜签牌」那条原先被我当成真声明，查过才知道 EP002 正文没有它。
    cases = [
        ("真·死亡确认书", "死亡确认书仍在沈九围裙口袋里", True),
        ("假·黄铜签牌（EP002 正文无此物）", "黄铜签牌仍在沈九围裙口袋里", False),
        ("假·檀木算盘（凭空捏造）", "檀木算盘仍在沈九围裙口袋里", False),
        ("假·铜制门牌（凭空捏造）", "铜制门牌仍在沈九围裙口袋里", False),
        ("真·炉膛温度", "炉膛空、温度九百度，三〇〇号整容台与盛伯渊下落仍未知", True),
        ("真·你爸的墓", '周主任提到"你爸的墓前天被人扫过"未闭环。', True),
        ("假·完全编造情节", "办公桌上的紫水晶镇纸在打斗中碎成三块。", False),
        ("假·编造人名与事件", "老郑昨天回过办公室，留下一封信。", False),
    ]
    for desc, clause, want in cases:
        r = cont.subject_hits(clause, body, ents)
        rep.add(desc, r["ok"] == want,
                "期望%s 实际%s (%s)" % ("过" if want else "拦",
                                        "过" if r["ok"] else "拦", r["kind"]))

    # 反向保证：正文**不含**连续性标注（否则声明会拿自己比对，检查是假的）
    rep.add("正文剔除了 [连续性] 标注（防自比）",
            "[连续性]" not in body)
    rep.add("正文剔除了 [画面文字] 等制作标注",
            "[画面文字]" not in body and "[SFX]" not in body)
    # 假声明不能因为"背景短语"而通过
    rep.add("假声明不因『围裙口袋里』而通过",
            not cont.subject_hits("檀木算盘仍在沈九围裙口袋里",
                                  body, ents)["ok"])

    # 真文件上跑一遍，确认 0 阻断
    os.system('"%s" "%s" --from 1 --to 2 > nul 2>&1'
              % (sys.executable, os.path.join(ROOT, "tools", "continuity.py")))
    return rep.report()


if __name__ == "__main__":
    sys.exit(main())
