# -*- coding: utf-8 -*-
"""工程底座全量验收。

一次跑完三件事并给出通过/失败汇总：
  1. git 仓库完整性，以及「不该进仓库的东西确实没进去」
  2. 知识库文档齐备且非空
  3. 数据库结构、数据域覆盖、schema 自检

用法： python tools/verify.py
退出码 0 = 全部通过。
"""
import os
import re
import subprocess
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT = os.path.join(ROOT, ".tools", "mingit", "cmd", "git.exe")
DB = os.path.join(ROOT, "data", "drama.db")

# 必须存在于知识库的文档
KB_DOCS = [
    "README.md",
    "01-制作方法/剧本规范.md",
    "01-制作方法/竖屏短剧结构模板.md",
    "01-制作方法/角色一致性SOP.md",
    "01-制作方法/抽卡策略与Draft模式.md",
    "01-制作方法/分镜表字段规范.md",
    "02-模型与成本/火山方舟模型与价格.md",
    "02-模型与成本/单集成本模型.md",
    "03-项目档案/README.md",
    "04-复盘/README.md",
]

# 绝不能进仓库的路径（私钥、数据库、39MB 工具链）
MUST_NOT_TRACK = [
    ".tools/ssh/id_ed25519",
    ".tools/mingit/cmd/git.exe",
    "data/drama.db",
]

DOMAINS = {
    "剧本": ["script", "scene"],
    "集": ["episode"],
    "镜头": ["shot"],
    "素材": ["asset"],
    "生成任务": ["generation"],
    "成本": ["cost_ledger", "price_book"],
}

results = []


def ok(section, name, passed, detail=""):
    results.append((section, name, passed, detail))
    print("  [%s] %-44s %s" % ("OK" if passed else "!!", name, detail))


def git(*args):
    p = subprocess.run([GIT] + list(args), cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip()


def check_git():
    print("\n=== 1. git ===")
    if not os.path.exists(GIT):
        ok("git", "便携版 git 存在", False, GIT)
        return
    ok("git", "便携版 git 存在", True)

    rc, out = git("rev-parse", "--is-inside-work-tree")
    ok("git", "是 git 仓库", rc == 0 and out == "true")

    rc, out = git("rev-list", "--count", "HEAD")
    ok("git", "有提交历史", rc == 0 and out.isdigit() and int(out) > 0, "%s 个提交" % out)

    rc, out = git("status", "--porcelain")
    ok("git", "工作区干净", rc == 0 and out == "", "" if not out else "有未提交改动")

    rc, out = git("fsck", "--no-progress")
    bad = [l for l in out.splitlines() if l and not l.startswith("dangling")]
    ok("git", "对象库无损坏", not bad, "" if not bad else bad[0][:60])

    rc, tracked = git("ls-files")
    files = set(tracked.splitlines())
    ok("git", "已跟踪文件数", len(files) > 0, "%d 个" % len(files))

    # 安全检查：私钥 / 数据库 / 工具链都不得被跟踪
    leaked = [p for p in MUST_NOT_TRACK if p in files]
    ok("git", "私钥/数据库/工具链未被跟踪", not leaked, "" if not leaked else str(leaked))

    # 反向确认 .gitignore 真的在拦
    for p in MUST_NOT_TRACK:
        rc, _ = git("check-ignore", "-q", p)
        ok("git", "已被忽略: %s" % p, rc == 0)

    # 仓库内不得出现疑似密钥
    suspicious = []
    for f in files:
        if not f.endswith((".md", ".py", ".sql", ".yml", ".yaml", ".json", ".txt")):
            continue
        full = os.path.join(ROOT, f)
        try:
            text = open(full, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for pat in (r"sk-[A-Za-z0-9]{16,}", r"ARK_API_KEY\s*=\s*['\"]?[A-Za-z0-9\-_]{12,}",
                    r"BEGIN OPENSSH PRIVATE KEY"):
            if re.search(pat, text):
                suspicious.append("%s ~ %s" % (f, pat))
    ok("git", "跟踪文件中无明文密钥", not suspicious,
       "" if not suspicious else suspicious[0][:70])


def check_knowledge():
    print("\n=== 2. 知识库 ===")
    missing, empty = [], []
    for rel in KB_DOCS:
        p = os.path.join(ROOT, "knowledge", rel)
        if not os.path.exists(p):
            missing.append(rel)
        elif len(open(p, encoding="utf-8", errors="ignore").read().strip()) < 100:
            empty.append(rel)
    ok("知识库", "应有文档 %d 篇都在" % len(KB_DOCS), not missing,
       "" if not missing else "缺: %s" % missing)
    ok("知识库", "文档均有实质内容", not empty, "" if not empty else str(empty))


def check_db():
    print("\n=== 3. 数据库 ===")
    if not os.path.exists(DB):
        ok("数据库", "库文件存在", False, "先跑 python db/init_db.py")
        return
    con = sqlite3.connect(DB)

    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for domain, tabs in DOMAINS.items():
        hit = [t for t in tabs if t in tables]
        ok("数据库", "数据域可承载: %s" % domain, len(hit) == len(tabs),
           ", ".join(hit) if hit else "缺失")

    views = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view'")}
    need_views = {"v_episode_cost", "v_episode_outline", "v_shot_progress", "v_price_book"}
    ok("数据库", "视图齐备", need_views <= views,
       "" if need_views <= views else "缺: %s" % (need_views - views))

    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")]
    ok("数据库", "索引数量", len(idx) >= 12, "%d 个" % len(idx))

    bad = list(con.execute("PRAGMA foreign_key_check"))
    ok("数据库", "外键完整性", not bad, "" if not bad else str(bad[:2]))

    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    ok("数据库", "schema 版本已记录", bool(row), "v%s" % row[0] if row else "无")

    n = con.execute("SELECT COUNT(*) FROM price_book").fetchone()[0]
    ok("数据库", "价目表非空", n > 0, "%d 条" % n)
    con.close()


def check_selftest():
    print("\n=== 4. schema 自检 ===")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(ROOT, "db", "selftest.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    last = [l for l in (p.stdout or "").strip().splitlines() if l.strip()]
    ok("自检", "db/selftest.py 通过", p.returncode == 0,
       last[-1] if last else (p.stderr or "")[:70])


def main():
    print("工程底座全量验收")
    print("仓库:", ROOT)
    check_git()
    check_knowledge()
    check_db()
    check_selftest()

    failed = [r for r in results if not r[2]]
    print("\n" + "=" * 64)
    print("共 %d 项，通过 %d 项，失败 %d 项 -> %s"
          % (len(results), len(results) - len(failed), len(failed),
             "全部通过" if not failed else "**有失败**"))
    for sec, name, _, detail in failed:
        print("  失败: [%s] %s %s" % (sec, name, detail))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
