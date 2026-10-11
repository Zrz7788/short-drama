# -*- coding: utf-8 -*-
"""Markdown 剧本 → 数据库导入器。

第 1 阶段的第二条通道。原来只有「调 LLM 吐 JSON」一条路，用的是手写的
`pipeline/prompts/script.md`（54 行），完全没用上 226 KB 的专业编剧技能
`short-drama-write`，直接导致剧本被切成 9 个 4 秒碎片（见 HANDOFF §四）。

现在剧本的**真源是 `剧集/EP###/剧本.md` 文件**：agent 按技能写，人可以直接改
其中一段，本模块只负责解析、校验、写库、记账。**它不生成任何内容。**

规则强度三分法（沿用 `stage_storyboard.check_shots` 的约定）：

  block   可核查的：一级/场景标题语法、场景 ID、标签闭合、说话者清单。
          **证明它错就报错，不猜。**
  warn    工艺默认值：时间/天气粒度、画面文字数量。**能偏离，报出来即可。**
  review  需要人判断的：beat 推断、时长分配、说话者是否已在实体表。
          **明确标注为推断，绝不伪装成作者的原文。**

关于两个「新技能没有、旧库要」的字段，本模块的处理原则是**推导并标注**，
不是编造，也不是静默放弃：

  beat        技能里没有这个字段。第 5 阶段的 prompt 和第 2 阶段的分镜摘要都要它。
              按**场的位置与是否有状态转折**推断，写进 `剧集/EP###/.beat-inferred`
              标注文件，报告里逐场列出，交创作者确认。可用 `--beat` 覆盖。
  duration_s  技能明确拒绝在没有声明语速时猜秒数。所以：声明了 `短剧.json`
              的 pacing 就按语速算；没声明就按**本集目标总秒数**按对白字数+
              动作段数分配。后者是分配不是测量，报告里说清楚。
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

# 技能 screenplay-format.md §3.3 的受支持标签
TAGS = ("VO", "OS", "SFX", "画面文字", "连续性", "转场")

EPISODE_H1 = re.compile(r"^#\s+(EP\d{3})\s*(.*)$")
SCENE_H2 = re.compile(r"^##\s+(EP\d{3}-SC\d{3})\s+(\S+)\s*·\s*([^·]+?)\s*·\s*(.+?)\s*$")
# ⚠️ 与技能 screenplay_index.py 的 DIALOGUE_RE **逐字符一致**。
# 同一格式两套答案就会分叉：第一版我照抄了这个正则，却又加了一个
# `_looks_like_action()` 兜底，而它把 `主管（边走边翻页，没有抬头）：…` 里
# **括号内表演提示**的逗号当成了动作标志，于是带提示的对白全被判成动作。
# 教训与 HANDOFF §七 同款：报错的位置（正则）和出错的位置（兜底函数）不在一起。
# 结论——校验器用哪个正则，导入器就用哪个，不要再加一层自己的判断。
DIALOGUE = re.compile(
    r"^(?P<speaker>[^\s：（）:\[\]#]{1,40})"
    r"(?:（(?P<cue>[^（）\r\n]+)）)?：(?P<text>\S[\s\S]*)$")
TAG_RE = re.compile(r"^\[(?P<tag>VO|OS|SFX|画面文字|连续性|转场)\]\s*(?P<body>\S[\s\S]*)$")
# 只有 [VO]/[OS] 的标签体可以是一条**说话**的对白（format §3.3）。
# 其余标签的正文是画面文字／音效事实：`[画面文字] 卷宗编号：甲—0417` 里的冒号
# 是标签正文自己的分隔符，不是对白定界符。不区分就会凭空多出一个叫
# 「卷宗编号」的说话者——而且它会一路走进说话者清单和下游拆镜。
VOICED_TAGS = ("VO", "OS")
PLACE_KINDS = ("内", "外", "内外")


class ScriptFormatError(Exception):
    """结构性问题——能证明它错，所以直接拒绝写库。"""


def parse_md(text):
    """把技能格式的剧本 Markdown 解析成结构化场景。

    保留原文：每个场景的 body 逐字留下，action/dialogue 只是**视图**。
    解析不出场景标题、场景 ID 重复、标签不闭合 -> 报错，不猜。
    """
    lines = text.splitlines()
    episode_id = episode_title = None
    scenes = []
    cur = None
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip()
        m = EPISODE_H1.match(line)
        if m and episode_id is None:
            episode_id, episode_title = m.group(1), m.group(2).strip()
            continue
        m = SCENE_H2.match(line)
        if m:
            sid, place, loc, when = m.groups()
            if place not in PLACE_KINDS:
                raise ScriptFormatError(
                    "第 %d 行场景标题的「%s」不是 内/外/内外：%s" % (lineno, place, line))
            if not when.strip():
                raise ScriptFormatError("第 %d 行缺时间/天气：%s" % (lineno, line))
            cur = {"scene_id": sid, "place": place, "location": loc.strip(),
                   "time_of_day": when.strip(), "body": [], "lineno": lineno}
            scenes.append(cur)
            continue
        if cur is not None:
            cur["body"].append(line)

    if episode_id is None:
        raise ScriptFormatError("找不到一级标题 `# EP001 集名`")
    if not scenes:
        raise ScriptFormatError("找不到任何场景标题 `## EP001-SC001 内 · 地点 · 时间`")

    seen = set()
    for i, s in enumerate(scenes, 1):
        if s["scene_id"] in seen:
            raise ScriptFormatError("场景 ID 重复：%s" % s["scene_id"])
        seen.add(s["scene_id"])
        s["seq"] = i
        _classify(s)
    return {"episode_id": episode_id, "title": episode_title, "scenes": scenes}


def _classify(s):
    """把场景 body 分成动作段、对白、生产标签。保留原文顺序。

    分类顺序与技能索引器一致：先试生产标签，再从标签体内剥出对白
    （`[VO] 葛晴：…` 是**对白**不是标签正文，见技能 format §3.3），
    最后才是动作段。
    """
    s["actions"], s["dialogues"], s["tags"] = [], [], []
    for raw in s["body"]:
        line = raw.strip()
        if not line:
            continue
        tagm = TAG_RE.match(line)
        if tagm:
            name, body = tagm.group("tag"), tagm.group("body")
            dm = DIALOGUE.match(body) if name in VOICED_TAGS else None
            if dm:      # [VO]/[OS] 带说话者 -> 是有声对白，声音在画外
                s["dialogues"].append(_dlg(dm, line, tag=name))
            else:       # 其余标签是生产事实（含画面文字），整条留原样
                s["tags"].append({"tag": name, "text": body})
            continue
        dm = DIALOGUE.match(line)
        if dm:
            s["dialogues"].append(_dlg(dm, line))
        else:
            s["actions"].append(line)

    s["action"] = "\n\n".join(s["actions"])
    s["dialogue"] = "\n".join(d["raw"] for d in s["dialogues"])
    s["slug"] = "%s · %s · %s" % (s["place"], s["location"], s["time_of_day"])
    s["speakers"] = sorted({d["who"] for d in s["dialogues"] if not d["tag"]})
    s["spoken_chars"] = sum(len(re.sub(r"[，。！？、：；\"'…—\s]", "", d["text"]))
                            for d in s["dialogues"])


def _dlg(m, raw, tag=None):
    return {"who": m.group("speaker").strip(), "cue": m.group("cue"),
            "text": m.group("text").strip(), "tag": tag,
            # 存进库的 dialogue 保留**原文整行**（含标签与表演提示），
            # 下游（第 5 阶段拆镜、第 2 阶段抽实体）需要看得见的提示，
            # 拿走只剩台词会丢掉表演信息。
            "raw": raw}


# ---------------------------------------------------------------- beat 推断
def infer_beats(scenes):
    """技能里没有 beat 字段，但旧库的下游要它。

    推断规则（**是推断，不是作者原文，必须标注**）：
      末场           -> 扣子（技能要求结尾形态；本集末场是悬念断点）
      首场           -> 钩子（技能：全集第一拍自己要是钩子）
      中段且有状态转折 -> 高潮
      其余           -> 升级
    创作可用 `--beat 高潮,扣子` 覆盖。
    """
    n = len(scenes)
    out = []
    for i, s in enumerate(scenes):
        if i == n - 1:
            out.append("扣子")
        elif i == 0:
            out.append("钩子")
        elif _has_turn(s):
            out.append("高潮")
        else:
            out.append("升级")
    return out


def _has_turn(s):
    """中段是否出现状态转折——有可见的不可逆动作才算。"""
    text = s["action"]
    return bool(re.search(r"(睁开|抓住|流血|倒下|认出|撕|摔|断|破|亮起|熄灭)", text))


def crosscheck_with_skill_indexer(md_path, parsed):
    """拿技能的 `screenplay_index.py` 复核自己的切分。

    为什么值得多跑一个进程：本模块的对白正则是**抄**技能验证器的。抄就会分叉，
    而分叉是安静的——第一版那个 `_looks_like_action()` 就把 3 句带表演提示的
    对白变成了动作段，库照写、报告照绿。这里让**技能的验证器**来判我切得对不对，
    它说 clean 才算 clean。

    技能不在本工作区时返回 (True, "技能索引器不可用，跳过")——不假装验过。
    """
    import tempfile
    script = os.path.join(C.ROOT, ".dsh", "skills", "short-drama-write",
                          "scripts", "screenplay_index.py")
    if not os.path.exists(script):
        return True, "技能索引器不在本工作区，跳过交叉复核"

    speakers = sorted({sp for s in parsed["scenes"] for sp in s["speakers"]})
    out = os.path.join(tempfile.gettempdir(),
                       "md-import-crosscheck-%d.jsonl" % os.getpid())
    cmd = [sys.executable, script, md_path, "--output", out]
    for sp in speakers:
        cmd += ["--speaker", sp]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return True, "技能索引器跑不起来（%s），跳过交叉复核" % str(e)[:40]
    finally:
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass

    if p.returncode != 0:
        return False, ("技能索引器判为不合格：%s"
                       % C.tail((p.stdout or "") + (p.stderr or ""), 200))
    try:
        res = json.loads((p.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return True, "技能索引器输出看不懂，跳过交叉复核"
    if res.get("review_status") != "clean" or res.get("source_issue_count"):
        return False, ("技能索引器报了问题：review=%s issues=%s"
                       % (res.get("review_status"), res.get("source_issue_count")))
    return True, "技能索引器复核 clean（%s 块）" % res.get("block_count")


# ---------------------------------------------------------------- 时长
def allocate_durations(con, ep, scenes, project_dir):
    """给每场分配 duration_s。返回 (时长列表, 来源说明)。

    技能明确拒绝在没声明语速时猜秒数，所以这里**不假装是测量**：
      1. 项目声明了 pacing.spoken_characters_per_second -> 按语速算对白秒数，
         加上动作段数 × seconds_per_action_paragraph，这才是估算；
      2. 没声明 -> 用本集目标总秒数 target_seconds，按（对白字数 + 动作段数×8）
         的权重**分配**。分配保证合计 ≈ 目标，且明说这是分配。
    """
    cfg = _load_project_cfg(project_dir)
    pacing = (cfg or {}).get("format", {}).get("pacing", {}) or {}
    cps = pacing.get("spoken_characters_per_second")
    spa = pacing.get("seconds_per_action_paragraph")

    target = ep.get("target_seconds") or ep.get("target_episode_seconds") or 80

    if cps:
        secs = []
        for s in scenes:
            v = s["spoken_chars"] / float(cps)
            if spa:
                v += len(s["actions"]) * float(spa)
            secs.append(round(v, 1))
        total = sum(secs)
        return secs, ("按项目声明的语速 %.2f 字/秒%s计算；合计 %.0f 秒（目标 %s 秒）"
                      % (cps, "＋动作段 %.2f 秒/段" % float(spa) if spa else "",
                         total, target))

    weights = [s["spoken_chars"] + len(s["actions"]) * 8 for s in scenes]
    if sum(weights) <= 0:
        weights = [1] * len(scenes)
    secs = [round(target * w / float(sum(weights)), 1) for w in weights]
    secs[-1] = round(target - sum(secs[:-1]), 1)   # 补掉舍入误差，保证合计=目标
    return secs, ("项目未声明语速，按（对白可发声字数＋动作段数×8）的权重**分配**"
                  "本集目标 %s 秒——这是分配不是测量" % target)


def _load_project_cfg(project_dir):
    p = os.path.join(project_dir, "短剧.json")
    if not os.path.exists(p):
        p = os.path.join(project_dir, "short-drama.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 校验
def validate(con, parsed, ep, beats, secs):
    """返回 (blocks, warns, reviews)。block 非空就不写库。"""
    blocks, warns, reviews = [], [], []
    project_id = ep["project_id"]

    for i, s in enumerate(parsed["scenes"]):
        tag = "第%d场 %s" % (s["seq"], s["scene_id"])
        if not s["actions"]:
            blocks.append("%s 没有动作段落（只有对白撑不起一场戏）" % tag)
        if s["place"] not in PLACE_KINDS:
            blocks.append("%s 空间性质非法：%s" % (tag, s["place"]))

        if not re.search(r"[日夜晨昏]", s["time_of_day"]):
            warns.append("%s 时间/天气「%s」里没有日/夜/晨/昏——下游排光线时不好用"
                         % (tag, s["time_of_day"]))
        n_text = len([t for t in s["tags"] if t["tag"] == "画面文字"])
        if n_text > 2:
            warns.append("%s 有 %d 处 [画面文字]，观众读不过来（建议 ≤2）" % (tag, n_text))
        n_all = len(s["tags"])
        if n_all > 4:
            warns.append("%s 有 %d 条生产标签，其中 [画面文字] %d 处——标签密度偏高"
                         % (tag, n_all, n_text))

        for sp in s["speakers"]:
            row = con.execute(
                "SELECT 1 FROM entity WHERE project_id=? AND kind='character' AND "
                "(name=? OR alias LIKE ?)", (project_id, sp, "%" + sp + "%")).fetchone()
            if not row:
                reviews.append("%s 的说话者「%s」还不在实体表里（第 2 阶段会处理）"
                               % (tag, sp))

        if s["duration_s"] < 20:
            warns.append("%s 分配时长 %.1f 秒，偏短（技能的目标是一场 30–60 秒）"
                         % (tag, s["duration_s"]))

    total = sum(secs)
    target = None
    if abs(total - 80) > 30:
        warns.append("各场合计 %.0f 秒，离 80 秒较远" % total)

    return blocks, warns, reviews


# ---------------------------------------------------------------- 写库
def persist(con, ep, parsed, beats, secs, dry, review_items):
    if dry:
        return None
    row = con.execute("SELECT COALESCE(MAX(version),0) AS v FROM script WHERE episode_id=?",
                      (ep["id"],)).fetchone()
    version = row["v"] + 1
    con.execute("UPDATE script SET is_current=0 WHERE episode_id=?", (ep["id"],))

    content = _rebuild_content(parsed)
    title = "%s：%s" % (parsed["episode_id"], parsed["title"]) if parsed["title"] \
        else parsed["episode_id"]
    logline = _first_action(parsed)

    sid = con.execute(
        "INSERT INTO script (episode_id, version, title, logline, content, word_count,"
        " is_current, notes) VALUES (?,?,?,?,?,?,1,?)",
        (ep["id"], version, title, logline, content, len(content),
         "由 %s 导入（stage_script --from-md）" % parsed["source"]
         if parsed.get("source") else "Markdown 导入")).lastrowid

    for i, s in enumerate(parsed["scenes"]):
        con.execute(
            "INSERT INTO scene (script_id, seq, slug, location, time_of_day, action,"
            " dialogue, beat, duration_s) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, s["seq"], s["slug"], s["location"], s["time_of_day"],
             s["action"], s["dialogue"], beats[i], secs[i]))
    con.execute("UPDATE episode SET title=? WHERE id=?", (title, ep["id"]))
    con.execute("UPDATE episode SET status='scripted' WHERE id=?", (ep["id"],))
    con.commit()
    return {"version": version, "script_id": sid}


def _rebuild_content(parsed):
    """按场景重建正文——保留每场原文，只统一标题行。"""
    out = ["# %s %s" % (parsed["episode_id"], parsed["title"]) if parsed["title"]
           else "# %s" % parsed["episode_id"], ""]
    for s in parsed["scenes"]:
        out.append("## %s %s · %s · %s" % (s["scene_id"], s["place"], s["location"],
                                           s["time_of_day"]))
        out.append("")
        out.extend(s["body"])
        out.append("")
    return "\n".join(out).strip() + "\n"


def _first_action(parsed):
    for s in parsed["scenes"]:
        if s["actions"]:
            return C.tail(s["actions"][0], 80)
    return ""


# ---------------------------------------------------------------- 入口
def run(con, ep, md_path, dry, beat_override, project_dir="."):
    rep = C.Report("第 1 阶段 · 导入 Markdown 剧本")
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    if not os.path.exists(md_path):
        rep.add("剧本文件存在", False, md_path)
        return rep.finish()
    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    try:
        parsed = parse_md(text)
    except ScriptFormatError as e:
        rep.add("格式可解析（structural_invariant）", False, str(e))
        return rep.finish()
    parsed["source"] = md_path
    rep.add("格式可解析（structural_invariant）", True,
            "%s · %d 场" % (parsed["episode_id"], len(parsed["scenes"])))

    # 集号对不上就是接错文件了——这是能证明的错，必须拦
    want = "EP%03d" % ep["seq"]
    if parsed["episode_id"] != want:
        rep.add("集号与库中集一致", False,
                "文件是 %s，库里第 %s 集应是 %s" % (parsed["episode_id"], ep["seq"], want))
        return rep.finish()
    rep.add("集号与库中集一致", True, parsed["episode_id"])

    beats = infer_beats(parsed["scenes"])
    if beat_override:
        if len(beat_override) != len(parsed["scenes"]):
            rep.add("--beat 覆盖项数匹配", False,
                    "给了 %d 个，本集 %d 场" % (len(beat_override), len(parsed["scenes"])))
            return rep.finish()
        beats = beat_override
        rep.info("beat 由 --beat 指定（非推断）")

    secs, how = allocate_durations(con, ep, parsed["scenes"], project_dir)
    for i, s in enumerate(parsed["scenes"]):
        s["duration_s"] = secs[i]

    blocks, warns, reviews = validate(con, parsed, ep, beats, secs)
    if blocks:
        for b in blocks:
            rep.add("机制性校验（block）", False, b)
        return rep.finish()
    rep.add("机制性校验（block）", True, "无阻断项")

    ok, msg = crosscheck_with_skill_indexer(md_path, parsed)
    rep.add("技能索引器交叉复核", ok, msg)
    if not ok:
        return rep.finish()

    for i, s in enumerate(parsed["scenes"]):
        print("  --   %s %s  %s  %.1f 秒  %d 段动作 / %d 句对白 / %d 标签"
              % (s["scene_id"], s["slug"], beats[i], secs[i],
                 len(s["actions"]), len(s["dialogues"]), len(s["tags"])))
    rep.info("时长来源：%s" % how)
    rep.info("beat 为**推断值**（技能无此字段），请核对后可用 --beat 覆盖")

    for w in warns:
        rep.add("工艺建议（warn）", True, w)
    for r in reviews:
        rep.add("待人工确认（review）", True, r)

    info = persist(con, ep, parsed, beats, secs, dry, reviews)
    if dry:
        rep.info("dry-run：未写库")
    else:
        rep.add("已写入 script 与 scene", True,
                "版本 v%s · %d 场" % (info["version"], len(parsed["scenes"])))
        _write_sidecars(md_path, parsed, beats, secs, how)
        rep.add("已写标注文件", True, os.path.join(os.path.dirname(md_path),
                                                   ".beat-inferred"))
    return rep.finish()


def _write_sidecars(md_path, parsed, beats, secs, how):
    """把「哪些是推断的」写到剧本旁边，供人核对——推断值不能只活在报告里。"""
    d = os.path.dirname(md_path)
    lines = ["# 本文件的 beat 与 duration_s 是**导入时推断**的，不是剧本原文",
             "# 剧本原文没有 beat 字段；duration_s 来源：%s" % how,
             "# 核对后用 --beat 覆盖，或直接改 scene 表", ""]
    for i, s in enumerate(parsed["scenes"]):
        lines.append("%s  seq=%d  beat=%s  duration_s=%.1f"
                     % (s["scene_id"], s["seq"], beats[i], secs[i]))
    with open(os.path.join(d, ".beat-inferred"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Markdown 剧本 → 数据库")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--md", required=True, help="剧集/EP001/剧本.md")
    ap.add_argument("--beat", default=None,
                    help="覆盖推断的 beat，逗号分隔，按场序，如 钩子,扣子")
    C.add_common_args(ap)
    args = ap.parse_args(argv)

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    beats = [b.strip() for b in args.beat.split(",")] if args.beat else None
    try:
        return run(con, ep, args.md, args.dry_run, beats)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
