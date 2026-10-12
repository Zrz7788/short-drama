# -*- coding: utf-8 -*-
r"""诊断：为什么 parse_domain_facts 只解析到 3 条，而门禁报 25 条。

两种可能：
  a) 解析器把「领域事实」章截断得太早（正则问题）——**真 bug**
  b) 计数口径不同（门禁另外读了别处）——**我的审计写错了**
必须先分辨，否则可能是在用一份残缺的事实表做门禁。
"""
import re
import sys

sys.path.insert(0, ".")
from pipeline import stage_entities as SE  # noqa: E402

P = "knowledge/03-项目档案/殡仪馆的阿九.md"
text = open(P, encoding="utf-8").read()

print("=" * 74)
print("1) 档案里到底有多少事实行")
print("=" * 74)
m = re.search(r"^##\s*领域事实\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
if not m:
    print("  ❌ 正则没匹配到「## 领域事实」章 —— 这就是 bug 所在")
    # 退一步：手动按标题切
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "## 领域事实")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end])
    print("  手动切出来 %d 行" % len(body.splitlines()))
else:
    body = m.group(1)
    print("  正则切出来 %d 行" % len(body.splitlines()))

rows = []
for line in body.splitlines():
    line = line.strip()
    if not line.startswith("|"):
        continue
    cells = [c.strip() for c in line.strip("|").split("|")]
    rows.append(cells)
print("  表格行（含表头/分隔线）共 %d 行" % len(rows))
print()
print("  逐行看解析器的判据（需 ≥5 格 且 首格匹配 F-xx）：")
ok_rows = []
for cells in rows:
    fid = cells[0] if cells else ""
    n = len(cells)
    pass_len = n >= 5
    pass_id = bool(SE.FACT_ID.match(fid))
    mark = "✅ 收" if (pass_len and pass_id) else "✗ 弃"
    why = []
    if not pass_len:
        why.append("格数 %d < 5" % n)
    if not pass_id:
        why.append("首格 %r 不匹配 F-xx" % fid[:16])
    ok_rows.append((mark, fid, n, "; ".join(why)))
    print("    %s  格数=%-2d 首格=%-14r %s" % (mark, n, fid[:14], "; ".join(why)))

print()
print("=" * 74)
print("2) 解析器实际返回（解包三元组）")
print("=" * 74)
facts, ok, why = SE.parse_domain_facts(P)
print("  ok=%s  why=%s  条数=%d" % (ok, why, len(facts)))
for k in sorted(facts):
    v = facts[k]
    print("    %-7s assumed=%-5s conf=%-10s %s"
          % (k, v["assumed"], v["confidence"], v["sentence"][:44]))

print()
print("=" * 74)
print("3) 门禁那条「25 条」是什么口径")
print("=" * 74)
for name in ("load_domain_facts", "load_domain_questions"):
    print("  %s:" % name)
    try:
        import inspect
        src = inspect.getsource(getattr(SE, name))
        for ln in src.splitlines():
            print("     %s" % ln)
    except Exception as e:
        print("     （读不到源码：%s）" % e)
