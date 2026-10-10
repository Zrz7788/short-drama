# -*- coding: utf-8 -*-
"""第 1/2 阶段的端到端自检。

在本地起一个假的 OpenAI 兼容服务，让 stage_script.py 与 stage_entities.py
真跑一遍：调用 -> JSON 抽取 -> 结构校验 -> 落库 -> 版本管理。

刻意让 mock 返回**带 ``` 围栏和前置解释文字**的内容，而不是干净的 JSON——
真实模型就是这样的，只测干净路径等于没测。

全程用临时库，不碰生产数据。

用法： python pipeline/selftest_llm.py [--keep]
"""
import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import llm as L  # noqa: E402

TEST_ROOT = os.path.join(C.ROOT, ".tools", "llm-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")

rep = C.Report("第 1/2 阶段自检")

# ---------------------------------------------------------------- 假模型的回复

SCENES = [
    (1, "内景 · 夜 · 出租屋", "出租屋", "夜", "她把化验单攥成一团", "", "钩子", 5),
    (2, "内景 · 夜 · 出租屋", "出租屋", "夜", "手机亮起，陌生号码来电", "你到底是谁？", "建立", 16),
    (3, "内景 · 夜 · 医院走廊", "医院走廊", "夜", "她追上前，对方转身就走", "站住！", "升级", 20),
    (4, "内景 · 夜 · 天台", "天台", "夜", "两人对峙，风把纸吹落", "那孩子是我的。", "升级", 16),
    (5, "近景 · 夜 · 天台", "天台", "夜", "她后退一步，镜头推向眼睛", "", "高潮", 16),
    (6, "全景 · 夜 · 天台", "天台", "夜", "手机从手里滑落，坠向楼下", "", "扣子", 7),
]

SCRIPT_OBJ = {
    "title": "化验单",
    "logline": "她拿到一张化验单，却发现有人比她更早知道结果。",
    "content": "# 化验单\n\n第1场 内景 · 夜 · 出租屋\n\n她把化验单攥成一团。\n",
    "scenes": [{"seq": s[0], "slug": s[1], "location": s[2], "time_of_day": s[3],
                "action": s[4], "dialogue": s[5], "beat": s[6], "duration_s": s[7]}
               for s in SCENES],
}

ENTITY_OBJ = {
    "entities": [
        {"kind": "character", "name": "林薇", "alias": "女主、薇薇", "tier": "main",
         "description": "32岁女性，东亚面孔，齐肩黑直发，杏眼，左眉尾浅疤",
         "style_note": "偏冷色调，常穿深灰"},
        {"kind": "character", "name": "陈默", "alias": "", "tier": "main",
         "description": "40岁男性，寸头，方脸，右眼下有一颗痣",
         "style_note": "硬朗，多穿深色风衣"},
        {"kind": "character", "name": "护士", "alias": "", "tier": "background",
         "description": "年轻女性护士，白色制服，戴蓝色口罩",
         "style_note": "无"},
        {"kind": "location", "name": "出租屋", "alias": "", "tier": "main",
         "description": "十平米单间，铁架床，墙面有水渍，唯一光源是床头台灯",
         "style_note": "暖黄台灯，2010年代城中村"},
        {"kind": "location", "name": "医院走廊", "alias": "", "tier": "secondary",
         "description": "深夜空走廊，日光灯管发白，地面反光",
         "style_note": "冷白光，高对比"},
        {"kind": "prop", "name": "化验单", "alias": "那张纸", "tier": "secondary",
         "description": "A5 大小的医院化验单，被攥出褶皱",
         "style_note": "白纸黑字，边缘皱"},
    ],
}


class Handler(http.server.BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        prompt = body["messages"][-1]["content"]
        Handler.calls.append(body.get("model"))

        if "编剧" in prompt:
            payload = SCRIPT_OBJ
        elif "美术统筹" in prompt:
            payload = ENTITY_OBJ
        else:
            payload = {}

        # 刻意弄脏：前置解释 + ``` 围栏，模拟真实模型的输出习惯
        content = ("好的，我来写这一集。\n\n```json\n"
                   + json.dumps(payload, ensure_ascii=False, indent=2)
                   + "\n```\n\n以上是完整结果。")
        if "MALFORMED" in prompt:
            content = "抱歉，我无法完成这个请求。"

        resp = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):
        pass


def run_stage(script, args, base_url=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if base_url:
        env.update(LLM_BASE_URL=base_url, LLM_API_KEY="test-key", LLM_MODEL="mock-model")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "pipeline", script)] + args,
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)

    # ---- 0. extract_json 单元检查（先测最容易出错的那一段）----
    print("=== 0. JSON 抽取（模型输出不干净是常态）===")
    cases = [
        ('{"a":1}', {"a": 1}, "纯 JSON"),
        ('```json\n{"a":1}\n```', {"a": 1}, "``` 围栏"),
        ('好的：\n{"a":1}\n以上', {"a": 1}, "前后夹解释文字"),
        ('{"a":"值里有 } 和 { 花括号"}', {"a": "值里有 } 和 { 花括号"}, "字符串里含花括号"),
        ('[{"a":1},{"a":2}]', [{"a": 1}, {"a": 2}], "顶层数组"),
    ]
    for text, want, label in cases:
        try:
            got = L.extract_json(text)
            rep.add("抽取：" + label, got == want, "" if got == want else str(got)[:40])
        except L.LLMError as e:
            rep.add("抽取：" + label, False, str(e)[:40])
    try:
        L.extract_json("完全没有 JSON 的一段话")
        rep.add("抽取：垃圾输入应报错", False, "竟然没报错")
    except L.LLMError:
        rep.add("抽取：垃圾输入应报错", True, "已按预期抛出 LLMError")

    # ---- 1. 起假模型服务 ----
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("\n=== 1. 假模型服务 ===")
    rep.add("已启动", True, base)

    # ---- 2. 临时库 ----
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep.add("临时库建立", p.returncode == 0)

    con = C.connect(TEST_DB)
    pid = con.execute("INSERT INTO project (name, logline) VALUES ('自检剧','测试')"
                      ).lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    con.commit()
    con.close()

    # ---- 3. 第 1 阶段：剧本 ----
    print("\n=== 3. 第 1 阶段 stage_script.py ===")
    p = run_stage("stage_script.py",
                  ["--episode-id", str(eid), "--db", TEST_DB,
                   "--brief", "一个关于化验单的悬疑短剧"], base_url=base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    con = C.connect(TEST_DB)
    s = con.execute("SELECT * FROM script WHERE episode_id=?", (eid,)).fetchone()
    rep.add("script 行已写入", s is not None)
    if s:
        rep.add("版本为 1 且为现行稿", s["version"] == 1 and s["is_current"] == 1,
                "v%s current=%s" % (s["version"], s["is_current"]))
        rep.add("标题正确", s["title"] == "化验单", s["title"])
    sc = con.execute("SELECT COUNT(*) FROM scene").fetchone()[0]
    rep.add("scene 行数", sc == len(SCENES), "%d（预期 %d）" % (sc, len(SCENES)))
    beats = [r[0] for r in con.execute(
        "SELECT beat FROM scene s JOIN script t ON t.id=s.script_id ORDER BY s.seq")]
    rep.add("beat 序列正确",
            beats == ["钩子", "建立", "升级", "升级", "高潮", "扣子"], " ".join(beats))
    total = con.execute("SELECT SUM(duration_s) FROM scene").fetchone()[0]
    rep.add("时长合计 80 秒", abs(total - 80) < 0.01, "%.0f 秒" % total)
    st = con.execute("SELECT status, title FROM episode WHERE id=?", (eid,)).fetchone()
    rep.add("episode 状态置为 scripted", st["status"] == "scripted", st["status"])
    rep.add("episode 标题已回填", st["title"] == "化验单", st["title"])
    con.close()

    # ---- 4. 重跑：版本应递增、旧稿降级 ----
    print("\n=== 4. 重跑第 1 阶段（版本管理）===")
    p = run_stage("stage_script.py",
                  ["--episode-id", str(eid), "--db", TEST_DB,
                   "--brief", "改一版"], base_url=base)
    con = C.connect(TEST_DB)
    vs = [(r["version"], r["is_current"]) for r in con.execute(
        "SELECT version, is_current FROM script WHERE episode_id=? ORDER BY version", (eid,))]
    rep.add("生成 v1/v2 且只有 v2 是现行稿", vs == [(1, 0), (2, 1)], str(vs))
    rep.add("旧稿未被删除（可追溯）", len(vs) == 2, "%d 个版本" % len(vs))
    n_scene = con.execute("SELECT COUNT(*) FROM scene sc JOIN script t ON t.id=sc.script_id "
                          "WHERE t.version=2").fetchone()[0]
    rep.add("v2 的场数正确", n_scene == len(SCENES), "%d" % n_scene)
    con.close()

    # ---- 5. 第 2 阶段：实体 ----
    print("\n=== 5. 第 2 阶段 stage_entities.py ===")
    p = run_stage("stage_entities.py", ["--episode-id", str(eid), "--db", TEST_DB],
                  base_url=base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-14:]))

    con = C.connect(TEST_DB)
    rows = con.execute("SELECT kind, name, role FROM entity ORDER BY kind, name").fetchall()
    rep.add("entity 行数", len(rows) == len(ENTITY_OBJ["entities"]),
            "%d（预期 %d）" % (len(rows), len(ENTITY_OBJ["entities"])))
    kinds = {r["kind"] for r in rows}
    rep.add("三类实体都提取到了", kinds == {"character", "location", "prop"},
            " ".join(sorted(kinds)))
    tiers = {}
    for r in rows:
        tiers[r["role"]] = tiers.get(r["role"], 0) + 1
    rep.add("分级写入 role 字段",
            tiers == {"main": 3, "secondary": 2, "background": 1},
            " ".join("%s=%d" % kv for kv in sorted(tiers.items())))
    m = con.execute("SELECT description FROM entity WHERE name='林薇'").fetchone()
    rep.add("description 是可粘贴的描述块",
            m and "杏眼" in m["description"], (m["description"][:36] + "…") if m else "-")
    con.close()

    # ---- 6. 幂等：重跑实体不应产生重复 ----
    print("\n=== 6. 重跑第 2 阶段（应幂等）===")
    run_stage("stage_entities.py", ["--episode-id", str(eid), "--db", TEST_DB], base_url=base)
    con = C.connect(TEST_DB)
    n2 = con.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    rep.add("实体数未增长", n2 == len(ENTITY_OBJ["entities"]),
            "%d（重跑前也是 %d）" % (n2, len(ENTITY_OBJ["entities"])))
    con.close()

    # ---- 7. --from-json：agent 驱动模式 ----
    print("\n=== 7. --from-json（跳过模型调用）===")
    fj = os.path.join(TEST_ROOT, "ents.json")
    with open(fj, "w", encoding="utf-8") as f:
        json.dump({"entities": [{"kind": "prop", "name": "天台铁门", "alias": "",
                                 "tier": "main", "description": "锈迹斑斑的铁门，漆面剥落",
                                 "style_note": "潮湿"}]}, f, ensure_ascii=False)
    p = run_stage("stage_entities.py",
                  ["--episode-id", str(eid), "--db", TEST_DB, "--from-json", fj])
    con = C.connect(TEST_DB)
    got = con.execute("SELECT role FROM entity WHERE name='天台铁门'").fetchone()
    rep.add("外部 JSON 成功入库", got is not None and got["role"] == "main",
            "role=%s" % (got["role"] if got else "-"))
    con.close()

    # ---- 8. 坏输出要干净失败，不能写脏库 ----
    print("\n=== 8. 模型返回垃圾时应干净失败 ===")
    p = run_stage("stage_script.py",
                  ["--episode-id", str(eid), "--db", TEST_DB,
                   "--brief", "MALFORMED 触发垃圾返回"], base_url=base)
    rep.add("退出码非 0", p.returncode != 0)
    blob = p.stdout + p.stderr
    err_line = next((l for l in blob.splitlines() if "JSON" in l), "")
    rep.add("给出了可读的原因", bool(err_line), err_line.strip()[:70])
    con = C.connect(TEST_DB)
    n3 = con.execute("SELECT COUNT(*) FROM script WHERE episode_id=?", (eid,)).fetchone()[0]
    rep.add("未写入脏数据（script 仍为 2 行）", n3 == 2, "%d 行" % n3)
    con.close()

    # ---- 9. 缺配置时的报错是否可操作 ----
    print("\n=== 9. 未配置 LLM 时应给出可操作的提示 ===")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("LLM_MODEL", None)
    env.pop("LLM_API_KEY", None)
    env.pop("ARK_API_KEY", None)
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "pipeline", "stage_script.py"),
                        "--episode-id", str(eid), "--db", TEST_DB, "--brief", "x"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    blob = p.stdout + p.stderr
    rep.add("未配置时报错并指出缺少什么",
            p.returncode != 0 and "LLM_MODEL" in blob and "LLM_API_KEY" in blob,
            C.tail(blob, 60))

    # ---- 隔离性 ----
    print("\n=== 10. 隔离性 ===")
    prod = C.connect(C.DEFAULT_DB)
    n = prod.execute("SELECT COUNT(*) FROM script").fetchone()[0]
    prov = prod.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    prod.close()
    rep.add("生产库未被写入", n == 0 and prov == 0, "script=%d entity=%d" % (n, prov))

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
