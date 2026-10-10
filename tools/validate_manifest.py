# -*- coding: utf-8 -*-
"""流水线清单校验器（零依赖）。

这台机器上没有 PyYAML，而我们也不想为校验一个文件去装依赖。
所以这里不查完整 YAML 语法，只查**我们契约要求的东西**——比语法检查更有用：

  结构层
    - 无制表符（YAML 里 tab 是硬错误）
    - 缩进是 2 的倍数
    - 每行是「键:」「键: 值」「- 项」或「- 键: 值」之一
    - 顶层必需键存在：name / version / orchestration / stages

  契约层（这层才是重点）
    - 每个 stage 有 name
    - 有人工门禁的阶段必须写明 review_focus（否则人不知道审什么）
    - `human_approval_default: true` 的阶段，其 skill 必须登记在 required_skills 里
    - required_skills 里列的文件必须在 skills/ 下真实存在
    - required_artifacts_in 必须能被前面某个 stage 的 produces 满足（拓扑检查）
    - produces 不允许跨阶段重名

用法： python tools/validate_manifest.py [清单路径]
退出码 0 = 通过。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "pipeline_defs", "short-drama.yaml")

LINE_OK = re.compile(r"^(\s*)(#.*|-\s.*|[A-Za-z_][\w\-]*\s*:.*|.*:\s*.*)$")
KV = re.compile(r"^(\s*)([A-Za-z_][\w\-]*)\s*:\s*(.*)$")
ITEM = re.compile(r"^(\s*)-\s*(.*)$")
REQUIRED_TOP = ["name", "version", "orchestration", "stages"]

problems = []
warnings = []


def check(cond, msg):
    if not cond:
        problems.append(msg)
    return cond


def parse(lines):
    """极简缩进解析：返回 (top_keys, stages, required_skills)。

    只认我们清单里实际用到的形态。刻意不做完整 YAML——那需要一个库，
    而这里的目的是查契约不是查语法。

    必须处理两种折行，否则会把正常写法误报成语法错：
      1. 块标量（`description: >` 之后更深缩进的段落）
      2. 换行的列表项（一个 `- ` 项的文字续到下一行）
    判据：某行既不构成键也不构成列表项，却比上一非空行更缩进 —— 就是续行。
    """
    top, stages, skills = [], [], []
    cur_stage = cur_sub = None
    in_skills = in_stages = False
    prev_indent = None
    sub_indent = None          # 当前 sub_stage 项的缩进，用于区分层级
    block_key_indent = None    # 正在某个块标量（key: > / key: |）内部

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        if "\t" in line:
            problems.append("第 %d 行含制表符（YAML 不允许 tab 缩进）" % i)
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            problems.append("第 %d 行缩进 %d 不是 2 的倍数" % (i, indent))

        # 块标量内部：比该键更缩进的行都算文本。
        # 必须显式记状态——块标量从第二行起缩进与第一行相同，
        # 只用「比上一行更缩进」的判据只能接住第一行（实测踩过）。
        if block_key_indent is not None:
            if indent > block_key_indent:
                prev_indent = indent
                continue
            block_key_indent = None

        is_item = bool(ITEM.match(line))
        is_kv = bool(KV.match(line))

        # 续行判定：不构成键/项，且比上一非空行更缩进
        if prev_indent is not None and indent > prev_indent and not is_kv and not is_item:
            prev_indent = indent
            continue
        if not is_kv and not is_item:
            problems.append("第 %d 行不是可识别的形态：%s" % (i, line.strip()[:60]))
            prev_indent = indent
            continue
        prev_indent = indent

        # 进入块标量
        if is_kv and KV.match(line).group(3).strip() in (">", "|", ">-", "|-", ">+", "|+"):
            block_key_indent = indent

        stripped = line.strip()
        if indent == 0:
            m = KV.match(line)
            if m:
                top.append(m.group(2))
            in_skills = stripped.startswith("required_skills:")
            in_stages = stripped.startswith("stages:")
            continue

        if in_stages:
            # 阶段项（缩进 2 的 `- name: xxx`）
            if indent == 2 and is_item:
                cur_stage = {"line": i, "name": None, "produces": [],
                             "required_in": [], "skill": None,
                             "checkpoint": None, "approval": None,
                             "review_focus": [], "sub_stages": []}
                stages.append(cur_stage)
                cur_sub, sub_indent = None, None
                body = ITEM.match(line).group(2).strip()
                kv = KV.match(body)
                if kv:
                    cur_stage[kv.group(2)] = kv.group(3).strip().strip('"')
                continue
            if cur_stage is None:
                continue

            # 子阶段项（比阶段项深 4 的 `- name: xxx`）
            if is_item:
                body = ITEM.match(line).group(2).strip()
                parent = recent_key(lines, i, indent - 2)
                if body.startswith("name:") and parent == "sub_stages":
                    cur_sub = {"line": i, "name": body.split(":", 1)[1].strip(),
                               "checkpoint_required": None,
                               "human_approval_default": None,
                               "review_focus": []}
                    cur_stage["sub_stages"].append(cur_sub)
                    sub_indent = indent
                    continue
                key = recent_key(lines, i, indent - 2)
                target = cur_sub if (cur_sub and parent is None) else cur_stage
                if key == "produces":
                    cur_stage["produces"].append(body)
                elif key == "required_artifacts_in":
                    cur_stage["required_in"].append(body)
                elif key == "review_focus":
                    (cur_sub if (cur_sub and sub_indent and indent > sub_indent)
                     else cur_stage)["review_focus"].append(body)
                continue

            # 键
            m = KV.match(line)
            if not m:
                continue
            k, v = m.group(2), m.group(3).strip()
            target = cur_sub if (cur_sub and sub_indent and indent > sub_indent) else cur_stage
            if k in ("checkpoint_required", "human_approval_default") and target is cur_sub:
                cur_sub[k] = v
            elif target is cur_stage:
                if k == "skill":
                    cur_stage["skill"] = v
                elif k == "checkpoint_required":
                    cur_stage["checkpoint"] = v
                elif k == "human_approval_default":
                    cur_stage["approval"] = v
            continue

        if in_skills and indent == 2 and is_item:
            skills.append(ITEM.match(line).group(2).strip())
    return top, stages, skills


def recent_key(lines, upto, indent):
    """往上找最近的同缩进键名，用来判断列表项归属。"""
    for j in range(upto - 2, -1, -1):
        line = lines[j].rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        ind = len(line) - len(line.lstrip(" "))
        if ind < indent:
            return None
        if ind == indent:
            m = KV.match(line)
            if m:
                return m.group(2)
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.exists(path):
        print("清单不存在:", path)
        return 1
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    top, stages, skills = parse(lines)
    print("清单:", os.path.relpath(path, ROOT))
    print("顶层键:", ", ".join(top))
    print("阶段数:", len(stages))
    print("登记技能数:", len(skills))

    print("\n--- 结构层 ---")
    for k in REQUIRED_TOP:
        check(k in top, "缺少顶层键 %s" % k)
    dup = [k for k in set(top) if top.count(k) > 1]
    check(not dup, "顶层键重复：%s" % dup)
    print("  %d 个问题" % len(problems))

    print("\n--- 契约层 ---")
    n0 = len(problems)
    all_produces = []
    seen = {}
    for st in stages:
        nm = st.get("name")
        check(nm, "第 %d 行的 stage 没有 name" % st["line"])
        if not nm:
            continue
        # 有人工门禁就要写审查关注点
        if str(st.get("approval")).lower() == "true":
            check(st["review_focus"],
                  "阶段 %s 有人工门禁但没写 review_focus（人不知道审什么）" % nm)
        # skill 必须登记
        if st.get("skill"):
            check(st["skill"] in skills,
                  "阶段 %s 的 skill「%s」未登记进 required_skills" % (nm, st["skill"]))
        # produces 不许跨阶段重名
        for a in st["produces"]:
            if a in seen:
                problems.append("工件 %s 被阶段 %s 与 %s 重复产出"
                                % (a, seen[a], nm))
            seen[a] = nm
            all_produces.append(a)

    # 拓扑：每个 required_artifacts_in 必须由**更早**的阶段产出
    for idx, st in enumerate(stages):
        earlier = set()
        for prev in stages[:idx]:
            earlier.update(prev["produces"])
        for need in st["required_in"]:
            if need not in earlier:
                problems.append("阶段 %s 依赖工件 %s，但前面的阶段都没有产出它"
                                % (st.get("name"), need))

    # 登记的技能文件必须真实存在
    missing_skill = []
    for s in skills:
        p = os.path.join(ROOT, "skills", s.replace("/", os.sep) + ".md")
        if not os.path.exists(p):
            missing_skill.append(s)
    if missing_skill:
        warnings.append("以下技能已登记但文件还没写（%d 个）：%s"
                        % (len(missing_skill), ", ".join(missing_skill[:6])))
    print("  %d 个问题，%d 个待办" % (len(problems) - n0, len(warnings)))

    print("\n--- 阶段总览 ---")
    for st in stages:
        gate = "人批" if str(st.get("approval")).lower() == "true" else "自动"
        subs = len(st.get("sub_stages") or [])
        print("  %-12s %-5s 产出 %-28s%s"
              % (st.get("name"), gate, ",".join(st["produces"]) or "-",
                 "  子阶段 %d" % subs if subs else ""))

    print("\n" + "=" * 64)
    if warnings:
        for w in warnings:
            print("待办:", w)
    if problems:
        for p in problems:
            print("问题:", p)
        print("共 %d 个问题 -> **有失败**" % len(problems))
        return 1
    print("共 0 个问题 -> 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
