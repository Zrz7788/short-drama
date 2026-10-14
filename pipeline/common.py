# -*- coding: utf-8 -*-
"""管线脚本的共用层：路径、数据库连接、ffmpeg 调用。

所有阶段脚本都从这里取 ffmpeg 路径与数据库连接，避免各写一份。
数据库路径可用 --db 覆盖（自检用它指向临时库，不碰生产数据）。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(ROOT, ".tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE = os.path.join(ROOT, ".tools", "ffmpeg", "bin", "ffprobe.exe")
DEFAULT_DB = os.path.join(ROOT, "data", "drama.db")
ASSETS = os.path.join(ROOT, "assets")
ENV_FILE = os.path.join(ROOT, ".env")


def load_env(path=None):
    """把工作区的 .env 读进环境变量。返回载入的条数。

    **已存在的环境变量优先**——所以自检里临时传的 ARK_API_KEY=test-key
    不会被文件里的真值盖掉，CI 覆盖也照常生效。

    为什么必须有这个函数：在这之前，ark.py 与 llm.py 的报错信息都写着
    "推荐写进工作区 .env"，但**没有任何代码读那个文件**——那是句空头承诺。
    实测发现：只设 .env 的话，脚本会报"密钥未配置"，而错误提示正是叫你写 .env。

    为什么用文件而不是 setx：DSH 宿主进程在用户设环境变量之前就启动了，
    setx 改的东西它看不见；而且 setx 的结果对已运行的父进程不生效。
    文件是本机唯一可靠的传递方式，且 .env 已被 .gitignore 挡住。
    """
    p = path or ENV_FILE
    # 自检要把"未配置时报错"这类反向用例跑起来，就不能被 .env 里的真凭证污染。
    # 设 DSH_NO_DOTENV=1 即可让本函数什么都不做。
    if os.environ.get("DSH_NO_DOTENV"):
        return 0
    if not os.path.exists(p):
        return 0
    n = 0
    with open(p, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, sep, val = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
                n += 1
    return n


# 模块导入时即生效：所有阶段脚本都 import common，所以一处加载、处处可用
ENV_LOADED = load_env()


def add_common_args(parser):
    parser.add_argument("--db", default=os.environ.get("DSH_DRAMA_DB", DEFAULT_DB),
                        help="数据库路径（默认 data/drama.db）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    return parser


def require_ffmpeg():
    missing = [p for p in (FFMPEG, FFPROBE) if not os.path.exists(p)]
    if missing:
        print("缺少 ffmpeg 组件：")
        for m in missing:
            print("  ", m)
        print("先运行： python tools/setup_ffmpeg.py")
        sys.exit(2)


def connect(db_path):
    if not os.path.exists(db_path):
        print("数据库不存在:", db_path)
        print("先运行： python db/init_db.py")
        sys.exit(2)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def run_ffmpeg(args, cwd=None, timeout=1800):
    return subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, timeout=timeout)


def probe(path, entries, cwd=None):
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", entries,
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    return (p.stdout or "").strip()


def duration(path, cwd=None):
    try:
        return float(probe(path, "format=duration", cwd=cwd))
    except ValueError:
        return -1.0


# ---------------------------------------------------------------- U0 规格门
#
# 依据 Muse 短片流水线评审（toosielu/muse-idea-to-short Issue #1）：
# **画幅已知会跑偏**（720×1280、768×1152、784×1088、1280×720、640×480 都出现过），
# 而当时"没有任何一步能拦住"。
#
# 关键设计：在**下载下来的文件**上量，而不是信请求参数——
# 我们在请求里写 resolution=1080p，不代表它真返回 1080p。
#
# 不合格的处理是**原样重生成、计入重试次数、不进入语义检查**：
# **先保证它是个合法产物，再谈它好不好。顺序不能反。**
SPEC_ASPECT_TOL = 0.01        # 画幅比例允许的绝对误差

# 图片魔数 → 扩展名。**按内容判断，不按我们给文件名取的扩展名。**
_IMG_SIGS = ((b"\x89PNG\r\n\x1a\n", ".png"),
             (b"\xff\xd8\xff", ".jpg"),
             (b"RIFF", ".webp"),
             (b"GIF8", ".gif"),
             (b"BM", ".bmp"))


def sniff_image_ext(path):
    """按**文件内容**判断图片真实格式，返回小写扩展名；认不出返回 None。

    ⚠️ 为什么必须有这个：Seedream 返回的是 **JPEG**，而我们把文件名写成了 `.png`。
    实测踩过——三张人物参考图全是 `xxx.png`，字节却是 JPEG。

    这个 bug 藏了很久没暴露，因为 **ffmpeg 这类工具按内容嗅探、不在意扩展名**，
    整条链路照样跑通；**只有严格的图片读取器（以及任何信扩展名的人或工具）看得出**。
    我们的自检也从没检查过真实格式——**又一个"能跑通但不对"**。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for sig, ext in _IMG_SIGS:
        if head.startswith(sig):
            return ext
    return None



def spec_gate(path, target_aspect=None, min_short_side=None,
              min_seconds=None, max_seconds=None):
    """量一个**已下载文件**的硬规格。返回 (ok, 说明, 详情)。

    只查机制性的东西（尺寸/时长），**不查内容好不好**——那是语义检查的事。
    缺参数就不查那一项。
    """
    import math
    d = {}
    sig = video_signature(path)
    if not sig or not sig[0] or not sig[1]:
        return False, "读不出视频尺寸（文件损坏或不是视频）", d
    w, h = int(sig[0]), int(sig[1])
    d["width"], d["height"] = w, h
    d["short_side"] = min(w, h)
    d["aspect"] = round(w / float(h), 5)
    d["seconds"] = round(duration(path), 2)

    bad = []
    if target_aspect:
        diff = abs(d["aspect"] - float(target_aspect))
        d["aspect_target"] = float(target_aspect)
        d["aspect_diff"] = round(diff, 5)
        if diff > SPEC_ASPECT_TOL:
            bad.append("画幅 %.4f，目标 %.4f（差 %.4f > %.2f）"
                       % (d["aspect"], float(target_aspect), diff, SPEC_ASPECT_TOL))
    if min_short_side and d["short_side"] < int(min_short_side):
        bad.append("短边 %d < %d" % (d["short_side"], int(min_short_side)))
    if min_seconds and d["seconds"] >= 0 and d["seconds"] < float(min_seconds) - 0.5:
        bad.append("时长 %.2fs < %.1fs" % (d["seconds"], float(min_seconds)))
    if max_seconds and d["seconds"] >= 0 and d["seconds"] > float(max_seconds) + 0.5:
        bad.append("时长 %.2fs > %.1fs" % (d["seconds"], float(max_seconds)))

    if bad:
        return False, "；".join(bad), d
    return True, ("%dx%d %.2fs" % (w, h, d["seconds"])), d


def video_signature(path, cwd=None):
    """返回 (宽, 高, 编码, 像素格式) 元组，用于判断能否 -c copy 直接拼接。

    ⚠️ 必须用 -of json 按字段名取，不要用 csv=p=0 按位置切。
    ffprobe 的 CSV 输出遵循**它自己的内部字段顺序**（codec_name,width,height,pix_fmt），
    不遵循 -show_entries 里的书写顺序。按位置切会把编码名当成宽度，
    生成 scale=h264:320 这类非法滤镜——实测踩过，表现为
    "Error opening output files: Invalid argument"，且很难看出因果。
    """
    p = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,codec_name,pix_fmt",
                        "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    try:
        st = json.loads(p.stdout or "")["streams"][0]
    except (ValueError, KeyError, IndexError):
        return None
    return (st.get("width"), st.get("height"),
            st.get("codec_name"), st.get("pix_fmt"))


def episode_shots(con, episode_id):
    """按镜号返回镜头及其选定成片。

    选定规则：优先 shot.selected_generation_id；
    没指定时退回该镜头第一条 succeeded 的记录。
    """
    rows = con.execute(
        """
        SELECT s.id            AS shot_id,
               s.seq           AS shot_seq,
               s.duration_s    AS shot_seconds,
               s.status        AS shot_status,
               s.first_frame_path, s.last_frame_path, s.continues_from,
               s.selected_generation_id,
               g.id            AS gen_id,
               g.local_path    AS video_path,
               g.status        AS gen_status
        FROM shot s
        LEFT JOIN generation g
               ON g.id = COALESCE(s.selected_generation_id,
                                  (SELECT id FROM generation
                                    WHERE shot_id = s.id AND status = 'succeeded'
                                    ORDER BY attempt_no LIMIT 1))
        WHERE s.episode_id = ?
        ORDER BY s.seq
        """, (episode_id,)).fetchall()
    return [dict(r) for r in rows]


def episode_info(con, episode_id):
    # 把 project.logline 一起带出来：它是创意梗概的**真源**，
    # 第 1 阶段的 brief 不传时就取它（编排层不该知道每个阶段的参数）。
    r = con.execute(
        "SELECT e.id, e.seq, e.title, p.name AS project, p.id AS project_id,"
        " p.logline, p.aspect_ratio, p.target_episode_seconds, e.target_seconds "
        "FROM episode e JOIN project p ON p.id = e.project_id WHERE e.id = ?",
        (episode_id,)).fetchone()
    return dict(r) if r else None


def assets_root():
    """素材根目录。可用 DSH_ASSETS_ROOT 覆盖——自检借此写到临时目录，
    不污染真实的 assets/。

    也可用来把**生成物放到别的盘**（.env 里设 `DSH_ASSETS_ROOT=D:/...`）。
    改这个之前请先看 `asset_rel()`：跨盘时路径不能再存相对的。
    """
    return os.environ.get("DSH_ASSETS_ROOT") or ASSETS


def asset_rel(p):
    """把素材路径存成**跨盘可用的**形式。

    能相对工作区就相对（整个仓库搬家时还找得到）；
    **不能就存绝对路径。**

    ⚠️ 为什么必须这样：Windows 上跨盘 `os.path.relpath` 会直接抛
        ValueError: path is on mount 'D:', start on mount 'C:'
    实测踩过——把生成物放到 D 盘时，落盘路径这一行会整段炸掉。
    `abs_path()` 对绝对路径是原样返回的，所以两种形式都能读回来。
    """
    try:
        r = os.path.relpath(p, ROOT)
        if not r.startswith(".."):
            return r.replace("\\", "/")
    except ValueError:
        pass
    return os.path.abspath(p).replace("\\", "/")


def file_sha256(path, chunk=1 << 20):
    """算文件的内容指纹。文件不存在返回 None。

    用途：参照图的版本标识。依据 dsh-film 的交接规范——
    **绝不因为文件名看着对就认一个文件；字节变了就是另一个版本。**
    """
    import hashlib
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 规则强度三分法
#
# 依据 dsh-film 的交接规范。**每条校验先归类，档位决定"违反时怎么办"**：
#
#   BLOCK  可核查的——引用完整性、枚举、API 硬限制、预算与账本、
#          上游数据是否齐备。
#          → **修正或报告；这些会阻断交接。**
#
#   WARN   工艺默认值——craft 层面的建议（运镜四件套写全没、速度带没带数字、
#          防崩坏词是不是常用说法…）。
#          → **遵循，除非作者意图另有说明；偏离时说清理由即可，不阻断。**
#
#   NOTE   品味——结局基调、钩子类型、视角、风格、景别偏好。
#          → **作者决定；绝不用它阻断。**
#
# ⚠️ 最容易搞错的两种情形：
#   1. **把 WARN 当 BLOCK** —— 作者想用自己的说法时整批被拒收。
#      实测踩过：camera_lock 的白名单被做成硬拒绝。
#   2. **把"没事可做"当失败** —— 幂等重跑时"这一档已出完"是正常状态，
#      不是错误。实测踩过：stage_video 的 --mode final 重跑会以失败退出。
BLOCK, WARN, NOTE = "block", "warn", "note"
TIER_LABEL = {BLOCK: "硬门禁", WARN: "工艺建议", NOTE: "由作者定"}


# 自检用来证明"没污染生产库"的几张表
PROD_TABLES = ("project", "episode", "script", "scene", "entity", "shot",
               "generation", "image_generation", "entity_ref", "asset")


def prod_fingerprint(db=None):
    """生产库的行数指纹。

    自检开工前记一次、收工后比一次，**比"生产库必须为空"正确**——
    生产库本来就该有真实作品；原来的断言在第一次真跑之后就必然失败
    （实测：跑完第 1 阶段后三个自检各挂一项，而它们检查的其实是
    "自检有没有污染生产库"，不是"生产库空不空"）。
    """
    try:
        con = connect(db or DEFAULT_DB)
    except Exception:
        return {}
    out = {}
    for t in PROD_TABLES:
        try:
            out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except Exception:
            out[t] = None
    con.close()
    return out


def prod_unchanged(before, after=None):
    """指纹有没有变。返回 (是否未变, 说明文字)。"""
    after = after if after is not None else prod_fingerprint()
    diffs = []
    for t in PROD_TABLES:
        b, a = before.get(t), after.get(t)
        if b is not None and a is not None and a != b:
            diffs.append("%s %d→%d" % (t, b, a))
    if diffs:
        return False, "；".join(diffs)
    return True, "与开工前一致（%d 张表）" % len(PROD_TABLES)


def frame_paths(episode_seq, shot_seq):
    """抽帧产物路径。返回跨盘可用的路径（相对仓库根，跨盘则绝对）。"""
    d = os.path.join(assets_root(), "frames")
    first = os.path.join(d, "E%02dS%03d_first.png" % (episode_seq, shot_seq))
    last = os.path.join(d, "E%02dS%03d_last.png" % (episode_seq, shot_seq))
    # assets_root 配在 D 盘时 os.path.relpath 会炸（跨盘 ValueError）——
    # 复用 asset_rel 的跨盘逻辑，不重复实现。
    return (asset_rel(first), asset_rel(last))


def abs_path(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def tail(text, limit=70):
    """取最后一行非空输出，用作错误摘要。

    不要直接写 (text or '').splitlines()[-1]——当输出为空串时
    splitlines() 返回 []，会抛 IndexError 把真正的失败原因盖掉。实测踩过。
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return lines[-1].strip()[:limit] if lines else ""


class Report(object):
    """统一的结果汇总与退出码。"""

    def __init__(self, title):
        self.title = title
        self.rows = []

    def add(self, name, passed, detail=""):
        self.rows.append((name, passed, detail))
        print("  [%s] %-44s %s" % ("OK" if passed else "!!", name, detail))

    def info(self, msg):
        print("  --   %s" % msg)

    def finish(self):
        bad = [r for r in self.rows if not r[1]]
        print("\n" + "=" * 64)
        print("%s：共 %d 项，通过 %d，失败 %d -> %s"
              % (self.title, len(self.rows), len(self.rows) - len(bad), len(bad),
                 "全部通过" if not bad else "**有失败**"))
        for n, _, d in bad:
            print("   失败: %s %s" % (n, d))
        return 0 if not bad else 1
