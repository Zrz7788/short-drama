# -*- coding: utf-8 -*-
"""第 3 / 4 阶段：生成参考图（人物素材 / 场景素材 / 道具素材）。

三个子阶段共用一个脚本，用 --phase 区分——流程完全相同，只是
「给谁出图」和「出哪些视角」不同：

  第 3 阶段  --phase characters  人物素材：大头照 + 全身照
  第 4 阶段  --phase scenes      场景素材：正视图 + 俯视图 + 站位图
             --phase props       道具素材：孤立照片

分级的成本含义（按 kind 不同）：
  character  main=2 张 / secondary=1 张 / background=0 张
  location   main=3 张 / secondary=2 张 / background=0 张
  prop       main=1 张 / secondary=1 张 / background=0 张

**派生视角以基底视角为参考图生成（图生图）**，这是刻意的：纯文生图无法保证
是同一个实体。顺序是「全身照 → 大头照」「正视图 → 俯视图/站位图」——
基底是锚点，派生从锚点长出来。

⚠️ **本脚本原来出 front/side/back「三视图」，已废弃**（官方明确会引发双胞胎）。
文件头下方有完整说明。

用法：
  python pipeline/stage_refs.py --episode-id 1 --phase characters
  python pipeline/stage_refs.py --episode-id 1 --phase scenes
  python pipeline/stage_refs.py --episode-id 1 --phase characters --dry-run
  python pipeline/stage_refs.py --episode-id 1 --phase characters --only 林薇 --force
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

TPL = os.path.join(C.ROOT, "pipeline", "prompts", "portrait.md")

# ------------------------------------------------------------------ 素材体系
#
# ⚠️ **这里做过一次重构。** 原来第 4 阶段出 front/side/back「三视图」，
# 依据用户提供的《拾音人第一集》工艺规范，那是**官方明确禁止**的做法：
#
#   ❌ 禁止使用人物三视图或多视图作为参考素材——官方明确会导致模型识别为
#      多个不同主体，**直接引发"双胞胎问题"**。
#
# 三个独立来源指向同一件事（OpenMontage「脸只出现在一张上」、《真人质感》
# 「把脸部视图与全身图分开」、拾音人「直接禁用三视图」）。
#
# 重构后的体系把**人物与场景分开**——它们本来就是两种不同的参考物：
#
#   人物（每 main 角色 2 张）
#     headshot  大头照：仅面部、无表情、无肩颈、无背景、正面平光、高清。
#               用途是**锁死面部细节**；面部大特写镜**只加载这一张**。
#     fullbody  全身照：完整妆造，部件级（内层/外层/下装/鞋/配饰）。
#               用途是**妆造与体态参考**。
#
#   场景（每个 location）
#     scene_front  正视图：环境本身
#     blocking     站位图：颜色标记人偶 + 位置说明，用于空间调度
#     scene_top    俯视图：空间关系（只给需要走位调度的核心场景）
#
# 提示词里的引用分工（照《拾音人》规范）：
#   「面部特征参考图片1（大头照），妆造参考图片2（全身照）」
#   且 **@图片1 必须放在提示词最开头**。

HEADSHOT = ("headshot", "大头照",
            "仅面部特写，正面平视镜头，**不出现肩颈与以下部位，不出现背景**，"
            "中性无表情，正面均匀平光（无侧光、无轮廓光、无阴影），"
            "背景纯白，超高清，皮肤纹理与毛孔清晰可见。")
FULLBODY = ("fullbody", "全身照",
            "完整全身照，正面站立，完整展示内层衣物、外层衣物、下装、鞋履与"
            "全部配饰；四肢完整入镜，不裁切；自然站姿，平视机位，"
            "柔和均匀照明，中性表情。")

SCENE_FRONT = ("scene_front", "场景正视图",
               "正面平视视角的完整场景，无人物，自然光照，"
               "空间结构与纵深清楚。")
SCENE_TOP = ("scene_top", "场景俯视图",
             "约 30 度俯视视角的完整场景，无人物，"
             "清楚交代空间布局、通道走向与家具摆位。")
BLOCKING = ("blocking", "站位图",
            "在场景图上用**半透明白膜人偶**标出人物站位，"
            "每个人偶旁标注编号与角色名，用**明显不同的颜色**区分人偶，"
            "人偶为纯白无贴图模型（不显示五官与服装细节）。"
            "此图仅作站位参考。"
            "**最终画面里不得出现任何线框、编号与文字标记。**")

# ---------------------------------------------------------------- 子空间
#
# 依据《8、场景穿越》：「**先生成发生具体剧情的子空间，再通过子空间扩图到
# 大场景**」。原文正确顺序是**从小到大**，不是先画大场景——
# 因为**模型的空间推理能力有限**（原文实测：「SD 很难通过异常场景图，
# 精准识别到远处的拱门」「SD 很难通过内外两张图，做好空间衔接」）。
#
# 原表里这条被列为"应改"，但一直没落地；今天用户指出三间房各长各的，
# 正是同一个根因。
#
# 为什么把**最终构图**放进去：扩图指令照原文写的是
# 「将参考图放在中间黄金分割点，向四周扩图…**保持参考图中间的包间不变**」——
# "中间不变"要求子空间在画面中央，否则扩完会被裁掉或偏移。
# 所以我们不是给一块孤立特写，而是**给一个已经按最终构图摆好的小空间**。
SCENE_SUBSPACE = ("scene_subspace", "子空间",
                  "剧情真正发生的那一小块空间，**画面中央**，"
                  "四周留出可向外扩展的余量；构图为**最终镜头的取景**"
                  "（该场景出现在哪些镜头里，就按那些镜头的视角来定）；"
                  "无人物，自然光照，材质与色彩写实；"
                  "**画面分前景与后景两层**——前景放贴近镜头的一两件具体物体"
                  "（占据画面下缘约三成），后景交代通道或墙面走向，"
                  "使空间有纵深、不像一张平片。")

# 同场景的**不同时段光影版本**（依《6、女频氛围感》）。
# 依据：同一场景只做一个时段，运镜一换时段光影就接不上；
# 而"避免日夜混光"也是同一条的下半句——**同一条里不能日夜混着来**。
#
# ⚠️ 本项目第 1 集是**同一场秋晨**，所以只出 time_dawn 一版；
#    这个常量是为后面跨越时段的集准备的（登记即为落地）。
SCENE_LIGHT_VARIANTS = ("time_dawn", "time_dusk")
SCENE_LIGHT_DESC = {
    "time_dawn": "清晨冷调自然光，色温偏低、略偏青，光线从上方偏侧进入，"
                 "阴影柔和、方向一致，整体明亮但不刺眼。",
    "time_dusk": "黄昏暖调自然光，色温偏高、偏橙，光线低角度斜射，"
                 "阴影拉长，明暗对比强于清晨，整体偏暖。",
}

# 扩图说明（i2i）。**是否启用由实体自己的开关决定**：
# 只有当子空间画面里**能看到房间结构**（墙面/门/顶/地）时才扩得动；
# 画面里本来就没有的东西，模型变不出来。
SCENE_EXPAND_DESC = (
    "以参考图为**画面中央的主体**保持不变，向四周扩图，"
    "展示该空间的完整环境；保持参考图里已有的材质、色调、光源方向与"
    "空间结构完全不变，只向外补全墙、门、天花、地面与周边陈设。"
    "**画面分前景、中景、后景三层**，前景留具体物体、后景交代空间纵深。")

# 人物素材：main 出两张，secondary 只出全身照（不会有面部大特写）
CHARACTER_VIEWS = {
    "main": [HEADSHOT, FULLBODY],
    "secondary": [FULLBODY],
    "background": [],
}
# 场景素材：核心场景出四张（含子空间），一次性场景出三张
SCENE_VIEWS = {
    "main": [SCENE_SUBSPACE, SCENE_FRONT, BLOCKING, SCENE_TOP],
    "secondary": [SCENE_SUBSPACE, SCENE_FRONT, BLOCKING],
    "background": [],
}

# 工具素材（道具）：一张即可
PROP_VIEW = ("prop_ref", "道具参考图",
             "道具的清晰孤立照片，纯白背景，正面视角，"
             "材质、磨损、颜色与尺寸比例清楚可辨。")

# 每个实体类型的**主参考图**（标记 is_primary=1）。
# 选"第一个生成的那张"：人物是全身照（大头照以它为参考），
# **场景是子空间**（正视图以扩图方式从它来），道具只有一张。
PRIMARY_VIEW = {"character": "fullbody", "location": "scene_subspace",
                "prop": "prop_ref"}

# 各阶段给哪些 tier 出图
PHASE_TIERS = {"characters": ("main", "secondary"),
               "scenes": ("main", "secondary"),
               "props": ("main", "secondary")}

# 各阶段管哪一类实体（entity.kind）
PHASE_KIND = {"characters": "character", "scenes": "location", "props": "prop"}


def entity_block(e):
    body = (e["description"] or "").strip()
    if e["style_note"] and e["style_note"].strip():
        body += "（%s）" % e["style_note"].strip()
    return "【%s·%s】%s" % (e["kind"], e["name"], body)


def verify_refs(con):
    """检查已冻结的参照图**内容有没有变**。

    依据 dsh-film 的交接规范：**绝不因为文件名看着对就认一个文件；
    字节变了就是另一个版本。**

    返回 (ok, 说明)。三种异常要分开报：
      missing  文件不在了
      changed  sha256 对不上——**文件被换过**（同名覆盖、手动替换）
      unknown  历史数据没记 sha256（迁移前的行）
    """
    missing, changed, unknown, ok = [], [], [], 0
    for r in con.execute("SELECT entity_id, view, path, sha256 FROM entity_ref"):
        p = C.abs_path(r["path"]) if r["path"] else None
        if not p or not os.path.exists(p):
            missing.append("%s/%s" % (r["entity_id"], r["view"]))
            continue
        if not r["sha256"]:
            unknown.append("%s/%s" % (r["entity_id"], r["view"]))
            continue
        if C.file_sha256(p) != r["sha256"]:
            changed.append("%s/%s" % (r["entity_id"], r["view"]))
        else:
            ok += 1
    if not (missing or changed):
        return True, "OK（%d 张一致%s）" % (
            ok, "，%d 张缺指纹（迁移前）" % len(unknown) if unknown else "")
    parts = []
    if changed:
        parts.append("**%d 张内容已变**：%s" % (len(changed), "、".join(changed[:5])))
    if missing:
        parts.append("%d 张文件缺失：%s" % (len(missing), "、".join(missing[:5])))
    return False, "；".join(parts)


def build_view_prompt(e, view_name, view_desc, reference=None):
    """按素材类型组装提示词。

    人物用 portrait.md 模板（含"真人质感"混搭与妆造部件级描述），
    场景/道具复用同一模板但换掉视角说明——它们的差别主要在视角描述上。

    **扩图**：正视图若挂了子空间当参考，视角说明换成扩图指令。
    依《8、场景穿越》原文写法：
    「将参考图放在中间黄金分割点，向四周扩图…**保持参考图中间的包间不变**」。
    关键是那句"保持不变"——不写的话，模型会**重画整个画面**，
    子空间就白做了。
    """
    desc = view_desc
    if view_name == "scene_front" and reference:
        desc = SCENE_EXPAND_DESC
    return L.load_prompt(
        TPL,
        entity_block=entity_block(e),
        view_name=view_name,
        view_desc=desc,
    )


def image_unit_price(con, model_id):
    r = con.execute("SELECT unit_price FROM price_book WHERE model_id=? AND unit=?",
                    (model_id, "image")).fetchone()
    return r["unit_price"] if r else None


def out_path(ep, e, view):
    """参考图落盘路径。走 assets_root()，便于自检用 DSH_ASSETS_ROOT 隔离。"""
    safe = "".join(ch for ch in e["name"] if ch not in '\\/:*?"<>|').strip() or "entity"
    p = os.path.join(C.assets_root(), "refs", e["kind"], "%s_%s.png" % (safe, view))
    return C.asset_rel(p)


def find_existing(e, view):
    """找**磁盘上已存在**的某个视角图，返回绝对路径或 None。

    ## 为什么需要

    参考链是**全身照在前、大头照以它为参考**（见 ref_for 的说明）。
    所以在改版造型要重出全身照时，管线**没有面部参考**，脸会变。

    而脸恰恰是 60 集最不能变的东西。实测有颗痣是识别锚点——
    这种细节一旦丢了，后面每一集都要重新锚定人物。

    做法：重出全身照时，把**旧的大头照**（磁盘上还在）当面部参考，
    于是服装按新设定、脸保持原样。

    ⚠️ 只找磁盘，不看数据库：数据库记录可能已被清理，
    而文件还在（本项目的旧资产就是这种情况）。
    """
    safe = "".join(ch for ch in e["name"] if ch not in '\\/:*?"<>|').strip() or "entity"
    base = os.path.join(C.assets_root(), "refs", e["kind"])
    for ext in (".png", ".jpg", ".jpeg"):
        p = os.path.join(base, "%s_%s%s" % (safe, view, ext))
        if os.path.exists(p):
            return p
    return None


def do_one(con, cfg, ep, e, view, prompt, reference, size, dry, rep, tag):
    rel = out_path(ep, e, view)
    if dry:
        rep.info("计划：%s  %s  ->  %s%s"
                 % (tag, e["name"], rel, "（参考：%s）" % reference if reference else ""))
        return True

    try:
        res = A.generate_image(prompt, size=size, reference=reference,
                               model=cfg["image_model"], base_url=cfg["base_url"],
                               api_key=cfg["api_key"])
    except A.ArkError as ex:
        gid = con.execute(
            "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
            " ref_image_path, status, error, cost, finished_at) "
            "VALUES (?,?,?,?,?,?,'failed',?,0,datetime('now','localtime'))",
            (e["id"], view, cfg["image_model"], prompt, size, reference, str(ex)[:500])
        ).lastrowid
        con.commit()
        msg = str(ex)
        rep.add("%s %s" % (tag, e["name"]), False, msg.splitlines()[0][:64])
        # 完整错误必须全部可见：可操作的下一步和落盘的原始响应路径都在里面，
        # 而报告行会截断到 64 字。实测踩过两次——先是第二行被吞，再是第一行被截。
        for line in msg.splitlines():
            if line.strip():
                rep.info(line.strip())
        return False

    url = res["urls"][0]
    price = image_unit_price(con, res["model"])
    cost = price if price is not None else 0.0
    try:
        nbytes = A.download(url, C.abs_path(rel))
    except Exception as ex:
        # ⚠️ 下载失败**不是** succeeded。原来这里把 rel 置 None 后继续往下走，
        #    末尾统一写 'succeeded'——于是磁盘上一张图都没有，库里却是全绿。
        #    这是"测试全绿但结果不对"的同一类病（HANDOFF §七），而且更贵：
        #    **它把真实的钱记成了成功**。实测踩到：7 张图 ¥2.10，一张没落盘，
        #    status 全是 succeeded、local_path 全空。
        #
        #    钱是要照记的（模型已经生成、已经计费），但**状态必须诚实**：
        #    API 成功 + 落盘失败 = 'failed'，并把 url 留下来供重试下载。
        con.execute(
            "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
            " ref_image_path, status, image_url, local_path, error, cost, finished_at) "
            "VALUES (?,?,?,?,?,?,'failed',?,NULL,?,?,datetime('now','localtime'))",
            (e["id"], view, res["model"], prompt, size, reference, url,
             "生成成功但下载失败：%s" % str(ex)[:400], cost)).lastrowid
        con.execute(
            "INSERT INTO cost_ledger (category, model_id, quantity, unit, unit_price,"
            " amount, note) VALUES ('image',?,1,'image',?,?,?)",
            (res["model"], cost, cost, "%s %s（已计费·未落盘）" % (e["name"], view)))
        con.commit()
        rep.add("%s %s 下载失败" % (tag, e["name"]), False, str(ex)[:60])
        rep.info("模型已计费 ¥%.2f，但文件没落盘。URL 已存进 image_generation，"
                 "可用它重试下载（不必重新生成、不必再付费）。" % cost)
        return False

    # ⚠️ 按**内容**校正扩展名。Seedream 返回 JPEG，而 out_path() 拼的是 .png，
    # 于是文件长期是"名叫 png、实为 jpg"。ffmpeg 按内容嗅探所以链路照跑，
    # 但严格的图片读取器会直接拒读——实测踩过。
    if rel:
        _real = C.sniff_image_ext(C.abs_path(rel))
        if _real and not rel.lower().endswith(_real):
            _new = rel[:rel.rfind(".")] + _real
            try:
                os.replace(C.abs_path(rel), C.abs_path(_new))
                rel = _new
            except OSError:
                pass

    gid = con.execute(
        "INSERT INTO image_generation (entity_id, view, model_id, prompt, size,"
        " ref_image_path, status, image_url, local_path, cost, finished_at) "
        "VALUES (?,?,?,?,?,?,'succeeded',?,?,?,datetime('now','localtime'))",
        (e["id"], view, res["model"], prompt, size, reference, url, rel, cost)
    ).lastrowid
    # 内容指纹。**字节变了就是另一个版本**——只靠路径认图，
    # 文件被同名覆盖或手动换了一张，系统是不知道的。
    sha = C.file_sha256(C.abs_path(rel)) if rel else None
    con.execute(
        "INSERT INTO entity_ref (entity_id, view, path, image_gen_id, is_primary,"
        " approved, sha256) VALUES (?,?,?,?,?,0,?) "
        "ON CONFLICT(entity_id, view) DO UPDATE SET path=excluded.path, "
        "image_gen_id=excluded.image_gen_id, sha256=excluded.sha256",
        (e["id"], view, rel, gid, 1 if view == PRIMARY_VIEW.get(e["kind"]) else 0, sha))
    con.execute(
        "INSERT INTO cost_ledger (category, model_id, quantity, unit, unit_price,"
        " amount, note) VALUES ('image',?,1,'image',?,?,?)",
        (res["model"], cost, cost, "%s %s" % (e["name"], view)))
    con.commit()
    # 回传落盘路径：调用方要靠它更新 have，否则**同一轮里后面的视角
    # 找不到刚生成的参考图**（实测踩过：大头照以全身照为参考，
    # 但 have 是开工前拍的快照，于是所有大头照都被跳过，只出了一半的图）。
    return rel

    detail = "%.0f KB" % (nbytes / 1024.0) if nbytes else rel
    if price is None:
        detail += "  ⚠ price_book 里没有该模型单价，本次成本记 0"
    rep.add("%s %s" % (tag, e["name"]), True, detail)
    return True


def main():
    ap = argparse.ArgumentParser(description="第 3/4 阶段：生成参考图")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--phase", choices=("characters", "scenes", "props"), required=False,
                    default=None)
    ap.add_argument("--verify-refs", action="store_true",
                    help="只检查已冻结的参照图内容有没有被换过，不出图（不需要 --phase）")
    ap.add_argument("--only", default=None, help="只处理某个实体名")
    ap.add_argument("--include-background", action="store_true",
                    help="连 background 级实体也出图（默认不出）")
    ap.add_argument("--force", action="store_true", help="已存在的视角也重新生成")
    ap.add_argument("--keep-face", dest="keep_face", action="store_true",
                    help="重出人物全身照时，用磁盘上旧的大头照当面部参考"
                         "（改版造型但脸要保留时用；否则脸会变）")
    ap.add_argument("--size", default="2K", help="1K/2K/4K 或 '宽x高'（默认 2K）")
    ap.add_argument("--allow-unknown-price", action="store_true",
                    help="价目缺失时也继续（账本会失真，仅用于跑通流程）")
    A.add_ark_args(ap)
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2

    # --verify-refs：只查不生成。用在下游出图之前——
    # 已冻结的锚点图若被换过，后面几十集都会用错。
    if args.verify_refs:
        rep = C.Report("参照图内容指纹校验")
        okr, why = verify_refs(con)
        rep.add("参照图内容指纹一致", okr, why)
        if not okr:
            print("\n参照图被换过了。下游生成会用到错的锚点图——")
            print("  要么把文件换回去，要么用 --force 重出这些视角。")
        con.close()
        return rep.finish()

    if not args.phase:
        print("要么给 --phase {characters|scenes|props}，要么给 --verify-refs")
        con.close()
        return 2

    label = {"characters": "第 3 阶段 · 人物素材（大头照 + 全身照）",
             "scenes": "第 4 阶段 · 场景素材（正视图 + 俯视图 + 站位图）",
             "props": "第 4 阶段 · 道具素材"}[args.phase]
    rep = C.Report(label)
    print("集：第 %s 集 项目：%s\n" % (ep["seq"], ep["project"]))

    tiers = list(PHASE_TIERS[args.phase])
    if args.include_background:
        tiers.append("background")
    # **必须按 kind 过滤**：三个阶段各管一类实体。
    # 只按 role 过滤的话，--phase characters 会连场景和道具一起出图。
    q = ("SELECT * FROM entity WHERE project_id=? AND kind=? AND role IN (%s)"
         % ",".join("?" * len(tiers)))
    ents = [dict(r) for r in con.execute(
        q, [ep["project_id"], PHASE_KIND[args.phase]] + tiers)]
    if args.only:
        ents = [e for e in ents if e["name"] == args.only]
    if not ents:
        rep.add("有可处理的实体", False,
                "本项目 role ∈ %s 的实体为空，先跑第 2 阶段" % tiers)
        con.close()
        return rep.finish()

    try:
        cfg = A.require_key()
    except A.ArkError as e:
        rep.add("Ark 已配置", False, str(e).splitlines()[0])
        print("\n" + str(e))
        con.close()
        return rep.finish()
    # 命令行可覆盖端点与模型
    cfg["base_url"] = args.base_url or cfg["base_url"]
    cfg["image_model"] = args.image_model or cfg["image_model"]

    rep.info("模型：%s　size：%s　tier：%s　实体数：%d"
             % (cfg["image_model"], args.size, "/".join(tiers), len(ents)))

    def views_for(e):
        """这个实体该出哪些素材。人物与场景用两套体系（见文件头的重构说明）。"""
        if e["kind"] == "character":
            return CHARACTER_VIEWS.get(e["role"], [])
        if e["kind"] == "location":
            return SCENE_VIEWS.get(e["role"], [])
        if e["kind"] == "prop":
            return [PROP_VIEW] if e["role"] in ("main", "secondary") else []
        return []

    def ref_for(e, view):
        """给某个视角找参考图（i2i）。

        依据《真人质感》：**先用 i2i 生成半身/全身图，再据它生成脸部视图**，
        能更好地控制人脸。所以全身照在前、大头照以它为参考。
        场景的俯视图以正视图为参考，保持同一空间。

        `--keep-face`：重出全身照时，用磁盘上**旧的大头照**当面部参考。
        改版造型（服装/姿态变了、脸要保留）时用这个，
        否则重出全身照会让脸变，而脸是 60 集最不能变的东西。
        """
        if e["kind"] == "character" and view == "headshot":
            return have.get((e["id"], "fullbody"))
        # 场景的参考链（依《8、场景穿越》"子空间先行，再扩图"）：
        #   子空间   -> 无参考（文生图，最小单位）
        #   正视图   -> **以子空间为参考做扩图**（"保持参考图中间不变"）
        #   俯视/站位 -> 以正视图为参考（保持同一空间）
        if e["kind"] == "location":
            if view in ("scene_top", "blocking"):
                return have.get((e["id"], "scene_front"))
            if view == "scene_front":
                return have.get((e["id"], "scene_subspace"))
        # 全身照：默认无参考（文生图）；开了 --keep-face 就挂旧大头照
        #
        # 用 getattr 是因为 `args` 是外层 main() 的局部名，而 ref_for 定义在
        # 同级作用域里、靠闭包读到它。自检会以函数方式导入本模块，
        # 那时没有 args —— getattr 让它退化为"默认关闭"，而不是抛 NameError。
        if e["kind"] == "character" and view == "fullbody" \
                and getattr(args, "keep_face", False):
            old = find_existing(e, "headshot")
            if old:
                rep.info("面部参考（旧大头照）：%s"
                         % os.path.relpath(old, C.assets_root())[:60])
            return old
        return None

    # 已有参考图检查
    have = {}
    for r in con.execute("SELECT entity_id, view, path FROM entity_ref"):
        have[(r["entity_id"], r["view"])] = r["path"]

    price = image_unit_price(con, cfg["image_model"])
    planned = 0
    # 生成顺序**由参考链决定**：被参考的先生成。
    #   人物：全身照 -> 大头照
    #   场景：子空间 -> 正视图（扩图）-> 俯视图 / 站位图
    ORDER = {"fullbody": 0, "headshot": 1,
             "scene_subspace": 0, "scene_front": 1,
             "scene_top": 2, "blocking": 2, "prop_ref": 0}

    def ordered(e):
        return sorted(views_for(e), key=lambda v: ORDER.get(v[0], 9))

    for e in ents:
        for v, _n, _d in ordered(e):
            if args.force or not have.get((e["id"], v)):
                planned += 1

    rep.add("待生成张数", True, "%d 张%s" % (planned, "（dry-run）" if args.dry_run else ""))
    if price is not None:
        rep.add("预估成本", True, "约 ¥%.2f（¥%.2f/张）" % (planned * price, price))
    elif not args.allow_unknown_price:
        # **拒绝在未知花费下继续。**
        # 原来这里只打一句"成本将记 0"就接着跑。后果是：跑 80 集、生成几千张图，
        # cost_ledger 上写着 ¥0，你完全不知道花了多少。
        # 这与 cost-governor 里给视频定的规矩应当一致——当时只对图片手软了。
        rep.add("[%s] 价目缺失时拒绝继续" % C.BLOCK, False,
                "price_book 里没有 %s 的图片单价" % cfg["image_model"])
        print()
        print("拒绝在未知花费下继续——按 0 记账会让账本彻底失真。")
        print("补价目（去火山方舟控制台的模型计费页查单价）：")
        print("  INSERT INTO price_book (model_id, variant, unit, unit_price,"
              " confidence, source)")
        print("    VALUES ('%s', '图片', 'image', <单价>, 'high', '控制台');"
              % cfg["image_model"])
        print("确实要在未知花费下继续（比如只想跑通流程）：加 --allow-unknown-price")
        con.close()
        return rep.finish()
    else:
        rep.info("已按 --allow-unknown-price 继续，本次成本记 0（账本会失真）")

    if not planned:
        rep.info("没有需要生成的视角（都已存在）。要重出请加 --force")
        con.close()
        return rep.finish()

    ok = True
    for e in ents:
        for v, vn, vd in ordered(e):
            if not (args.force or not have.get((e["id"], v))):
                continue
            ref = ref_for(e, v)
            if v == "headshot" and not ref:
                rep.info("跳过 %s 的大头照：没有全身照可作参考（先跑同一阶段，"
                         "全身照会先出）" % e["name"])
                continue
            if v == "scene_front" and ref:
                rep.info("扩图：%s 的正视图以子空间为参考（保持中间不变）"
                         % e["name"])
            got = do_one(con, cfg, ep, e, v,
                         build_view_prompt(e, vn, vd, ref),
                         ref, args.size, args.dry_run, rep,
                         "%s·%s" % (e["name"], vn))
            ok &= bool(got)
            if got and not args.dry_run:
                # 立刻登记，后面的视角才能把它当参考图（见 do_one 的说明）
                have[(e["id"], v)] = got

    if not args.dry_run:
        n = con.execute(
            "SELECT COUNT(*) FROM entity_ref r JOIN entity e ON e.id=r.entity_id "
            "WHERE e.project_id=?", (ep["project_id"],)).fetchone()[0]
        rep.add("entity_ref 累计参考图数", True, "%d 张" % n)
        rep.info("下一步：人工审图，选定后用 entity_ref.approved=1 冻结")
    con.close()
    return rep.finish() if ok else 1


if __name__ == "__main__":
    sys.exit(main())
