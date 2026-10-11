# -*- coding: utf-8 -*-
"""第 3/4/7/8 阶段的端到端自检。

在本地起一个**假的火山方舟服务**，让 stage_refs.py、stage_firstframes.py 与
stage_video.py 真跑一遍：请求组装 -> 任务创建 -> 轮询 -> 下载 -> 落库 -> 成本记账。

刻意覆盖几种真实会遇到的坏情况：
  - 响应结构不符预期（防御式解析应把原始响应落盘并给出可读错误）
  - 401 鉴权失败（应提示检查 key 与模型权限）
  - 429 限流（应退避重试后成功）

全程用临时库与临时素材根，不碰生产数据与真实 assets/。

用法： python pipeline/selftest_ark.py [--keep]
"""
import argparse
import http.server
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402

TEST_ROOT = os.path.join(C.ROOT, ".tools", "ark-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")
TEST_ASSETS = os.path.join(TEST_ROOT, "assets")

rep = C.Report("第 3/4/7/8 阶段自检")

ENTITIES = [
    ("character", "林薇", "main", "32岁女性，东亚面孔，齐肩黑直发，杏眼，左眉尾浅疤"),
    ("character", "陈默", "main", "40岁男性，寸头，方脸，右眼下有一颗痣"),
    ("location", "出租屋", "secondary", "十平米单间，铁架床，墙面有水渍"),
    ("prop", "化验单", "secondary", "A5 大小的医院化验单，被攥出褶皱"),
    ("character", "护士", "background", "年轻女性护士，白色制服，蓝色口罩"),
]

PNG = None
VIDEO = None


def make_png():
    from PIL import Image
    img = Image.new("RGB", (96, 96), (198, 120, 92))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_mp4(root=None, short_side=None, seconds=None, aspect=9 / 16.0):
    """用 ffmpeg 造一段真 MP4；没有 ffmpeg 时退化成占位字节。

    root 可指定落盘目录（默认本模块的 TEST_ROOT）。别的自检复用本函数时
    必须传自己的目录——否则目标目录不存在，ffmpeg 写不出去就退化成了占位字节，
    而那会让抽帧/拼接阶段的测试失去意义。实测踩过。

    short_side / seconds：**按请求的规格生成**。
    加入 U0 规格门之后，固定造 270x480/1 秒会被门拦下——那不是门的错，
    是假服务没有模拟真实的返回规格。传了就按它造，门该过就过。
    """
    root = root or TEST_ROOT
    os.makedirs(root, exist_ok=True)
    out = os.path.join(root, "seed.mp4")
    exe = os.path.join(C.ROOT, ".tools", "ffmpeg", "bin", "ffmpeg.exe")
    if not os.path.exists(exe):
        return b"\x00" * 2048, "占位字节（未装 ffmpeg）"
    if short_side:
        # libx264 + yuv420p 要求宽高都是偶数，否则 ffmpeg 直接失败。
        # 实测踩过：480 / 0.5625 = 853.33 → 853（奇数），编码失败，返回占位字节，
        # 然后规格门报"读不出视频尺寸"——因果链很绕，值得记一笔。
        def _even(n):
            n = int(round(n))
            return n if n % 2 == 0 else n + 1

        # 竖屏：短边是宽；横屏反过来。这里统一按 aspect<1（竖屏）处理。
        if aspect and aspect < 1:
            w, h = _even(short_side), _even(short_side / aspect)
        else:
            h, w = _even(short_side), _even(short_side * (aspect or 16 / 9.0))
        size = "%dx%d" % (w, h)
    else:
        size = "270x480"
    dur = seconds or 1
    r = C.run_ffmpeg(["-f", "lavfi", "-i",
                      "testsrc=size=%s:rate=24:duration=%s" % (size, dur),
                      "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      os.path.relpath(out, C.ROOT).replace("\\", "/")], cwd=C.ROOT)
    if r.returncode != 0 or not os.path.exists(out):
        return b"\x00" * 2048, "占位字节（ffmpeg 失败：%s）" % C.tail(r.stderr, 40)
    with open(out, "rb") as f:
        return f.read(), "真 MP4（%s %ss）" % (size, dur)


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []
    video_requests = []
    video_specs = {}      # tid -> (resolution, duration)
    video_cache = {}      # tid -> mp4 bytes
    _tid_seq = 0

    @classmethod
    def next_tid(cls):
        """只增不减的任务序号。

        ⚠️ **别用 `len(video_requests)`** —— 测试中途会 `clear()`，计数器归零，
        任务 id 就重复了；而 `video_specs` 是按 id 记规格的，于是 1080p 的请求
        会拿到早先 480p 任务的缓存视频，被规格门判失败。
        实测踩过：表现为 `direct`/`final` 全部 spec_failed「短边 480 < 1080」，
        而规格门本身是对的——错的是假服务。
        """
        cls._tid_seq += 1
        return cls._tid_seq
    video_polls = {}
    state = {"mode": "ok", "n429": 0}

    def _send(self, code, blob, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path.startswith("/mock/video"):
            # 路径形如 /mock/video.mp4?tid=cgt-0001 —— 按该任务的规格造。
            tid = ""
            if "tid=" in self.path:
                tid = self.path.split("tid=", 1)[1].split("&")[0]
            res, dur = Handler.video_specs.get(tid, ("1080p", 4))
            blob = Handler.video_cache.get(tid)
            if blob is None:
                short = {"480p": 480, "720p": 720, "1080p": 1080}.get(res, 1080)
                blob, _note = make_mp4(root=TEST_ROOT, short_side=short,
                                       seconds=float(dur))
                Handler.video_cache[tid] = blob
            return self._send(200, blob, "video/mp4")
        if self.path.startswith("/mock/"):
            return self._send(200, PNG, "image/png")
        if "/contents/generations/tasks/" in self.path:
            tid = self.path.rsplit("/", 1)[-1].split("?")[0]
            Handler.video_polls[tid] = Handler.video_polls.get(tid, 0) + 1
            port = self.server.server_address[1]
            # 第一次返回 running，之后 succeeded —— 真实服务就是这样异步的
            if Handler.video_polls[tid] < 2:
                return self._json(200, {"id": tid, "status": "running"})
            return self._json(200, {
                "id": tid, "status": "succeeded",
                "content": {"video_url":
                            "http://127.0.0.1:%d/mock/video.mp4?tid=%s" % (port, tid)},
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"_raw": raw[:200].decode("utf-8", "replace")}
        rec = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}

        if "/contents/generations/tasks" in self.path:
            Handler.video_requests.append(rec)
            if Handler.state["mode"] == "video_bad":
                # 创建成功但响应里没有任务 id —— 防御式解析该给出可读错误
                return self._json(200, {"something": "else"})
            if Handler.state["mode"] == "401":
                return self._json(401, {"error": {"code": "AuthenticationError",
                                                  "message": "the API key is invalid"}})
            tid = "cgt-%04d" % Handler.next_tid()
            # 记住这次请求的规格，轮询成功时按它造视频。
            # 加了 U0 规格门之后，假服务必须**模拟真实的返回规格**——
            # 否则固定造 270x480 会被门拦下，而那不是门的错。
            Handler.video_specs[tid] = (body.get("resolution") or "1080p",
                                        body.get("duration") or 4)
            return self._json(200, {
                "id": tid,
                "model": body.get("model", ""),
            })

        Handler.requests.append(rec)
        mode = Handler.state["mode"]
        if mode == "401":
            return self._json(401, {"error": {"code": "AuthenticationError",
                                              "message": "the API key is invalid"}})
        if mode == "429":
            Handler.state["n429"] += 1
            if Handler.state["n429"] == 1:
                return self._json(429, {"error": {"code": "ModelAccountIpmRateLimitExceeded",
                                                  "message": "rate limited"}})
        if mode == "bad_shape":
            return self._json(200, {"unexpected": True, "note": "no data array"})

        port = self.server.server_address[1]
        return self._json(200, {
            "model": body.get("model", ""),
            "data": [{"url": "http://127.0.0.1:%d/mock/img.png" % port}],
        })

    def log_message(self, *a):
        pass


def run_script(script, args, base_url, api_key="test-key", assets=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 自检必须是**密闭的**：不能被工作区 .env 里的真实凭证影响，
    # 否则"未配置时报错"这类反向用例会失效（实测踩过：加了 .env 之后
    # 两个自检各挂掉一部分）。DSH_NO_DOTENV 让 common.load_env() 什么都不做。
    env["DSH_NO_DOTENV"] = "1"
    if base_url:
        env["ARK_BASE_URL"] = base_url
    env["ARK_API_KEY"] = api_key
    env["DSH_ASSETS_ROOT"] = assets or TEST_ASSETS
    return subprocess.run([sys.executable, "-X", "utf8",
                           os.path.join(C.ROOT, "pipeline", script)] + args,
                          cwd=C.ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def run_stage(args, base_url, api_key="test-key", assets=None):
    return run_script("stage_refs.py", args, base_url, api_key, assets)


def main():
    global PNG, VIDEO
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    PNG = make_png()
    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)
    VIDEO, video_note = make_mp4()

    print("=== 0. 临时资源 ===")
    rep.add("构造测试 PNG", len(PNG) > 0, "%d 字节" % len(PNG))
    rep.add("构造测试视频素材", len(VIDEO) > 0, "%s，%d 字节" % (video_note, len(VIDEO)))

    print("\n=== 1. 假 Ark 服务 ===")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rep.add("已启动", True, base)

    print("\n=== 2. 临时库与实体 ===")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep.add("临时库建立", p.returncode == 0)
    con = C.connect(TEST_DB)
    # **从生产价目表复制真实价格**，不硬编码。
    #
    # 这里原来写死 0.20 与 1.45。0.20 是 doubao-seedream-4-0-250828 的价格——
    # 一个我们因为账号没开通而**弃用**的模型；当前默认模型的真实价格是 0.30。
    # 于是测试在一个虚构的价格世界里跑，而且断言名里还写着"¥0.20/张"误导人。
    #
    # 更要命的是：硬编码意味着**价格一变，测试不会跟着变**——
    # 它测的还是老世界。改成从生产库复制后，两边永远同步。
    from pipeline import ark as _A
    prod = C.connect(C.DEFAULT_DB)
    for mid, unit in ((_A.MODEL_IMAGE, "image"), (_A.MODEL_VIDEO_25, "second")):
        src = prod.execute(
            "SELECT variant, unit_price, confidence, source FROM price_book "
            "WHERE model_id=? AND unit=? AND variant LIKE '%Draft%' "
            "ORDER BY unit_price LIMIT 1", (mid, unit)).fetchone()
        if not src:
            src = prod.execute(
                "SELECT variant, unit_price, confidence, source FROM price_book "
                "WHERE model_id=? AND unit=? ORDER BY unit_price LIMIT 1",
                (mid, unit)).fetchone()
        if src:
            con.execute("INSERT INTO price_book (model_id, variant, unit, unit_price,"
                        " confidence, source) VALUES (?,?,?,?,?,?)",
                        (mid, src["variant"], unit, src["unit_price"],
                         src["confidence"], "自检·复制自生产库"))
    prod.close()
    # 把本测试要用的单价读回来，后面的期望值全部由它推导——不再有魔数
    PRICE_IMG = con.execute(
        "SELECT unit_price FROM price_book WHERE model_id=? AND unit='image'",
        (_A.MODEL_IMAGE,)).fetchone()["unit_price"]
    PRICE_DRAFT = con.execute(
        "SELECT unit_price FROM price_book WHERE model_id=? AND unit='second' "
        "AND variant LIKE '%Draft%'", (_A.MODEL_VIDEO_25,)).fetchone()
    PRICE_FINAL = con.execute(
        "SELECT unit_price FROM price_book WHERE model_id=? AND unit='second' "
        "AND variant NOT LIKE '%Draft%'", (_A.MODEL_VIDEO_25,)).fetchone()
    PRICE_DRAFT = PRICE_DRAFT["unit_price"] if PRICE_DRAFT else PRICE_IMG
    PRICE_FINAL = PRICE_FINAL["unit_price"] if PRICE_FINAL else PRICE_IMG
    rep.info("价目（复制自生产库）：图片 ¥%.2f/张　样片 ¥%.2f/秒　成片 ¥%.2f/秒"
             % (PRICE_IMG, PRICE_DRAFT, PRICE_FINAL))
    con.commit()
    pid = con.execute("INSERT INTO project (name) VALUES ('Ark 自检')").lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    for kind, name, tier, desc in ENTITIES:
        con.execute("INSERT INTO entity (project_id, kind, name, role, description) "
                    "VALUES (?,?,?,?,?)", (pid, kind, name, tier, desc))
    con.commit()
    con.close()
    # 各阶段该出多少张，**从视图定义推导**，不硬编码——
    # 素材体系一改（比如三视图换成大头照+全身照），这里自动跟着变。
    from pipeline import stage_refs as S

    def _views_of(kind, role):
        if kind == "character":
            return len(S.CHARACTER_VIEWS.get(role, []))
        if kind == "location":
            return len(S.SCENE_VIEWS.get(role, []))
        if kind == "prop":
            return 1 if role in ("main", "secondary") else 0
        return 0

    def _imgs(kind):
        return sum(_views_of(k, r) for k, _n, r, _d in ENTITIES if k == kind)

    def _prim(kind):
        """有几张 is_primary（每个出过图的实体各一张）。"""
        return sum(1 for k, _n, r, _d in ENTITIES
                   if k == kind and _views_of(k, r) > 0)

    want_char, want_char_prim = _imgs("character"), _prim("character")
    want_scene, want_scene_prim = _imgs("location"), _prim("location")
    want_prop = _imgs("prop")
    n_main = sum(1 for e in ENTITIES if e[2] == "main")
    n_sec = sum(1 for e in ENTITIES if e[2] == "secondary")
    n_bg = sum(1 for e in ENTITIES if e[2] == "background")
    rep.add("实体已灌入", True, "main=%d secondary=%d background=%d"
            % (n_main, n_sec, n_bg))

    # ---- 3. 定妆照 ----
    print("\n=== 3. 第 3 阶段 --phase characters（人物素材）===")
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "characters"], base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    want_portrait = want_char          # 按视图定义推导；background 不出图
    rep.add("请求数与预期一致（人物素材）",
            len(Handler.requests) == want_portrait,
            "%d 次（预期 %d，background 不出）" % (len(Handler.requests), want_portrait))
    if Handler.requests:
        r0 = Handler.requests[0]
        rep.add("走的是 /images/generations", r0["path"].endswith("/images/generations"),
                r0["path"])
        rep.add("带 Bearer 鉴权", (r0["auth"] or "").startswith("Bearer "),
                (r0["auth"] or "")[:18] + "…")
        b = r0["body"]
        rep.add("请求体字段齐备",
                all(k in b for k in ("model", "prompt", "response_format", "size", "watermark")),
                " ".join(sorted(b.keys())))
        rep.add("response_format=url、watermark=false",
                b.get("response_format") == "url" and b.get("watermark") is False,
                "%s / %s" % (b.get("response_format"), b.get("watermark")))
        rep.add("定妆照不带参考图（文生图）", "image" not in b)

    con = C.connect(TEST_DB)
    n_ref = con.execute("SELECT COUNT(*) FROM entity_ref").fetchone()[0]
    rep.add("entity_ref 写入数", n_ref == want_portrait, "%d 张" % n_ref)
    prim = con.execute("SELECT COUNT(*) FROM entity_ref WHERE is_primary=1").fetchone()[0]
    rep.add("人物各有一张主参考图", prim == want_char_prim,
            "%d 张（预期 %d，每个出图实体一张）" % (prim, want_char_prim))
    n_gen = con.execute("SELECT COUNT(*) FROM image_generation WHERE status='succeeded'"
                        ).fetchone()[0]
    rep.add("image_generation 记录", n_gen == want_portrait, "%d 条" % n_gen)
    cost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger WHERE category='image'"
                       ).fetchone()[0]
    expect = want_portrait * PRICE_IMG
    rep.add("成本已入账（图片单价 × 张数）", abs(cost - expect) < 1e-6,
            "¥%.2f（预期 ¥%.2f，单价 ¥%.2f）" % (cost, expect, PRICE_IMG))
    missing_file = 0
    for r in con.execute("SELECT path FROM entity_ref"):
        if not os.path.exists(C.abs_path(r["path"])):
            missing_file += 1
    rep.add("图片文件都已下载", missing_file == 0, "%d 个缺失" % missing_file)
    bg_ref = con.execute("SELECT COUNT(*) FROM entity_ref r JOIN entity e ON e.id=r.entity_id "
                         "WHERE e.role='background'").fetchone()[0]
    rep.add("background 级没有出图", bg_ref == 0, "%d 张" % bg_ref)
    con.close()

    # ---- 4. 场景素材 ----
    print("\n=== 4. 第 4 阶段 --phase scenes（场景素材）===")
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "scenes"], base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    want_turn = want_scene            # 按视图定义推导
    rep.add("场景素材张数正确",
            len(Handler.requests) == want_turn,
            "%d 次（预期 %d）" % (len(Handler.requests), want_turn))
    # 正视图本来就是文生图（无参考）；派生视角（俯视图 / 站位图）才带参考图
    _ref_reqs = [r for r in Handler.requests if "image" in r["body"]]
    rep.add("派生视角带了参考图（图生图）",
            bool(Handler.requests) and len(_ref_reqs) == len(Handler.requests) - 1,
            "带参考图 %d / 共 %d 次" % (len(_ref_reqs), len(Handler.requests)))
    if _ref_reqs:
        img = _ref_reqs[0]["body"].get("image") or [""]
        rep.add("参考图是内联 data URL", img[0].startswith("data:image/"),
                img[0][:34] + "…")

    con = C.connect(TEST_DB)
    views = dict(con.execute("SELECT view, COUNT(*) FROM entity_ref GROUP BY view").fetchall())
    # 新体系：人物出 headshot+fullbody，场景出 scene_front/blocking/scene_top。
    # **只统计已经跑过的阶段**（本测试只跑了 characters 与 scenes，
    # props 阶段没跑，所以道具的 prop_ref 不该出现在预期里）。
    _exp = {}
    for _k, _n, _r, _d in ENTITIES:
        if _k == "character":
            _vs = S.CHARACTER_VIEWS.get(_r, [])
        elif _k == "location":
            _vs = S.SCENE_VIEWS.get(_r, [])
        else:
            continue          # 道具阶段本测试不跑
        for _v, _vn, _vd in _vs:
            _exp[_v] = _exp.get(_v, 0) + 1
    rep.add("视角分布正确", views == _exp,
            " ".join("%s=%d" % kv for kv in sorted(views.items()))
            + ("　预期 " + " ".join("%s=%d" % kv for kv in sorted(_exp.items()))
               if views != _exp else ""))
    prim = con.execute("SELECT COUNT(*) FROM entity_ref WHERE is_primary=1").fetchone()[0]
    rep.add("场景素材不抢占人物主参考图标记",
            prim == want_char_prim + want_scene_prim,
            "is_primary=%d（人物 %d + 场景 %d）"
            % (prim, want_char_prim, want_scene_prim))
    total_cost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger"
                             ).fetchone()[0]
    rep.add("累计成本", abs(total_cost - (want_portrait + want_turn) * PRICE_IMG) < 1e-6,
            "¥%.2f" % total_cost)
    con.close()

    # ---- 5. 幂等：重跑不应重复出图 ----
    print("\n=== 5. 重跑应幂等（不重复出图）===")
    Handler.requests.clear()
    run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "characters"], base)
    rep.add("没有发起新请求", len(Handler.requests) == 0, "%d 次" % len(Handler.requests))

    # ---- 6. --force 应重出 ----
    print("\n=== 6. --force 应重新生成 ===")
    Handler.requests.clear()
    run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "characters",
               "--force"], base)
    rep.add("发起了 %d 次请求" % want_portrait,
            len(Handler.requests) == want_portrait, "%d 次" % len(Handler.requests))

    # ---- 7. 第 7 阶段：首帧图 ----
    print("\n=== 7. 第 7 阶段 stage_firstframes.py ===")
    con = C.connect(TEST_DB)
    ent_id = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM entity")}
    shot_plan = [
        (["林薇", "出租屋"], "近景", "【提示词】第1镜 林薇站在窗边攥着化验单"),
        (["陈默", "出租屋"], "中景", "【提示词】第2镜 陈默推门进来"),
        (["林薇", "陈默"], "特写", "【提示词】第3镜 两人对峙，林薇抬眼"),
    ]
    shot_ids = []
    for i, (names, size, prompt) in enumerate(shot_plan, start=1):
        sid = con.execute(
            "INSERT INTO shot (episode_id, seq, duration_s, shot_size, camera_move,"
            " description, action, lighting, prompt, strategy, draws_planned) "
            "VALUES (?,?,4,?,'固定',?,?,?,?,'draft',3)",
            (eid, i, size, "第%d镜" % i, "动作", "冷调", prompt)).lastrowid
        for n in names:
            con.execute("INSERT INTO shot_entity (shot_id, entity_id, role) "
                        "VALUES (?,?,'subject')", (sid, ent_id[n]))
        shot_ids.append(sid)
    con.commit()
    con.close()
    rep.add("分镜与实体关联已备好", True, "%d 个镜头" % len(shot_ids))

    Handler.requests.clear()
    p = run_script("stage_firstframes.py",
                   ["--episode-id", str(eid), "--db", TEST_DB], base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-16:]))

    rep.add("每镜一次请求", len(Handler.requests) == len(shot_plan),
            "%d 次（预期 %d）" % (len(Handler.requests), len(shot_plan)))
    rep.add("请求用的是第 6 阶段定稿的提示词",
            bool(Handler.requests) and all(
                r["body"].get("prompt", "").startswith("【提示词】")
                for r in Handler.requests),
            (Handler.requests[0]["body"].get("prompt", "")[:30] + "…")
            if Handler.requests else "")
    rep.add("带上了实体参考图（图生图）",
            bool(Handler.requests) and all("image" in r["body"] for r in Handler.requests),
            "首条参考图 %d 张"
            % len(Handler.requests[0]["body"].get("image", [])) if Handler.requests else "")

    con = C.connect(TEST_DB)
    n_kf = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND keyframe_path "
                       "IS NOT NULL", (eid,)).fetchone()[0]
    rep.add("keyframe_path 已写入", n_kf == len(shot_plan),
            "%d / %d" % (n_kf, len(shot_plan)))
    miss = 0
    for r in con.execute("SELECT keyframe_path FROM shot WHERE episode_id=?", (eid,)):
        if not os.path.exists(C.abs_path(r["keyframe_path"])):
            miss += 1
    rep.add("首帧文件都已下载", miss == 0, "%d 个缺失" % miss)
    n_gen = con.execute("SELECT COUNT(*) FROM image_generation WHERE view LIKE 'shot:%'"
                        ).fetchone()[0]
    rep.add("image_generation 记了镜头维度", n_gen == len(shot_plan), "%d 条" % n_gen)
    cost7 = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger "
                        "WHERE category='image'").fetchone()[0]
    n_ok = con.execute("SELECT COUNT(*) FROM image_generation WHERE status='succeeded'"
                       ).fetchone()[0]
    # 期望：人物素材 + 场景素材 + --force 重出的人物素材 + 首帧图
    expect_imgs = want_portrait + want_turn + want_portrait + len(shot_plan)
    rep.add("成功出图总张数符合预期", n_ok == expect_imgs, "%d 张" % n_ok)
    rep.add("成本 = 成功张数 × 单价", abs(cost7 - n_ok * PRICE_IMG) < 1e-6,
            "¥%.2f（%d 张 × ¥%.2f）" % (cost7, n_ok, PRICE_IMG))
    con.close()

    Handler.requests.clear()
    run_script("stage_firstframes.py", ["--episode-id", str(eid), "--db", TEST_DB], base)
    rep.add("第 7 阶段重跑幂等", len(Handler.requests) == 0,
            "%d 次请求" % len(Handler.requests))

    Handler.requests.clear()
    run_script("stage_firstframes.py",
               ["--episode-id", str(eid), "--db", TEST_DB, "--take", "2",
                "--only", "1", "--force"], base)
    rep.add("--take 2 发起 2 次请求", len(Handler.requests) == 2,
            "%d 次" % len(Handler.requests))
    rep.add("候选落在隔离的临时素材根",
            os.path.isdir(os.path.join(TEST_ASSETS, "keyframes")),
            os.path.relpath(os.path.join(TEST_ASSETS, "keyframes"), C.ROOT))

    # ---- 8. 第 8 阶段：视频生成 ----
    print("\n=== 8. 第 8 阶段 stage_video.py ===")
    con = C.connect(TEST_DB)
    # 前 3 镜设为 draft 策略各抽 2 次；再加一镜 direct 策略
    con.execute("UPDATE shot SET strategy='draft', draws_planned=2 WHERE episode_id=?",
                (eid,))
    sid4 = con.execute(
        "INSERT INTO shot (episode_id, seq, duration_s, shot_size, camera_move,"
        " description, action, lighting, prompt, strategy, draws_planned) "
        "VALUES (?,4,4,'全景','固定','第4镜','动作','自然光','【提示词】第4镜 空镜','direct',1)",
        (eid,)).lastrowid
    # 让第 2 镜续接第 1 镜，并造一个尾帧文件，验证输入图解析顺序
    last_rel = os.path.join(".tools", "ark-test", "assets", "frames", "E01S001_last.png")
    os.makedirs(os.path.dirname(C.abs_path(last_rel)), exist_ok=True)
    with open(C.abs_path(last_rel), "wb") as f:
        f.write(PNG)
    s1 = con.execute("SELECT id FROM shot WHERE episode_id=? AND seq=1", (eid,)).fetchone()["id"]
    s2 = con.execute("SELECT id FROM shot WHERE episode_id=? AND seq=2", (eid,)).fetchone()["id"]
    con.execute("UPDATE shot SET last_frame_path=? WHERE id=?", (last_rel, s1))
    con.execute("UPDATE shot SET continues_from=? WHERE id=?", (s1, s2))
    con.commit()
    con.close()
    rep.add("分镜策略与续接关系已备好", True, "draft×3 + direct×1，S02 续接 S01")

    # 8.1 dry-run
    Handler.video_requests.clear()
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "draft",
                    "--dry-run"], base)
    rep.add("dry-run 退出码 0", p.returncode == 0, C.tail(p.stderr, 50))
    rep.add("dry-run 未提交任何任务", len(Handler.video_requests) == 0,
            "%d 次" % len(Handler.video_requests))
    rep.add("dry-run 给出预估成本", "预估成本" in p.stdout,
            next((l.strip() for l in p.stdout.splitlines() if "预估成本" in l), "")[:56])

    # 8.2 预算熔断
    Handler.video_requests.clear()
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "draft",
                    "--max-cost", "0.01"], base)
    rep.add("超预算时中止", p.returncode != 0 and "超过 --max-cost" in p.stdout,
            next((l.strip() for l in p.stdout.splitlines() if "max-cost" in l), "")[:56])
    rep.add("熔断时不提交任务", len(Handler.video_requests) == 0,
            "%d 次" % len(Handler.video_requests))

    # 8.3 真跑 draft
    Handler.video_requests.clear()
    Handler.video_polls.clear()
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "draft",
                    "--poll-interval", "0.05", "--poll-timeout", "30"], base)
    rep.add("draft 退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-16:]))
    rep.add("3 镜 × 2 抽 = 6 个任务", len(Handler.video_requests) == 6,
            "%d 个" % len(Handler.video_requests))
    if Handler.video_requests:
        b = Handler.video_requests[0]["body"]
        rep.add("样片带 draft=true 且 480p",
                b.get("draft") is True and b.get("resolution") == "480p",
                "draft=%s resolution=%s" % (b.get("draft"), b.get("resolution")))
        rep.add("content 带参考图",
                any(c.get("type") == "image_url" for c in b.get("content", [])),
                "content 段数 %d" % len(b.get("content", [])))
        rep.add("带 duration 与 ratio",
                b.get("duration") == 4 and b.get("ratio") == "9:16",
                "duration=%s ratio=%s" % (b.get("duration"), b.get("ratio")))
        rep.add("成片不携带 draft 关联字段",
                "draft_task_id" not in b)

    # 续接：第 2 镜的输入图应该是第 1 镜的尾帧
    con = C.connect(TEST_DB)
    n_draft = con.execute("SELECT COUNT(*) FROM generation WHERE is_draft=1 "
                          "AND status='succeeded'").fetchone()[0]
    rep.add("样片记录已落库", n_draft == 6, "%d 条" % n_draft)
    miss = 0
    for r in con.execute("SELECT local_path FROM generation WHERE local_path IS NOT NULL"):
        if not os.path.exists(C.abs_path(r["local_path"])):
            miss += 1
    rep.add("样片文件都已下载", miss == 0, "%d 个缺失" % miss)
    vcost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger "
                        "WHERE category='video'").fetchone()[0]
    rep.add("视频成本按秒计费（样片单价 × 秒数）",
            abs(vcost - 6 * 4 * PRICE_DRAFT) < 1e-6,
            "¥%.2f（6 任务 × 4 秒 × ¥%.2f）" % (vcost, PRICE_DRAFT))
    rep.add("每个任务都轮询过（≥2 次）",
            bool(Handler.video_polls) and all(v >= 2 for v in Handler.video_polls.values()),
            "%d 个任务被轮询" % len(Handler.video_polls))
    con.close()

    # 8.4 direct 模式
    Handler.video_requests.clear()
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "direct",
                    "--poll-interval", "0.05", "--poll-timeout", "30"], base)
    rep.add("direct 退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    rep.add("只处理 strategy=direct 的镜头", len(Handler.video_requests) == 1,
            "%d 个任务" % len(Handler.video_requests))
    if Handler.video_requests:
        b = Handler.video_requests[0]["body"]
        rep.add("直出是 1080p 且不带 draft",
                b.get("resolution") == "1080p" and "draft" not in b,
                "resolution=%s draft=%s" % (b.get("resolution"), b.get("draft")))
    con = C.connect(TEST_DB)
    sel = con.execute("SELECT selected_generation_id FROM shot WHERE id=?",
                      (sid4,)).fetchone()["selected_generation_id"]
    rep.add("单条成功时自动设为选中项", sel is not None, "selected=%s" % sel)
    con.close()

    # 8.5 final 模式：应带样片关联字段
    Handler.video_requests.clear()
    Handler.video_polls.clear()
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "final",
                    "--poll-interval", "0.05", "--poll-timeout", "30"], base)
    rep.add("final 退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    rep.add("3 个有样片的镜头各出一次成片", len(Handler.video_requests) == 3,
            "%d 个任务" % len(Handler.video_requests))
    rep.add("成片带样片关联字段（字段名未核实，改了 ark.DRAFT_REF_FIELD）",
            bool(Handler.video_requests) and all(
                "draft_task_id" in r["body"] for r in Handler.video_requests),
            "示例值 %s" % (Handler.video_requests[0]["body"].get("draft_task_id")
                           if Handler.video_requests else "-"))
    rep.add("成片是 1080p 且不带 draft",
            bool(Handler.video_requests) and all(
                r["body"].get("resolution") == "1080p" and "draft" not in r["body"]
                for r in Handler.video_requests))
    con = C.connect(TEST_DB)
    n_final = con.execute("SELECT COUNT(*) FROM generation WHERE is_draft=0 "
                          "AND status='succeeded' AND resolution='1080p'").fetchone()[0]
    rep.add("成片记录已落库", n_final == 4, "%d 条（含 direct 那条）" % n_final)
    con.close()

    # 8.6 创建成功但响应里没有任务 id
    Handler.state["mode"] = "video_bad"
    p = run_script("stage_video.py",
                   ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "draft",
                    "--only", "1", "--force"], base)
    blob = p.stdout + p.stderr
    rep.add("拿不到任务 id 时报错并落盘原始响应",
            p.returncode != 0 and "任务 id" in blob and "ark-debug" in blob,
            next((l.strip() for l in blob.splitlines() if "任务 id" in l), "")[:56])
    Handler.state["mode"] = "ok"

    # ---- 9. 401 鉴权失败 ----
    print("\n=== 9. 401 应提示检查 key 与模型权限 ===")
    Handler.state["mode"] = "401"
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "scenes",
                   "--force", "--only", "出租屋"], base)
    blob = p.stdout + p.stderr
    rep.add("退出码非 0", p.returncode != 0)
    rep.add("提示了鉴权与权限", "鉴权失败" in blob and "权限" in blob,
            next((l.strip() for l in blob.splitlines() if "鉴权失败" in l), "")[:60])
    Handler.state["mode"] = "ok"

    # ---- 8. 响应结构不符预期 ----
    print("\n=== 10. 响应结构不符预期时应给出可读错误 ===")
    Handler.state["mode"] = "bad_shape"
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "scenes",
                   "--force", "--only", "出租屋"], base)
    blob = p.stdout + p.stderr
    rep.add("退出码非 0", p.returncode != 0)
    rep.add("报错说明没有 data 数组并给出落盘路径",
            "data 数组" in blob and "ark-debug" in blob,
            next((l.strip() for l in blob.splitlines() if "data 数组" in l), "")[:64])
    dbg = os.path.join(C.ROOT, ".tools", "ark-debug")
    rep.add("原始响应确实落盘了", os.path.isdir(dbg) and len(os.listdir(dbg)) > 0,
            "%d 个调试文件" % (len(os.listdir(dbg)) if os.path.isdir(dbg) else 0))
    Handler.state["mode"] = "ok"

    # ---- 9. 429 限流退避重试 ----
    print("\n=== 11. 429 应退避重试后成功 ===")
    Handler.state["mode"] = "429"
    Handler.state["n429"] = 0
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "scenes",
                   "--force", "--only", "出租屋"], base)
    rep.add("退出码 0（重试后成功）", p.returncode == 0, C.tail(p.stderr, 50))
    rep.add("确实发生了 ≥2 次请求（含被限流那次）",
            len(Handler.requests) >= 2, "%d 次" % len(Handler.requests))
    Handler.state["mode"] = "ok"

    # ---- 10. 缺 key 时报错可操作 ----
    print("\n=== 12. 未配置密钥时应给出可操作的提示 ===")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DSH_ASSETS_ROOT=TEST_ASSETS,
               DSH_NO_DOTENV="1")   # 不读工作区 .env，否则这里清不掉 key
    env.pop("ARK_API_KEY", None)
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "pipeline", "stage_refs.py"),
                        "--episode-id", str(eid), "--db", TEST_DB, "--phase", "characters"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    blob = p.stdout + p.stderr
    rep.add("报错指出 ARK_API_KEY 并说明要开权限",
            p.returncode != 0 and "ARK_API_KEY" in blob and "权限" in blob,
            next((l.strip() for l in blob.splitlines() if "ARK_API_KEY" in l), "")[:60])

    # ---- 11. 隔离性 ----
    print("\n=== 13. 隔离性 ===")
    prod = C.connect(C.DEFAULT_DB)
    n = prod.execute("SELECT COUNT(*) FROM entity_ref").fetchone()[0]
    m = prod.execute("SELECT COUNT(*) FROM image_generation").fetchone()[0]
    prod.close()
    rep.add("生产库未被写入", n == 0 and m == 0, "entity_ref=%d image_generation=%d" % (n, m))
    real_refs = os.path.join(C.ROOT, "assets", "refs")
    rep.add("真实 assets/refs 未被创建", not os.path.exists(real_refs), real_refs)

    srv.shutdown()
    code = rep.finish()
    if args.keep:
        print("\n产物保留在:", os.path.relpath(TEST_ROOT, C.ROOT))
    else:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print("已清理测试目录")
    return code


if __name__ == "__main__":
    sys.exit(main())
