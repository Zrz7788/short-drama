# -*- coding: utf-8 -*-
r"""第 1 阶段 · Markdown 导入通道自检（契约 + 端到端）。

为什么单独一个自检：本模块的对白正则**抄自**技能验证器，抄就会分叉，
而分叉是安静的——第一版那个 `_looks_like_action()` 把 3 句带表演提示的
对白变成了动作段，库照写、报告照绿（HANDOFF §七：安静地坏着）。
所以这里把两类断言钉死：

  契约层  格式能不能解析、错格式会不会被拦（不碰数据库）
  端到端  导入临时库后，script/scene 的每一列是不是对的

跑法：python pipeline\selftest_mdimport.py
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import md_import as M  # noqa: E402

FIXTURES = os.path.join(C.ROOT, "pipeline", "tests", "fixtures")
GOOD = os.path.join(FIXTURES, "screenplay_good.md")
TEST_ROOT = os.path.join(C.ROOT, ".tools", "mdimport-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")

rep = C.Report("第 1 阶段 · Markdown 导入通道自检")
PROD0 = C.prod_fingerprint()


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 契约层
print("\n=== 1. 格式契约（不碰数据库）===")

parsed = M.parse_md(fixture("screenplay_good.md"))
if os.environ.get("MDI_DEBUG"):
    for _s in parsed["scenes"]:
        print("DEBUG body of", _s["scene_id"])
        for _l in _s["body"]:
            _tm = M.TAG_RE.match(_l.strip())
            _dm = M.DIALOGUE.match(_l.strip())
            print("   %-58r tag=%s dlg=%s" % (_l, bool(_tm),
                                              _dm.group("speaker") if _dm else None))
        print("   -> dialogues:", [d["who"] for d in _s["dialogues"]],
              "tags:", [t["tag"] for t in _s["tags"]])
rep.add("一级标题解析", parsed["episode_id"] == "EP007" and parsed["title"] == "证明",
        "%s %s" % (parsed["episode_id"], parsed["title"]))
rep.add("场景数与场景 ID", [s["scene_id"] for s in parsed["scenes"]]
        == ["EP007-SC001", "EP007-SC002"])
s1, s2 = parsed["scenes"]
rep.add("场景标题三段拆开",
        (s1["place"], s1["location"], s1["time_of_day"]) == ("内", "档案室", "冬夜"),
        "%s / %s / %s" % (s1["place"], s1["location"], s1["time_of_day"]))

# ⭐ 回归：带括号表演提示的对白必须仍是对白。
#    第一版的兜底函数读了括号**里面**的逗号，把它判成了动作段。
rep.add("带表演提示的对白没有被误判为动作（回归）",
        len(s1["dialogues"]) == 1 and s1["dialogues"][0]["who"] == "林薇",
        "SC001 对白 %d 句：%s" % (len(s1["dialogues"]),
                                 [d["who"] for d in s1["dialogues"]]))
rep.add("表演提示被保留而不是丢掉",
        s1["dialogues"][0]["cue"] == "压着声音，手指还在装订线上",
        str(s1["dialogues"][0]["cue"]))
rep.add("台词正文不含提示也不含说话者",
        s1["dialogues"][0]["text"] == "这本是后来做的。",
        s1["dialogues"][0]["text"])

rep.add("不带提示的对白同样识别", [d["who"] for d in s2["dialogues"]] == ["陈默"])
# `[画面文字] 卷宗编号：甲—0417 改 甲—1193` —— 标签正文里的冒号**不是**对白定界符
rep.add("生产标签正文里的冒号没有被当成对白",
        [t["tag"] for t in s1["tags"]] == ["画面文字"]
        and len(s1["dialogues"]) == 1,
        str([t["tag"] for t in s1["tags"]]))
rep.add("[连续性] 归为标签", [t["tag"] for t in s2["tags"]] == ["连续性"])
rep.add("speaker 清单正确", s1["speakers"] == ["林薇"] and s2["speakers"] == ["陈默"],
        "%s / %s" % (s1["speakers"], s2["speakers"]))
rep.add("不含标点的可发声字数已统计", s1["spoken_chars"] == 7,
        "SC001 spoken_chars=%d" % s1["spoken_chars"])

# ---- 错格式必须被拦，而不是被猜过去 ----
BAD = [
    ("缺场景标题", "# EP007 证明\n\n只有正文，没有场景标题。\n"),
    ("集号非三位数", "# EP7 证明\n\n## EP007-SC001 内 · 档案室 · 冬夜\n\n她站着。\n"),
    ("场景 ID 重复",
     "# EP007 证明\n\n## EP007-SC001 内 · 档案室 · 冬夜\n\n她站着。\n\n"
     "## EP007-SC001 内 · 走廊 · 冬夜\n\n她走了。\n"),
    ("空间性质非法", "# EP007 证明\n\n## EP007-SC001 里 · 档案室 · 冬夜\n\n她站着。\n"),
    ("场景标题缺时间",
     "# EP007 证明\n\n## EP007-SC001 内 · 档案室\n\n她站着。\n"),
]
for name, text in BAD:
    try:
        M.parse_md(text)
        rep.add("错格式被拦：%s" % name, False, "居然解析通过了")
    except M.ScriptFormatError as e:
        rep.add("错格式被拦：%s" % name, True, str(e)[:38])

# ---------------------------------------------------------------- 端到端
print("\n=== 2. 导入临时库（端到端）===")

shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.makedirs(TEST_ROOT, exist_ok=True)
import subprocess  # noqa: E402

p = subprocess.run([sys.executable, os.path.join(C.ROOT, "db", "init_db.py"),
                    "--db", TEST_DB, "--rebuild"],
                   cwd=C.ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
rep.add("临时库建立", p.returncode == 0, C.tail(p.stderr or p.stdout, 80))

con = C.connect(TEST_DB)
pid = con.execute("INSERT INTO project (name, logline) VALUES ('导入自检剧','测试')"
                  ).lastrowid
eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                  "VALUES (?,7,80)", (pid,)).lastrowid
con.commit()

ep = C.episode_info(con, eid)
rc = M.run(con, ep, GOOD, dry=False, beat_override=None)
rep.add("导入退出码 0", rc == 0, "rc=%s" % rc)

s = con.execute("SELECT * FROM script WHERE episode_id=?", (eid,)).fetchone()
rep.add("script 行已写入且为现行稿", s is not None and s["is_current"] == 1)
rep.add("集标题回填", s and s["title"] == "EP007：证明", s["title"] if s else "")
rep.add("正文保留了原文的说话者与表演提示",
        s and "林薇（压着声音，手指还在装订线上）：这本是后来做的。" in s["content"])

scenes = con.execute("SELECT * FROM scene WHERE script_id=? ORDER BY seq",
                     (s["id"],)).fetchall()
rep.add("scene 行数", len(scenes) == 2, "%d" % len(scenes))
rep.add("slug 是「空间 · 地点 · 时间」",
        scenes[0]["slug"] == "内 · 档案室 · 冬夜", scenes[0]["slug"])
rep.add("location / time_of_day 分列存对",
        (scenes[0]["location"], scenes[0]["time_of_day"]) == ("档案室", "冬夜"))
rep.add("dialogue 保留原文整行（含提示）",
        scenes[0]["dialogue"] == "林薇（压着声音，手指还在装订线上）：这本是后来做的。",
        scenes[0]["dialogue"])
rep.add("action 里不含对白原文",
        "这本是后来做的" not in scenes[0]["action"], scenes[0]["action"][:40])
rep.add("action 里不含标签行（标签单独归集）",
        "[画面文字]" not in scenes[0]["action"]
        and "[连续性]" not in scenes[1]["action"])

# beat 是**推断**的，不是原文——首场钩子、末场扣子
beats = [r["beat"] for r in scenes]
rep.add("beat 推断为首场钩子、末场扣子", beats == ["钩子", "扣子"], " ".join(beats))
total = con.execute("SELECT SUM(duration_s) FROM scene WHERE script_id=?",
                    (s["id"],)).fetchone()[0]
rep.add("时长分配合计等于本集目标 80 秒", abs(total - 80) < 0.05, "%.1f 秒" % total)
rep.add("每场分配时长都为正", all(r["duration_s"] > 0 for r in scenes))

st = con.execute("SELECT status, title FROM episode WHERE id=?", (eid,)).fetchone()
rep.add("episode 状态置为 scripted", st["status"] == "scripted", st["status"])

# 标注文件：推断值不能只活在报告里
side = os.path.join(os.path.dirname(GOOD), ".beat-inferred")
rep.add("已写 .beat-inferred 标注文件", os.path.exists(side))
if os.path.exists(side):
    with open(side, encoding="utf-8") as f:
        txt = f.read()
    rep.add("标注文件写明 beat 与 duration 是推断的",
            "推断" in txt and "EP007-SC001" in txt)

# ---- 覆盖保护：重导应产生新版本，旧版本保留可追溯 ----
con.close()
con = C.connect(TEST_DB)
ep = C.episode_info(con, eid)
M.run(con, ep, GOOD, dry=False, beat_override=None)
vs = [(r["version"], r["is_current"]) for r in con.execute(
    "SELECT version, is_current FROM script WHERE episode_id=? ORDER BY version", (eid,))]
rep.add("重导产生 v1/v2 且只有 v2 现行", vs == [(1, 0), (2, 1)], str(vs))
rep.add("旧稿未被删除", len(vs) == 2)
con.close()

# ---- 生产库隔离：自检绝不能碰真库 ----
rep.add("生产库未被改动", C.prod_unchanged(PROD0), "")

# ---------------------------------------------------------------- 收尾
shutil.rmtree(TEST_ROOT, ignore_errors=True)
for stray in (".beat-inferred",):
    sp = os.path.join(os.path.dirname(GOOD), stray)
    if os.path.exists(sp):
        os.remove(sp)
sys.exit(rep.finish())
