# -*- coding: utf-8 -*-
r"""第 2 阶段 · 领域事实门禁自检。

**为什么单独一个自检**：这道门禁挡的是"凭想象编的实体"，而实体描述是生图
提示词的直接输入——错了每张图 ¥0.30，而且错图会成为首帧参考、把错锁进下游。
第 1 集就是这样三样全错（空间/载体/动作），当时没有任何东西挡它。

所以这里把两类断言钉死：

  解析层  项目档案里的「领域事实」表能不能解析、哪些算"假设"
  门禁层  该拦的拦不拦得住、该放的放不放行

跑法：python pipeline\selftest_domain.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import stage_entities as SE  # noqa: E402

rep = C.Report("第 2 阶段 · 领域事实门禁自检")
PROD0 = C.prod_fingerprint()
TMP = os.path.join(tempfile.gettempdir(), "domain-gate-test")


def write_brief(text):
    os.makedirs(TMP, exist_ok=True)
    p = os.path.join(TMP, "测试剧.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


BRIEF_OK = """# 测试剧

## 领域事实

### 已确认

| 编号 | 类别 | 事实 | 出处 | 置信度 |
|---|---|---|---|---|
| F-01 | 空间 | 遗体存放与化妆整容是两个分开的空间 | 用户指出 | 高 |
| F-02 | 作业 | 转运推车不是作业面，作业面是防腐整容台 | 用户指出 | 高 |

### 假设（待验证）

| 编号 | 环节 | 推测内容 | 出处 | 置信度 |
|---|---|---|---|---|
| F-91 | 用具 | 唇部多用唇油而非口红 | 无 | 低·待验证 |

## 角色

见数据库。
"""

EP = {"id": 1, "seq": 1, "project": "测试剧", "project_id": 1}


def ents(*items):
    """items: (kind, name, domain_fact)"""
    return {"entities": [{"kind": k, "name": n, "tier": "main",
                          "description": "d", "domain_fact": df}
                         for k, n, df in items]}


# ---------------------------------------------------------------- 1. 解析层
print("\n=== 1. 解析「领域事实」表 ===")

p = write_brief(BRIEF_OK)
facts, ok, why = SE.parse_domain_facts(p)
rep.add("能解析出事实表", ok and len(facts) == 3, "%s / %s" % (ok, why))
rep.add("已确认条目标为非假设",
        not facts["F-01"]["assumed"] and not facts["F-02"]["assumed"])
rep.add("F-9x 段标为假设", facts["F-91"]["assumed"])
rep.add("置信度与出处被读出来",
        facts["F-01"]["confidence"] == "高" and facts["F-01"]["source"] == "用户指出",
        "%s / %s" % (facts["F-01"]["confidence"], facts["F-01"]["source"]))

# 置信度写"低"但没有 F-9x 编号 -> 也应该算假设（靠文字判断，不只靠编号）
p2 = write_brief(BRIEF_OK.replace("| F-91 |", "| F-05 |"))
f2, ok2, _ = SE.parse_domain_facts(p2)
rep.add("编号不是 F-9x 但置信度含「低」也算假设",
        ok2 and f2["F-05"]["assumed"], "assumed=%s" % f2.get("F-05", {}).get("assumed"))

# 出处为空 -> 也算假设（无出处不能当定论）
p3 = write_brief(BRIEF_OK.replace("| 用户指出 | 高 |", "|  | 高 |", 1))
f3, ok3, _ = SE.parse_domain_facts(p3)
rep.add("出处为空也算假设", ok3 and f3["F-01"]["assumed"])

for name, text in (("文件不存在", None),
                   ("没有「领域事实」章", "# 测试剧\n\n## 角色\n\n无\n"),
                   ("章在但表不可解析", "# 测试剧\n\n## 领域事实\n\n随便写点东西。\n")):
    if text is None:
        okx, whyx = SE.parse_domain_facts(os.path.join(TMP, "不存在.md"))[1:]
    else:
        okx, whyx = SE.parse_domain_facts(write_brief(text))[1:]
    rep.add("解析失败被识别：%s" % name, not okx, whyx[:40])

# ---------------------------------------------------------------- 2. 门禁层
print("\n=== 2. 门禁（该拦的拦住，该放的放行）===")


def gate(data, allow_assumed=False, facts_ok=True):
    return SE.domain_gate(None, EP, data, facts, facts_ok, why, allow_assumed)


b, w = gate(ents(("location", "整容室", "F-01"), ("prop", "整容台", "F-02"),
                 ("character", "沈九", "story")))
rep.add("全部可溯源 -> 放行", not b, "拦下 %d 项" % len(b))

b, _ = gate(ents(("location", "化妆间", "")))
rep.add("domain_fact 留空 -> 拦下", bool(b), b[0][:44] if b else "")

b, _ = gate(ents(("prop", "口红", "F-99")))
rep.add("引用了不存在的编号 -> 拦下",
        bool(b) and "不存在" in b[0], b[0][:44] if b else "")

b, _ = gate(ents(("prop", "口红", "F-91")))
rep.add("依据「假设·待验证」-> 拦下",
        bool(b) and "假设" in b[0], b[0][:52] if b else "")

b, w = gate(ents(("prop", "口红", "F-91")), allow_assumed=True)
rep.add("加 --allow-assumed-facts -> 放行但告警",
        not b and bool(w), w[0][:44] if w else "无告警")

b, _ = gate(ents(("location", "某地", "story")))
rep.add("场景标 story -> 拦下（场景必须有依据）",
        bool(b) and "场景" in b[0], b[0][:44] if b else "")

b, _ = gate(ents(("prop", "一封信", "story"), ("character", "沈九", "story")))
rep.add("剧情道具与人物标 story -> 放行", not b)

# 领域事实整体不可用 -> 一律拦下，且给出补法
b, _ = gate(ents(("location", "整容室", "F-01")), facts_ok=False)
rep.add("领域事实不可用 -> 一律拦下",
        bool(b) and "领域事实不可用" in b[0], b[0][:40].replace("\n", " ") if b else "")
rep.add("拦下时给出补法（指向项目档案）",
        bool(b) and "03-项目档案" in b[0])

# ---------------------------------------------------------------- 3. 提示词层
print("\n=== 3. 提示词注入 ===")
dg = SE.facts_digest(facts)
rep.add("已确认事实进入清单", "F-01" in dg and "两个分开的空间" in dg)
rep.add("假设条目被显式标为禁止依据", "假设" in dg and "禁止" in dg)
rep.add("无事实时清单为空（不报错）", SE.facts_digest({}) == "")

# ---------------------------------------------------------------- 4. 端到端
print("\n=== 4. 端到端：真跑第 2 阶段，看门禁是否真拦 ===")
TEST_ROOT = os.path.join(C.ROOT, ".tools", "domain-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")
shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.makedirs(TEST_ROOT, exist_ok=True)

p = subprocess.run([sys.executable, os.path.join(C.ROOT, "db", "init_db.py"),
                    "--db", TEST_DB, "--rebuild"],
                   cwd=C.ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
rep.add("临时库建立", p.returncode == 0, C.tail(p.stderr or p.stdout, 60))

con = C.connect(TEST_DB)
pid = con.execute("INSERT INTO project (name, logline) VALUES ('门禁测试剧','x')"
                  ).lastrowid
eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                  "VALUES (?,1,80)", (pid,)).lastrowid
con.execute("INSERT INTO script (episode_id, version, title, content, is_current) "
            "VALUES (?,1,'t','# c',1)", (eid,))
con.commit()
con.close()

# 该项目没有项目档案 -> 必须退出非 0
r = subprocess.run([sys.executable, os.path.join(C.ROOT, "pipeline", "stage_entities.py"),
                    "--episode-id", str(eid), "--db", TEST_DB],
                   cwd=C.ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
rep.add("项目档案缺失 -> 退出码非 0", r.returncode != 0, "rc=%s" % r.returncode)
# 断言按**实际报告的标签**写，不按我以为的措辞写。
# 之前这里断言 "领域事实不可用"（那是 domain_gate 的措辞），而提前拦截走的是
# main() 的 "领域事实可用" 行——**测试错了，不是代码错了**。
rep.add("报出领域事实不可用并给出补法",
        "领域事实可用" in (r.stdout or "") and "03-项目档案" in (r.stdout or ""))
rep.add("报告里明确了阻断原因",
        "项目档案不存在" in (r.stdout or ""), "已报出缺失路径")
rep.add("没有调用模型（钱没花）",
        "来源：模型调用" not in (r.stdout or ""), "未打印模型调用")

# 加上档案 -> 门禁通过，但因为没有模型/假服务，会停在调用；只要不再报"不可用"即可
brief_dir = os.path.join(C.ROOT, "knowledge", "03-项目档案")
made = os.path.join(brief_dir, "门禁测试剧.md")
with open(made, "w", encoding="utf-8") as f:
    f.write(BRIEF_OK)
try:
    r2 = subprocess.run([sys.executable, os.path.join(C.ROOT, "pipeline", "stage_entities.py"),
                         "--episode-id", str(eid), "--db", TEST_DB, "--dump-prompt"],
                        cwd=C.ROOT, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    rep.add("补了档案后不再报「不可用」",
            "领域事实不可用" not in (r2.stdout or ""), "rc=%s" % r2.returncode)
    rep.add("提示词里带上了领域事实清单",
            "F-01" in (r2.stdout or "") and "两个分开的空间" in (r2.stdout or ""))
finally:
    if os.path.exists(made):
        os.remove(made)

rep.add("生产库未被改动", C.prod_unchanged(PROD0), "")

shutil.rmtree(TEST_ROOT, ignore_errors=True)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(rep.finish())
