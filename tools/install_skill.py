# -*- coding: utf-8 -*-
"""技能获取与安装 —— 带质量门禁。

"必须能下载安装技能"和"不好用的不要"是同一件事的两面：前者是能力，
后者是门禁。这个脚本把门禁写成可执行的检查，而不是靠感觉。

用法：
  # 列出候选并逐个评分（不安装）
  python tools/install_skill.py --list --tar <tarball>

  # 从本地 tarball 安装指定包
  python tools/install_skill.py --tar <tarball> --install seedance-2-0 visual-style

  # 从 URL 安装单个 SKILL.md
  python tools/install_skill.py --from-url https://... --name foo

  # 只检查不落盘
  python tools/install_skill.py --tar <tarball> --install X --dry-run

质量门禁分两类，刻意分开：
  硬性（不过就拒绝）—— 格式与废弃标记。这些是客观的。
  提示（只标记）—— 体积异常、出处缺失、描述稀薄。这些需要人判断。
**相关性不进门禁**：脚本判断不了"这个技能对我们这条流水线有没有用"，
它只把描述打出来让人决定。把相关性做成自动判断只会制造假信心。
"""
import argparse
import base64
import json
import os
import re
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_ROOT = os.path.join(ROOT, "skills", "techniques")
PROVENANCE = os.path.join(DEST_ROOT, "_provenance.json")

# 硬性拒绝：自我标注废弃 / 明确被取代
DEPRECATED_PAT = re.compile(
    r"\[DEPRECATED\]|\bdeprecated\b|use\s+`?[\w-]+`?\s+instead",
    re.I)
# 硬性要求
MIN_BODY = 1200          # 正文最小字符数，低于此多半是壳
MIN_DESC = 40            # description 最小长度
# 提示阈值
BIG_PACK = 5 * 1024 * 1024


def red(s):
    return "\033[31m%s\033[0m" % s if sys.stdout.isatty() else s


def green(s):
    return "\033[32m%s\033[0m" % s if sys.stdout.isatty() else s


def yellow(s):
    return "\033[33m%s\033[0m" % s if sys.stdout.isatty() else s


def parse_frontmatter(text):
    """取出 YAML frontmatter 的 name 与 description。没有就返回空。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    fm = m.group(1)
    out = {}
    nm = re.search(r"^name:\s*(.+)$", fm, re.M)
    if nm:
        out["name"] = nm.group(1).strip().strip("\"'")
    dm = re.search(r"^description:\s*(.*?)(?=^[a-zA-Z_]+:|\Z)", fm, re.M | re.S)
    if dm:
        out["description"] = re.sub(r"\s+", " ", dm.group(1)).strip().strip("\"'|>-")
    out["_raw"] = fm
    return out


def deprecated_names(packs):
    """先算出哪些包自我标注了废弃。用于第二遍的依赖检查。"""
    out = set()
    for n, p in packs.items():
        if p["skill_md"] and DEPRECATED_PAT.search(p["skill_md"][:4000]):
            out.add(n)
    return out


def gate(name, skill_md, files, total_bytes, deprecated=()):
    """返回 (ok, 硬性问题列表, 提示列表)。"""
    hard, soft = [], []
    if skill_md is None:
        hard.append("没有 SKILL.md —— 格式不合格，无法作为技能加载")
        return False, hard, soft

    fm = parse_frontmatter(skill_md)
    if not fm:
        hard.append("没有 YAML frontmatter（技能系统靠它取 name/description）")
    else:
        if not fm.get("name"):
            hard.append("frontmatter 缺 name")
        if not fm.get("description"):
            hard.append("frontmatter 缺 description")
        elif len(fm["description"]) < MIN_DESC:
            hard.append("description 只有 %d 字符，不足以让智能体判断何时该用"
                        % len(fm["description"]))

    body = skill_md
    if fm:
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", skill_md, count=1, flags=re.S)
    if len(body) < MIN_BODY:
        hard.append("正文只有 %d 字符，低于 %d 的底线——多半是壳，不是技能"
                    % (len(body), MIN_BODY))

    if DEPRECATED_PAT.search(skill_md[:4000]):
        hard.append("自我标注了废弃或被取代的信号")

    # 依赖检查：某个包合格，但它可能依赖了被拒的包。
    #
    # ⚠️ 教训：这条**不能**做成硬性拒绝。第一版是硬拒，结果 seedance-2-0、
    # visual-style 这些好包全被拒了——因为它们在正文里把 HeyGen 列为**反例**
    # （"Do not invent a Replicate, HeyGen, or Higgsfield identifier"）。
    # 名字出现在文本里 ≠ 依赖它。
    #
    # 而且过度拒绝的失败方向更糟：它是**静默地丢掉好材料**，比给个警告危险。
    # 所以这里只标记，并把原文上下文带出来，让人一眼判断是"支持"还是"依赖"。
    desc = (fm or {}).get("description", "")
    full = desc + "\n" + body
    for dep in deprecated:
        if dep == name:
            continue
        m = re.search(r"[^.\n]{0,90}\b%s\b[^.\n]{0,90}" % re.escape(dep), full, re.I)
        if not m:
            continue
        where = "描述" if re.search(r"\b%s\b" % re.escape(dep), desc, re.I) else "正文"
        soft.append("%s提到已废弃的「%s」，需人判断是支持还是依赖：…%s…"
                    % (where, dep, m.group(0).strip()[:110]))

    # 提示类
    if total_bytes > BIG_PACK:
        soft.append("体积 %.1f MB 偏大，可能有大量媒体资源" % (total_bytes / 1048576.0))
    if not re.search(r"https?://", skill_md):
        soft.append("没有出处 URL（可信度无法追溯）")
    # 陷阱小节。措辞各家用得不一样——实测踩过：只用 "Common Pitfalls|陷阱"
    # 会把写着「10 项关键踩坑速查」的中文技能误判成没有陷阱小节。
    if not re.search(r"^#{1,3}\s*[^\n]*?"
                     r"(Common Pitfalls|Pitfalls|常见陷阱|陷阱|踩坑|避坑|常见问题|注意事项)",
                     skill_md, re.M | re.I):
        soft.append("没有「陷阱」小节")
    if not re.search(r"^\|.*\|", skill_md, re.M):
        soft.append("没有表格（结构化信息少）")
    return not hard, hard, soft


def load_tar(path):
    """返回 {pack: {"skill_md": str|None, "files": int, "bytes": int,
                    "body_files": [(rel, bytes)]}}"""
    packs = {}
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/", 1)
            if len(parts) < 2:
                continue
            rel = parts[1]
            if not rel.startswith(".agents/skills/"):
                continue
            seg = rel[len(".agents/skills/"):].split("/")
            pack = seg[0]
            p = packs.setdefault(pack, {"skill_md": None, "files": 0, "bytes": 0,
                                        "body_files": []})
            p["files"] += 1
            p["bytes"] += m.size
            p["body_files"].append((rel, m.size))
            if len(seg) == 2 and seg[1].lower() == "skill.md":
                p["skill_md"] = tf.extractfile(m).read().decode("utf-8", "replace")
    return packs


def do_list(packs):
    print("%-32s %5s %9s %8s  %s" % ("技能包", "文件", "体积", "门禁", "说明"))
    print("-" * 132)
    dep = deprecated_names(packs)
    n_ok = 0
    for name in sorted(packs, key=lambda n: -packs[n]["bytes"]):
        p = packs[name]
        ok, hard, soft = gate(name, p["skill_md"], p["files"], p["bytes"], dep)
        if ok:
            n_ok += 1
        fm = parse_frontmatter(p["skill_md"] or "") or {}
        desc = (fm.get("description") or "")[:52]
        mark = green("通过") if ok else red("拒绝")
        note = desc if ok else hard[0][:60]
        print("%-32s %5d %8.1fKB %8s  %s"
              % (name, p["files"], p["bytes"] / 1024.0, mark, note))
    print("\n共 %d 个，通过门禁 %d 个，拒绝 %d 个"
          % (len(packs), n_ok, len(packs) - n_ok))


def do_install(packs, names, dry_run):
    os.makedirs(DEST_ROOT, exist_ok=True)
    # --tar 装的时候必须显式给来源与许可，否则标未知。
    # 原来写死成 OpenMontage/AGPL-3.0，装 zenstory 的包时就记错了。
    tar_source = getattr(ARGS, "source", None)
    tar_license = ARGS.license_id
    prov = {}
    if os.path.exists(PROVENANCE):
        prov = json.load(open(PROVENANCE, encoding="utf-8"))
    installed, rejected = [], []
    with tarfile.open(ARGS.tar, "r:gz") as tf:
        members = {}
        for m in tf.getmembers():
            if m.isfile():
                parts = m.name.split("/", 1)
                if len(parts) == 2 and parts[1].startswith(".agents/skills/"):
                    members.setdefault(
                        parts[1][len(".agents/skills/"):].split("/")[0], []).append(m)
        dep = deprecated_names(packs)
        for name in names:
            if name not in packs:
                rejected.append((name, "tarball 里没有这个包"))
                continue
            p = packs[name]
            ok, hard, soft = gate(name, p["skill_md"], p["files"], p["bytes"], dep)
            if not ok:
                rejected.append((name, hard[0]))
                continue
            if dry_run:
                installed.append(name)
                continue
            # rel 已经是「<包名>/<其余路径>」，所以直接拼到库根目录，
            # **不能**再拼一次包名——否则会变成 <包名>/<包名>/SKILL.md。
            # 实测踩过，是 verify.py 的「登记与实际一致」检查逮住的。
            n = 0
            for m in members[name]:
                rel = m.name.split("/.agents/skills/", 1)[1]
                out = os.path.join(DEST_ROOT, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with tf.extractfile(m) as src, open(out, "wb") as f:
                    f.write(src.read())
                n += 1
            fm = parse_frontmatter(p["skill_md"]) or {}
            prov[name] = {
                # ⚠️ 这里原来把来源与许可**写死**成 OpenMontage / AGPL-3.0，
                # 因为这条路径最初只为 OpenMontage 的 tarball 写的。
                # 实测踩过：用 --tar 装 zenstory 的包时，它照抄了 OpenMontage + AGPL，
                # 而实际是 zenstory-ai/drama-skills + MIT。
                # **许可证记错是双向风险**：记成 AGPL 只是过度保守，
                # 但反过来（AGPL 记成 MIT）就是侵权。
                # 现在必须显式给 --source；不给就标未知并警告。
                "source": tar_source or "**未知来源**（--tar 装的时候没给 --source）",
                "path": ".agents/skills/" + name,
                "license": tar_license or "unknown",
                "files": n,
                "bytes": p["bytes"],
                # 与 --from-github 那条路保持同一套字段，读的人不用猜
                "extras": [],
                "description": fm.get("description", "")[:400],
                "warnings": soft + ([] if tar_source else ["来源未知，--restore 无法工作"]),
            }
            installed.append(name)

    if not dry_run and installed:
        with open(PROVENANCE, "w", encoding="utf-8") as f:
            json.dump(prov, f, ensure_ascii=False, indent=2)
    print("安装 %d 个%s：" % (len(installed), "（dry-run，未落盘）" if dry_run else ""))
    for n in installed:
        print("  [OK] %s" % n)
    for n, why in rejected:
        print("  [拒绝] %s —— %s" % (n, why))
    return len(rejected)


def gh_api(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "dsh-study", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def gh_blob(repo, sha, ref="main"):
    d = gh_api("https://api.github.com/repos/%s/git/blobs/%s" % (repo, sha))
    return base64.b64decode(d["content"])


def do_github(repo, path, name, ref, license_id, extras, dry_run):
    """从 GitHub 仓库的某个目录装一个技能包。

    走 api.github.com（本机的 raw.githubusercontent.com 不通），
    tree 接口一次列全，再逐个 blob 取。目录结构原样保留。
    """
    tree = gh_api("https://api.github.com/repos/%s/git/trees/%s?recursive=1"
                  % (repo, ref))
    if tree.get("truncated"):
        print("警告：仓库过大，tree 被截断，可能漏文件")
    prefix = path.rstrip("/") + "/"
    blobs = [e for e in tree.get("tree", [])
             if e["type"] == "blob" and e["path"].startswith(prefix)]
    if not blobs:
        print("仓库 %s 下没有 %s 这个目录" % (repo, path))
        return 1

    files = {}
    for e in blobs:
        rel = e["path"][len(prefix):]
        files[rel] = gh_blob(repo, e["sha"], ref)
    # 额外的单文件（如案例复盘），原样附到包内
    for ex in extras or []:
        target = next((e for e in tree.get("tree", [])
                       if e["type"] == "blob" and e["path"] == ex), None)
        if not target:
            print("警告：额外文件不存在 %s" % ex)
            continue
        files["extras/" + os.path.basename(ex)] = gh_blob(repo, target["sha"], ref)

    skill_md = None
    for rel, data in files.items():
        if rel.lower() == "skill.md":
            skill_md = data.decode("utf-8", "replace")
    total = sum(len(v) for v in files.values())
    ok, hard, soft = gate(name, skill_md, len(files), total)
    print("来源: github.com/%s  %s  (%s)" % (repo, path, license_id or "许可未注明"))
    print("文件 %d 个，共 %.1f KB" % (len(files), total / 1024.0))
    print("门禁: %s" % ("通过" if ok else "拒绝"))
    for h in hard:
        print("  [硬性] %s" % h)
    for s in soft:
        print("  [提示] %s" % s)
    if not ok:
        return 1
    if dry_run:
        print("dry-run，未落盘")
        return 0

    dest = os.path.join(DEST_ROOT, name)
    for rel, data in files.items():
        out = os.path.join(dest, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
    prov = {}
    if os.path.exists(PROVENANCE):
        prov = json.load(open(PROVENANCE, encoding="utf-8"))
    prov[name] = {
        "source": "github.com/" + repo,
        "path": path,
        "ref": ref,
        "license": license_id or "unknown",
        "files": len(files),
        "bytes": total,
        # 记下额外附带的文件。不记的话 --restore 恢复时会**静默丢掉**它们
        # （实测：ai-mandrama-pipeline 的案例复盘就是这么漏的）。
        "extras": list(extras or []),
        "description": (parse_frontmatter(skill_md or "") or {}).get("description", "")[:400],
        "warnings": soft,
    }
    with open(PROVENANCE, "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    print("已安装到", os.path.relpath(dest, ROOT))
    return 0


def do_restore():
    """按 _provenance.json 把技能包装回来。

    包内容不进 git（许可原因，见 .gitignore），所以换台机器 clone 下来后
    这个目录是空的——这条命令读出处清单，按记录重新拉取。

    优先走本地 tarball：OpenMontage 那 12 个包逐个走 GitHub API 会很快撞上
    未认证限速（60 次/小时），而本地整包一次就能全解出来。
    """
    if not os.path.exists(PROVENANCE):
        print("没有出处清单，无从恢复：%s" % PROVENANCE)
        return 1
    prov = json.load(open(PROVENANCE, encoding="utf-8"))
    tar = os.path.join(ROOT, ".tools", "ref-openmontage", "openmontage.tar.gz")

    from_tar = {n: v for n, v in prov.items()
                if "OpenMontage" in (v.get("source") or "")}
    from_gh = {n: v for n, v in prov.items() if n not in from_tar}

    failed = []
    if from_tar:
        if os.path.exists(tar):
            packs = load_tar(tar)
            names = [n for n in from_tar
                     if os.path.exists(os.path.join(DEST_ROOT, n, "SKILL.md"))]
            done = [n for n in from_tar if n not in names]
            print("从本地 tarball 恢复 %d 个（已在磁盘上的 %d 个跳过）"
                  % (len(done), len(names)))
            if done:
                do_install(packs, done, False)
        else:
            print("本地 tarball 不在：%s" % tar)
            print("  OpenMontage 的 %d 个包需要它。获取方式："
                  "见 README 的「参照语料」一节" % len(from_tar))
            failed += list(from_tar)

    for n, v in from_gh.items():
        src = (v.get("source") or "")
        path = v.get("path") or ""
        if not src.startswith("github.com/"):
            print("  跳过 %s：来源 %s 不是 GitHub 仓库" % (n, src[:50]))
            failed.append(n)
            continue
        repo = src[len("github.com/"):]
        print("\n从 GitHub 恢复 %s（%s %s）" % (n, repo, path))
        rc = do_github(repo, path, n, v.get("ref") or "main",
                       v.get("license"), v.get("extras") or [], False)
        if rc:
            failed.append(n)

    print("\n" + "=" * 60)
    total = len(prov)
    print("恢复完成：%d/%d 成功" % (total - len(failed), total))
    if failed:
        print("未恢复：%s" % ", ".join(failed))
        return 1
    return 0


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", help="本地 tarball 路径")
    ap.add_argument("--source", dest="source",
                    help="--tar 装的时候必须给：来源，如 github.com/zenstory-ai/drama-skills")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--install", nargs="*", default=[])
    ap.add_argument("--restore", action="store_true",
                    help="按 _provenance.json 把技能包装回来")
    ap.add_argument("--from-url")
    ap.add_argument("--from-github", help="owner/repo")
    ap.add_argument("--path", help="仓库内目录，如 skills/xxx")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--license", dest="license_id")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="额外附带的单文件（仓库内路径）")
    ap.add_argument("--name")
    ap.add_argument("--dry-run", action="store_true")
    ARGS = ap.parse_args()

    if ARGS.restore:
        return do_restore()

    if ARGS.from_github:
        if not (ARGS.path and ARGS.name):
            ap.error("--from-github 需要同时给 --path 和 --name")
        return do_github(ARGS.from_github, ARGS.path, ARGS.name, ARGS.ref,
                         ARGS.license_id, ARGS.extra, ARGS.dry_run)

    if ARGS.from_url:
        req = urllib.request.Request(ARGS.from_url, headers={"User-Agent": "dsh"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
        name = ARGS.name or "unnamed"
        ok, hard, soft = gate(name, text, 1, len(text))
        print("来源:", ARGS.from_url)
        print("门禁:", "通过" if ok else "拒绝")
        for h in hard:
            print("  [硬性] %s" % h)
        for s in soft:
            print("  [提示] %s" % s)
        if not ok:
            return 1
        if ARGS.dry_run:
            print("dry-run，未落盘")
            return 0
        dest = os.path.join(DEST_ROOT, name)
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)
        prov = {}
        if os.path.exists(PROVENANCE):
            prov = json.load(open(PROVENANCE, encoding="utf-8"))
        prov[name] = {"source": ARGS.from_url, "files": 1, "bytes": len(text),
                      "description": (parse_frontmatter(text) or {}).get("description", "")[:400],
                      "warnings": soft}
        with open(PROVENANCE, "w", encoding="utf-8") as f:
            json.dump(prov, f, ensure_ascii=False, indent=2)
        print("已安装到", os.path.relpath(dest, ROOT))
        return 0

    if not ARGS.tar:
        ap.error("需要 --tar 或 --from-url")
    packs = load_tar(ARGS.tar)
    if ARGS.list or not ARGS.install:
        do_list(packs)
    if ARGS.install:
        return 1 if do_install(packs, ARGS.install, ARGS.dry_run) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
