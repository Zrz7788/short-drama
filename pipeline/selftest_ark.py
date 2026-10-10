# -*- coding: utf-8 -*-
"""第 3/4/7 阶段的端到端自检。

在本地起一个**假的火山方舟服务**，让 stage_refs.py 与 stage_firstframes.py
真跑一遍：请求组装 -> 图片下载 -> 落库 -> 成本记账。

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

rep = C.Report("第 3/4/7 阶段自检")

ENTITIES = [
    ("character", "林薇", "main", "32岁女性，东亚面孔，齐肩黑直发，杏眼，左眉尾浅疤"),
    ("character", "陈默", "main", "40岁男性，寸头，方脸，右眼下有一颗痣"),
    ("location", "出租屋", "secondary", "十平米单间，铁架床，墙面有水渍"),
    ("prop", "化验单", "secondary", "A5 大小的医院化验单，被攥出褶皱"),
    ("character", "护士", "background", "年轻女性护士，白色制服，蓝色口罩"),
]

PNG = None


def make_png():
    from PIL import Image
    img = Image.new("RGB", (96, 96), (198, 120, 92))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []
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
        if self.path.startswith("/mock/"):
            self._send(200, PNG, "image/png")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"_raw": raw[:200].decode("utf-8", "replace")}
        Handler.requests.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        })

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
    global PNG
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    PNG = make_png()
    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)

    print("=== 0. 临时资源 ===")
    rep.add("构造测试 PNG", len(PNG) > 0, "%d 字节" % len(PNG))

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
    pid = con.execute("INSERT INTO project (name) VALUES ('Ark 自检')").lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    for kind, name, tier, desc in ENTITIES:
        con.execute("INSERT INTO entity (project_id, kind, name, role, description) "
                    "VALUES (?,?,?,?,?)", (pid, kind, name, tier, desc))
    con.commit()
    con.close()
    n_main = sum(1 for e in ENTITIES if e[2] == "main")
    n_sec = sum(1 for e in ENTITIES if e[2] == "secondary")
    n_bg = sum(1 for e in ENTITIES if e[2] == "background")
    rep.add("实体已灌入", True, "main=%d secondary=%d background=%d"
            % (n_main, n_sec, n_bg))

    # ---- 3. 定妆照 ----
    print("\n=== 3. 第 3 阶段 --phase portrait ===")
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "portrait"], base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    want_portrait = n_main + n_sec          # background 不出图
    rep.add("请求数与预期一致（main+secondary）",
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
    rep.add("定妆照标为主参考图", prim == want_portrait, "%d 张" % prim)
    n_gen = con.execute("SELECT COUNT(*) FROM image_generation WHERE status='succeeded'"
                        ).fetchone()[0]
    rep.add("image_generation 记录", n_gen == want_portrait, "%d 条" % n_gen)
    cost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger WHERE category='image'"
                       ).fetchone()[0]
    expect = want_portrait * 0.20
    rep.add("成本已入账（¥0.20/张）", abs(cost - expect) < 1e-6,
            "¥%.2f（预期 ¥%.2f）" % (cost, expect))
    missing_file = 0
    for r in con.execute("SELECT path FROM entity_ref"):
        if not os.path.exists(C.abs_path(r["path"])):
            missing_file += 1
    rep.add("图片文件都已下载", missing_file == 0, "%d 个缺失" % missing_file)
    bg_ref = con.execute("SELECT COUNT(*) FROM entity_ref r JOIN entity e ON e.id=r.entity_id "
                         "WHERE e.role='background'").fetchone()[0]
    rep.add("background 级没有出图", bg_ref == 0, "%d 张" % bg_ref)
    con.close()

    # ---- 4. 三视图 ----
    print("\n=== 4. 第 4 阶段 --phase turnaround ===")
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "turnaround"], base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    want_turn = n_main * 3
    rep.add("只给 main 出三视图（每人 3 个视角）",
            len(Handler.requests) == want_turn,
            "%d 次（预期 %d）" % (len(Handler.requests), want_turn))
    rep.add("三视图带了参考图（图生图）",
            bool(Handler.requests) and all("image" in r["body"] for r in Handler.requests),
            "首条 image 字段长度 %d" % len(Handler.requests[0]["body"].get("image", [""])[0])
            if Handler.requests else "")
    if Handler.requests:
        img = Handler.requests[0]["body"].get("image") or [""]
        rep.add("参考图是内联 data URL", img[0].startswith("data:image/"),
                img[0][:34] + "…")

    con = C.connect(TEST_DB)
    views = dict(con.execute("SELECT view, COUNT(*) FROM entity_ref GROUP BY view").fetchall())
    rep.add("视角分布正确",
            views.get("portrait") == want_portrait and views.get("front") == n_main
            and views.get("side") == n_main and views.get("back") == n_main,
            " ".join("%s=%d" % kv for kv in sorted(views.items())))
    prim = con.execute("SELECT COUNT(*) FROM entity_ref WHERE is_primary=1").fetchone()[0]
    rep.add("三视图不抢占主参考图标记", prim == want_portrait, "is_primary 仍为 %d" % prim)
    total_cost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger"
                             ).fetchone()[0]
    rep.add("累计成本", abs(total_cost - (want_portrait + want_turn) * 0.20) < 1e-6,
            "¥%.2f" % total_cost)
    con.close()

    # ---- 5. 幂等：重跑不应重复出图 ----
    print("\n=== 5. 重跑应幂等（不重复出图）===")
    Handler.requests.clear()
    run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "portrait"], base)
    rep.add("没有发起新请求", len(Handler.requests) == 0, "%d 次" % len(Handler.requests))

    # ---- 6. --force 应重出 ----
    print("\n=== 6. --force 应重新生成 ===")
    Handler.requests.clear()
    run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "portrait",
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
    # 期望：定妆照 + 三视图 + --force 重出的定妆照 + 首帧图
    expect_imgs = want_portrait + want_turn + want_portrait + len(shot_plan)
    rep.add("成功出图总张数符合预期", n_ok == expect_imgs, "%d 张" % n_ok)
    rep.add("成本 = 成功张数 × 单价", abs(cost7 - n_ok * 0.20) < 1e-6,
            "¥%.2f（%d 张 × ¥0.20）" % (cost7, n_ok))
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

    # ---- 8. 401 鉴权失败 ----
    print("\n=== 8. 401 应提示检查 key 与模型权限 ===")
    Handler.state["mode"] = "401"
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "turnaround",
                   "--force", "--only", "林薇"], base)
    blob = p.stdout + p.stderr
    rep.add("退出码非 0", p.returncode != 0)
    rep.add("提示了鉴权与权限", "鉴权失败" in blob and "权限" in blob,
            next((l.strip() for l in blob.splitlines() if "鉴权失败" in l), "")[:60])
    Handler.state["mode"] = "ok"

    # ---- 8. 响应结构不符预期 ----
    print("\n=== 9. 响应结构不符预期时应给出可读错误 ===")
    Handler.state["mode"] = "bad_shape"
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "turnaround",
                   "--force", "--only", "林薇"], base)
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
    print("\n=== 10. 429 应退避重试后成功 ===")
    Handler.state["mode"] = "429"
    Handler.state["n429"] = 0
    Handler.requests.clear()
    p = run_stage(["--episode-id", str(eid), "--db", TEST_DB, "--phase", "turnaround",
                   "--force", "--only", "林薇"], base)
    rep.add("退出码 0（重试后成功）", p.returncode == 0, C.tail(p.stderr, 50))
    rep.add("确实发生了 ≥2 次请求（含被限流那次）",
            len(Handler.requests) >= 2, "%d 次" % len(Handler.requests))
    Handler.state["mode"] = "ok"

    # ---- 10. 缺 key 时报错可操作 ----
    print("\n=== 11. 未配置密钥时应给出可操作的提示 ===")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DSH_ASSETS_ROOT=TEST_ASSETS)
    env.pop("ARK_API_KEY", None)
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "pipeline", "stage_refs.py"),
                        "--episode-id", str(eid), "--db", TEST_DB, "--phase", "portrait"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    blob = p.stdout + p.stderr
    rep.add("报错指出 ARK_API_KEY 并说明要开权限",
            p.returncode != 0 and "ARK_API_KEY" in blob and "权限" in blob,
            next((l.strip() for l in blob.splitlines() if "ARK_API_KEY" in l), "")[:60])

    # ---- 11. 隔离性 ----
    print("\n=== 12. 隔离性 ===")
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
