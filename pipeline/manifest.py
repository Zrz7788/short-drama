# -*- coding: utf-8 -*-
"""流水线清单的解析与数据模型 —— 零依赖。

这台机器没有 PyYAML，而我们不想为读一个文件去装依赖。所以自己实现一个
**只覆盖我们清单用到的 YAML 子集**的解析器：

    嵌套映射 / 列表 / 列表项是映射 / 行内列表 [a, b] / 块标量 > 和 |
    注释（含行尾注释）/ 引号字符串 / 布尔与数字

刻意不做的：锚点别名、多文档、复杂键、流式映射 `{a: b}`。清单里没有，
一旦出现应当**报错而不是静默解析错**——静默解析错是这类工具最坏的失败方式。

用法：
    from pipeline.manifest import load
    m = load()
    m.stage("keyframes").requires_approval   # True
    m.stages_for_run()                       # 按顺序
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "pipeline_defs", "short-drama.yaml")


class ManifestError(Exception):
    """清单解析或校验失败。带行号，便于定位。"""


# ----------------------------------------------------------------- 解析

def strip_comment(s):
    """去掉行尾 YAML 注释。空白后的 `#` 才是注释起点（`a#b` 不是）。

    必须处理，否则 `- meta/x   # 说明` 会把注释当成值的一部分。
    实测踩过：校验器因此报出「技能文件缺失」的假警报。
    """
    out, quote, i = [], None, 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == quote:
                quote = None
            out.append(c)
        elif c in "\"'":
            quote = c
            out.append(c)
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def _split_commas(s):
    parts, depth, quote, cur = [], 0, None, []
    for c in s:
        if quote:
            if c == quote:
                quote = None
            cur.append(c)
        elif c in "\"'":
            quote = c
            cur.append(c)
        elif c in "[{":
            depth += 1
            cur.append(c)
        elif c in "]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def scalar(v):
    """把标量文本转成 Python 值。"""
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar_one(x) for x in _split_commas(inner)] if inner else []
    if v == "{}":
        return {}
    # 非空的流式映射我们没实现。**必须报错**——静默当字符串是这类解析器
    # 最坏的失败方式：清单看起来读进去了，实际是个错的值。
    if v.startswith("{") and v.endswith("}"):
        raise ManifestError("不支持非空的流式映射：%s（改写成缩进块）" % v[:40])
    return _scalar_one(v)


def _scalar_one(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v in ("true", "True", "yes", "on"):
        return True
    if v in ("false", "False", "no", "off"):
        return False
    if v in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+([eE][-+]?\d+)?", v):
        return float(v)
    return v


def _tokenize(text):
    """切成 (indent, 内容) 序列，丢掉空行与整行注释。"""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise ManifestError("第 %d 行含制表符，YAML 不允许 tab 缩进" % n)
        line = strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, line.strip(), n))
    return out


def _normalize(tokens):
    """把 `- key: value` 拆成 `-` + 更深缩进的 `key: value`。

    这样列表项是映射的情况就退化成普通的嵌套块，递归解析器不用特判。
    """
    out = []
    for ind, txt, n in tokens:
        if txt.startswith("- "):
            body = txt[2:].strip()
            if re.match(r"^[A-Za-z_][\w\-]*\s*:", body):
                out.append((ind, "-", n))
                out.append((ind + 2, body, n))
                continue
        out.append((ind, txt, n))
    return out


def _parse(tokens, i, indent):
    if i < len(tokens) and tokens[i][1].startswith("-"):
        return _parse_list(tokens, i, indent)
    return _parse_map(tokens, i, indent)


# 结构性行的判据：键值对、或列表项。注意只认 ASCII 冒号——
# 中文全角「：」在散文里很常见，认错会把折行的散文当成键。
_STRUCT = re.compile(r"^([A-Za-z_][\w\-]*\s*:|-\s|-$)")


def _is_continuation(tokens, i, parent_indent):
    """是不是上一行标量的折行续写。

    YAML 的 plain scalar 可以跨行：只要下一行更缩进、且不构成新的键或列表项。
    实测踩过——清单里「- 抽卡策略……（换行）全片一刀切是浪费」这种写法，
    没有这条会把合法折行报成缩进错误。
    """
    if i >= len(tokens):
        return False
    ind, txt, _n = tokens[i]
    return ind > parent_indent and not _STRUCT.match(txt)


def _consume_continuation(tokens, i, parent_indent, first):
    """收拢折行，用空格连接（YAML 的 folded 语义）。"""
    parts = [first]
    while _is_continuation(tokens, i, parent_indent):
        parts.append(tokens[i][1])
        i += 1
    return " ".join(parts), i


def _parse_map(tokens, i, indent):
    d = {}
    while i < len(tokens):
        ind, txt, n = tokens[i]
        if ind < indent:
            break
        if ind > indent:
            raise ManifestError("第 %d 行缩进异常（%d，期望 %d）：%s" % (n, ind, indent, txt[:50]))
        if txt.startswith("-"):
            break
        if ":" not in txt:
            raise ManifestError("第 %d 行不是键值对：%s" % (n, txt[:50]))
        key, _, val = txt.partition(":")
        key, val = key.strip(), val.strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            i += 1
            parts = []
            while i < len(tokens) and tokens[i][0] > indent:
                parts.append(tokens[i][1])
                i += 1
            joiner = " " if val.startswith(">") else "\n"
            d[key] = joiner.join(parts)
            continue
        if val == "":
            if i + 1 < len(tokens) and tokens[i + 1][0] > indent:
                sub, i = _parse(tokens, i + 1, tokens[i + 1][0])
                d[key] = sub
            else:
                d[key] = None
                i += 1
            continue
        text, i = _consume_continuation(tokens, i + 1, indent, val)
        d[key] = scalar(text)
    return d, i


def _parse_list(tokens, i, indent):
    out = []
    while i < len(tokens):
        ind, txt, n = tokens[i]
        if ind < indent:
            break
        if ind > indent:
            raise ManifestError("第 %d 行缩进异常（%d，期望 %d）：%s" % (n, ind, indent, txt[:50]))
        if not txt.startswith("-"):
            break
        body = txt[1:].strip()
        if body == "":
            if i + 1 >= len(tokens) or tokens[i + 1][0] <= indent:
                out.append(None)
                i += 1
                continue
            sub, i = _parse(tokens, i + 1, tokens[i + 1][0])
            out.append(sub)
            continue
        text, i = _consume_continuation(tokens, i + 1, indent, body)
        out.append(scalar(text))
    return out, i


def parse(text):
    tokens = _normalize(_tokenize(text))
    if not tokens:
        return {}
    if tokens[0][0] != 0:
        raise ManifestError("第 %d 行不是从第 0 列开始" % tokens[0][2])
    val, i = _parse(tokens, 0, 0)
    if i != len(tokens):
        raise ManifestError("第 %d 行之后有无法归属的内容：%s"
                            % (tokens[i][2], tokens[i][1][:50]))
    return val


# ----------------------------------------------------------------- 模型

def _as_dict(v, name):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ManifestError("%s 必须是映射，实际是 %s（检查缩进与流式写法）"
                            % (name, type(v).__name__))
    return v


class Stage:
    """一个阶段。属性直接来自清单，缺省值集中在这里，避免各处 get。"""

    def __init__(self, raw, parent=None):
        if not isinstance(raw, dict):
            raise ManifestError("阶段必须是映射，实际是 %r" % (raw,))
        self.raw = raw
        self.parent = parent
        self.name = raw.get("name") or ""
        self.skill = raw.get("skill") or ""
        self.description = raw.get("description") or ""
        self.produces = list(raw.get("produces") or [])
        self.required_in = list(raw.get("required_artifacts_in") or [])
        self.optional_in = list(raw.get("optional_artifacts_in") or [])
        self.tools = list(raw.get("tools_available") or [])
        self.review_focus = list(raw.get("review_focus") or [])
        self.success_criteria = list(raw.get("success_criteria") or [])
        self.governance = list(raw.get("governance") or [])
        self.checkpoint_required = bool(raw.get("checkpoint_required", True))
        self.human_approval = bool(raw.get("human_approval_default", False))
        self.substages = [Stage(s, self) for s in (raw.get("sub_stages") or [])]

    @property
    def key(self):
        """唯一标识，子阶段带上父阶段名。"""
        return "%s/%s" % (self.parent.name, self.name) if self.parent else self.name

    def __repr__(self):
        gate = "人批" if self.human_approval else "自动"
        return "<Stage %s %s → %s>" % (self.name, gate, ",".join(self.produces))


class Manifest:
    def __init__(self, raw, path=None):
        if not isinstance(raw, dict):
            raise ManifestError("清单顶层必须是映射，实际是 %s" % type(raw).__name__)
        self.raw = raw
        self.path = path
        self.name = raw.get("name") or ""
        self.version = str(raw.get("version") or "")
        self.description = raw.get("description") or ""
        # 类型检查放在这里：清单写错时给人一句能看懂的话，
        # 而不是让它到某个 .get() 上炸成 AttributeError。
        self.models = _as_dict(raw.get("models"), "models")
        orch = _as_dict(raw.get("orchestration"), "orchestration")
        self.budget_default = orch.get("budget_default_cny")
        self.hard_cap = orch.get("hard_cap_cny")
        self.max_revisions = orch.get("max_revisions_per_stage", 3)
        self.max_send_backs = orch.get("max_send_backs", 3)
        self.required_skills = list(raw.get("required_skills") or [])
        raw_stages = raw.get("stages")
        if raw_stages is not None and not isinstance(raw_stages, list):
            raise ManifestError("stages 必须是列表，实际是 %s" % type(raw_stages).__name__)
        self.stages = [Stage(s) for s in (raw_stages or [])]

    def stage(self, name):
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError("清单里没有阶段 %s" % name)

    def find(self, key):
        """按 `阶段` 或 `阶段/子阶段` 取。"""
        if "/" in key:
            parent, sub = key.split("/", 1)
            for s in self.stage(parent).substages:
                if s.name == sub:
                    return s
            raise KeyError("阶段 %s 下没有子阶段 %s" % (parent, sub))
        return self.stage(key)

    @property
    def stage_names(self):
        return [s.name for s in self.stages]

    def run_units(self):
        """把流水线展平成一串**运行单元**，按执行顺序。

        有子阶段的阶段（art 的 characters/scenes/props、motion 的 draft/direct/final）
        本身不作为门禁单位——真正的门在子阶段上。父阶段只提供产出与职责说明。
        没有子阶段的阶段自己就是单元。
        """
        units = []
        for s in self.stages:
            if s.substages:
                units.extend(s.substages)
            else:
                units.append(s)
        return units

    def unit(self, key):
        """按 `阶段` 或 `阶段/子阶段` 取运行单元。"""
        for u in self.run_units():
            if u.key == key or (u.parent is None and u.name == key):
                return u
        raise KeyError("没有这个运行单元：%s" % key)

    @property
    def gate_names(self):
        """需要人工批准的所有**运行单元**。

        按运行单元而不是按阶段算——`art` 这种有子阶段的父阶段本身不是门，
        真正的门在 `art/characters` / `art/scenes` / `art/props` 上。
        """
        return [u.key for u in self.run_units() if u.human_approval]

    def produced_by(self, name):
        """哪个阶段产出这个工件。"""
        for s in self.stages:
            if name in s.produces:
                return s.name
        return None

    def validate(self):
        """契约校验。返回 (problems, warnings)。"""
        problems, warnings = [], []
        for key in ("name", "version", "orchestration", "stages"):
            if key not in self.raw:
                problems.append("缺少顶层键 %s" % key)
        if not self.stages:
            problems.append("没有任何阶段")

        seen = {}
        for s in self.stages:
            if not s.name:
                problems.append("有阶段没有 name")
                continue
            if s.human_approval and not s.review_focus:
                problems.append("阶段 %s 有人工门禁但没写 review_focus（人不知道审什么）"
                                % s.name)
            if s.skill and s.skill not in self.required_skills:
                problems.append("阶段 %s 的 skill「%s」未登记进 required_skills"
                                % (s.name, s.skill))
            for a in s.produces:
                if a in seen:
                    problems.append("工件 %s 被阶段 %s 与 %s 重复产出"
                                    % (a, seen[a], s.name))
                seen[a] = s.name
            for sub in s.substages:
                if not sub.name:
                    problems.append("阶段 %s 下有子阶段没有 name" % s.name)

        # 拓扑：依赖的工件必须由更早的阶段产出
        for idx, s in enumerate(self.stages):
            earlier = set()
            for prev in self.stages[:idx]:
                earlier.update(prev.produces)
            for need in s.required_in:
                if need not in earlier:
                    problems.append("阶段 %s 依赖工件 %s，但前面的阶段都没有产出它"
                                    % (s.name, need))

        missing = [k for k in self.required_skills
                   if not os.path.exists(os.path.join(
                       ROOT, "skills", k.replace("/", os.sep) + ".md"))]
        if missing:
            warnings.append("以下技能已登记但文件还没写（%d 个）：%s"
                            % (len(missing), ", ".join(missing[:6])))
        return problems, warnings


_cache = {}


def load(path=None, use_cache=True):
    path = path or DEFAULT_PATH
    if use_cache and path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        raise ManifestError("清单不存在：%s" % path)
    with open(path, encoding="utf-8") as f:
        raw = parse(f.read())
    m = Manifest(raw, path)
    if use_cache:
        _cache[path] = m
    return m


if __name__ == "__main__":
    import sys
    m = load()
    print("清单 %s v%s —— %d 个阶段" % (m.name, m.version, len(m.stages)))
    print("单集预算 ¥%s  硬顶 ¥%s  每阶段最多改 %s 次"
          % (m.budget_default, m.hard_cap, m.max_revisions))
    print("\n%-14s %-5s %-34s %s" % ("阶段", "门禁", "产出", "子阶段"))
    for s in m.stages:
        print("%-14s %-5s %-34s %s"
              % (s.name, "人批" if s.human_approval else "自动",
                 ",".join(s.produces) or "-",
                 ",".join(x.name for x in s.substages) or "-"))
    probs, warns = m.validate()
    print("\n需要人工批准的门（%d 道）：%s" % (len(m.gate_names), ", ".join(m.gate_names)))
    for w in warns:
        print("待办:", w)
    for p in probs:
        print("问题:", p)
    sys.exit(1 if probs else 0)
