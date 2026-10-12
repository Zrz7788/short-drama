# -*- coding: utf-8 -*-
"""第 1/2/5/6 阶段的端到端自检。

在本地起一个假的 OpenAI 兼容服务，让四个阶段脚本真跑一遍：
调用 -> JSON 抽取 -> 结构校验 -> 落库 -> 版本管理 -> 逐镜组装提示词。

刻意让 mock 返回**带 ``` 围栏和前置解释文字**的内容，而不是干净的 JSON——
真实模型就是这样的，只测干净路径等于没测。

还包含一项反向验证：故意改坏提示词模板，确认"实体描述必须逐字出现"
这条保证会立刻失败，而不是等 80 集之后才发现主角每次长得都不一样。

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
from pipeline import stage_entities as E
from pipeline import stage_storyboard as SB
from pipeline import llm as L  # noqa: E402

TEST_ROOT = os.path.join(C.ROOT, ".tools", "llm-test")
TEST_DB = os.path.join(TEST_ROOT, "test.db")

rep = C.Report("第 1/2/5/6 阶段自检")
PROD0 = C.prod_fingerprint()   # 开工前记生产库指纹（供隔离性检查比对）

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
         "style_note": "偏冷色调，常穿深灰", "domain_fact": "story"},
        {"kind": "character", "name": "陈默", "alias": "", "tier": "main",
         "description": "40岁男性，寸头，方脸，右眼下有一颗痣",
         "style_note": "硬朗，多穿深色风衣", "domain_fact": "story"},
        {"kind": "character", "name": "护士", "alias": "", "tier": "background",
         "description": "年轻女性护士，白色制服，戴蓝色口罩",
         "style_note": "无", "domain_fact": "story"},
        # ⚠️ 场景必须回领域事实编号（v9 门禁）——这里回自检项目档案里的 F-01/F-02
        {"kind": "location", "name": "出租屋", "alias": "", "tier": "main",
         "description": "十平米单间，铁架床，墙面有水渍，唯一光源是床头台灯",
         "style_note": "暖黄台灯，2010年代城中村", "domain_fact": "F-01"},
        {"kind": "location", "name": "医院走廊", "alias": "", "tier": "secondary",
         "description": "深夜空走廊，日光灯管发白，地面反光",
         "style_note": "冷白光，高对比", "domain_fact": "F-02"},
        {"kind": "location", "name": "天台", "alias": "", "tier": "main",
         "description": "老旧居民楼天台，水泥地面有裂缝，四周是齐腰铁栏杆",
         "style_note": "夜，城市灯光背景，风大", "domain_fact": "F-01"},
        {"kind": "prop", "name": "化验单", "alias": "那张纸", "tier": "secondary",
         "description": "A5 大小的医院化验单，被攥出褶皱",
         "style_note": "白纸黑字，边缘皱", "domain_fact": "story"},
    ],
}

# ⚠️ 自检项目也要有一份项目档案（含「领域事实」章），否则第 2 阶段的门禁会拦。
# 项目名与生产项目不同名，不会互相干扰；跑完删掉。
TEST_PROJECT = "自检剧"
TEST_BRIEF = """# 自检剧

## 领域事实

### 已确认

| 编号 | 类别 | 事实 | 出处 | 置信度 |
|---|---|---|---|---|
| F-01 | 空间 | 城中村单间与老旧居民楼天台的常见结构与年代特征 | 自检夹具 | 高 |
| F-02 | 空间 | 深夜医院走廊的空间与照明特征 | 自检夹具 | 高 |

## 角色

见数据库。
"""

# 分镜计划：14 个镜头，合计 79 秒，draft 与 direct 都有，含单人/双人/纯空镜
SCENE_LOC = {1: "出租屋", 2: "出租屋", 3: "医院走廊", 4: "天台", 5: "天台", 6: "天台"}
SHOT_PLAN = [
    (1, 4, "特写", "固定", "draft", 5, [("林薇", "subject"), ("化验单", "prop")]),
    (2, 4, "近景", "推", "draft", 3, [("林薇", "subject")]),
    (2, 6, "中景", "固定", "draft", 3, [("林薇", "subject")]),
    (2, 6, "特写", "固定", "draft", 5, [("化验单", "prop")]),
    (3, 4, "全景", "跟", "direct", 1, [("林薇", "subject")]),
    (3, 8, "中景", "移", "draft", 3, [("林薇", "subject"), ("陈默", "subject")]),
    (3, 8, "近景", "固定", "draft", 4, [("林薇", "subject"), ("陈默", "subject")]),
    (4, 4, "全景", "升降", "direct", 1, []),
    (4, 6, "中景", "固定", "draft", 3, [("林薇", "subject"), ("陈默", "subject")]),
    (4, 6, "特写", "推", "draft", 5, [("林薇", "subject")]),
    (5, 4, "近景", "固定", "draft", 4, [("陈默", "subject")]),
    (5, 6, "特写", "推", "draft", 6, [("林薇", "subject")]),
    (5, 6, "中景", "拉", "draft", 3, [("林薇", "subject"), ("陈默", "subject")]),
    (6, 7, "远景", "固定", "direct", 1, []),
]


def build_storyboard():
    shots = []
    for i, (scene, dur, size, cam, strat, draws, ents) in enumerate(SHOT_PLAN, start=1):
        listing = [{"name": n, "role": r} for n, r in ents]
        # 纯空镜也必须挂上所在布景，否则这个镜头没有任何视觉锚点
        if not listing:
            listing = [{"name": SCENE_LOC[scene], "role": "background"}]
        elif not any(r == "background" for _, r in ents):
            listing.append({"name": SCENE_LOC[scene], "role": "background"})
        shots.append({
            "scene_seq": scene,
            "seq": i,
            "duration_s": dur,
            "shot_size": size,
            "camera_move": cam,
            # 运镜四件套：第 5 阶段校验要求这七项非空（缺一项模型就只能自由发挥）
            "camera_emotion": "聚焦式推进" if cam == "推" else "陪伴式平移",
            "movement_speed": "每秒 10-15 厘米，2 秒内完成",
            "camera_lock": "、".join(sorted(SB.CAMERA_LOCKS)[:2]),   # 从白名单取，不抄
            "camera_angle": "平视",
            "lens_mm": "85mm",
            "composition": "三分法，人物在右三分线",
            "dof": "极浅 f/1.8，背景虚化",
            "description": "第%d镜：%s" % (i, size),
            "action": "角色完成一个可见的动作，幅度适中，面部有细微表情变化",
            "dialogue": "你到底是谁？" if i == 7 else "",
            "sfx": "夜风声" if scene in (4, 5, 6) else "室内安静",
            "lighting": "冷调夜景，侧逆光" if scene != 1 else "床头台灯暖光",
            # 连续性具名锁（v8）：轴线/计数/静止/正面重申/画面文字归属。
            # 依据《Seedance提示词与连续性锁定》——约束属于具名正块，不是尾部负面词。
            "screen_direction": "林薇永远在画面左侧，陈默永远在右侧；正反打不越轴，两人位置绝不互换",
            "count_lock": "画面里正好两个人；镜面与玻璃倒影里都不能多出第三个人",
            "stillness_lock": "没有人站起来、没有人离开画面、化验单始终攥在林薇右手里",
            "positive_locks": "化验单的褶皱位置不变、走廊日光灯保持同一色温、两人的相对站位不变",
            # 精确文字只归属到唯一一镜：只有第 4 镜读化验单上的日期
            "on_screen_text": "化验单日期：11月6日" if i == 4 else "",
            "entities": listing,
            "strategy": strat,
            "draws_planned": draws,
        })
    return {"shots": shots}


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
        elif "分镜师" in prompt:
            payload = build_storyboard()
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
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               DSH_NO_DOTENV="1")   # 不读工作区 .env，保持自检密闭
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
    print("\n=== 2. 临时库 ===")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep.add("临时库建立", p.returncode == 0)

    con = C.connect(TEST_DB)
    pid = con.execute("INSERT INTO project (name, logline) VALUES (?, '测试')",
                      (TEST_PROJECT,)).lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    con.commit()
    con.close()

    # 项目档案（含「领域事实」章）。第 2 阶段的门禁要求它必须存在且可解析，
    # 否则直接阻断——这里给自检项目补一份，跑完删掉，不污染生产档案。
    BRIEF_PATH = os.path.join(C.ROOT, "knowledge", "03-项目档案",
                              "%s.md" % TEST_PROJECT)
    _brief_made = not os.path.exists(BRIEF_PATH)
    if _brief_made:
        os.makedirs(os.path.dirname(BRIEF_PATH), exist_ok=True)
        with open(BRIEF_PATH, "w", encoding="utf-8") as f:
            f.write(TEST_BRIEF)
    rep.add("自检项目的项目档案已就位", os.path.exists(BRIEF_PATH))

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
    rep.add("三类实体都提取到了", kinds == E.KINDS,   # 不抄枚举
            " ".join(sorted(kinds)))
    tiers = {}
    for r in rows:
        tiers[r["role"]] = tiers.get(r["role"], 0) + 1
    n_main = sum(1 for e in ENTITY_OBJ["entities"] if e["tier"] == "main")
    n_sec = sum(1 for e in ENTITY_OBJ["entities"] if e["tier"] == "secondary")
    n_bg = sum(1 for e in ENTITY_OBJ["entities"] if e["tier"] == "background")
    rep.add("分级写入 role 字段",
            tiers == {"main": n_main, "secondary": n_sec, "background": n_bg},
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
                                 "style_note": "潮湿",
                                 # 纯剧情道具：显式声明不需要领域事实（v9）
                                 "domain_fact": "story"}]}, f, ensure_ascii=False)
    p = run_stage("stage_entities.py",
                  ["--episode-id", str(eid), "--db", TEST_DB, "--from-json", fj])
    con = C.connect(TEST_DB)
    got = con.execute("SELECT role, domain_fact FROM entity WHERE name='天台铁门'"
                      ).fetchone()
    rep.add("外部 JSON 成功入库", got is not None and got["role"] == "main",
            "role=%s domain_fact=%s" % (got["role"] if got else "-",
                                        got["domain_fact"] if got else "-"))
    rep.add("domain_fact 也入库了", got is not None and got["domain_fact"] == "story",
            got["domain_fact"] if got else "-")
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
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               DSH_NO_DOTENV="1")   # 不读工作区 .env，保持自检密闭
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

    # ---- 11. 第 5 阶段：分镜 ----
    print("\n=== 10. 第 5 阶段 stage_storyboard.py ===")
    p = run_stage("stage_storyboard.py",
                  ["--episode-id", str(eid), "--db", TEST_DB], base_url=base)
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-16:]))

    con = C.connect(TEST_DB)
    n_shots = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=?",
                          (eid,)).fetchone()[0]
    rep.add("shot 行数", n_shots == len(SHOT_PLAN),
            "%d（预期 %d）" % (n_shots, len(SHOT_PLAN)))
    tot = con.execute("SELECT SUM(duration_s) FROM shot WHERE episode_id=?",
                      (eid,)).fetchone()[0]
    rep.add("镜头时长合计", abs(tot - 79) < 0.01, "%.0f 秒（预期 79）" % tot)
    strat = dict(con.execute("SELECT strategy, COUNT(*) FROM shot WHERE episode_id=? "
                             "GROUP BY strategy", (eid,)).fetchall())
    rep.add("抽卡策略已分化（不是一刀切）",
            strat.get("draft", 0) > 0 and strat.get("direct", 0) > 0,
            " ".join("%s=%d" % kv for kv in sorted(strat.items())))
    n_link = con.execute("SELECT COUNT(*) FROM shot_entity se JOIN shot s ON s.id=se.shot_id "
                         "WHERE s.episode_id=?", (eid,)).fetchone()[0]
    rep.add("shot_entity 关联已写入", n_link > 0, "%d 条" % n_link)
    orphan = con.execute(
        "SELECT COUNT(*) FROM shot s WHERE s.episode_id=? AND NOT EXISTS "
        "(SELECT 1 FROM shot_entity se WHERE se.shot_id = s.id)", (eid,)).fetchone()[0]
    rep.add("没有孤立镜头（纯空镜也挂了布景）", orphan == 0, "%d 个无实体" % orphan)
    filled = con.execute(
        "SELECT COUNT(*) FROM shot WHERE episode_id=? AND action IS NOT NULL AND action<>'' "
        "AND lighting IS NOT NULL AND lighting<>''", (eid,)).fetchone()[0]
    rep.add("action/lighting 写进独立字段（不是塞进 prompt）",
            filled == len(SHOT_PLAN), "%d / %d" % (filled, len(SHOT_PLAN)))
    stt = con.execute("SELECT status FROM episode WHERE id=?", (eid,)).fetchone()[0]
    rep.add("episode 状态置为 storyboarded", stt == "storyboarded", stt)
    con.close()

    # ---- 12. 第 6 阶段：提示词定稿 ----
    print("\n=== 11. 第 6 阶段 stage_prompts.py ===")
    p = run_stage("stage_prompts.py", ["--episode-id", str(eid), "--db", TEST_DB])
    rep.add("退出码 0", p.returncode == 0, C.tail(p.stderr, 60))
    if p.returncode:
        print("\n".join(p.stdout.strip().splitlines()[-16:]))

    con = C.connect(TEST_DB)
    n_p = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND prompt IS NOT NULL "
                      "AND prompt<>''", (eid,)).fetchone()[0]
    rep.add("全部镜头都有 prompt", n_p == len(SHOT_PLAN),
            "%d / %d" % (n_p, len(SHOT_PLAN)))
    n_n = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND negative_prompt "
                      "IS NOT NULL AND negative_prompt<>''", (eid,)).fetchone()[0]
    rep.add("全部镜头都有 negative_prompt", n_n == len(SHOT_PLAN), "%d 个" % n_n)

    miss = 0
    for r in con.execute("SELECT id, prompt FROM shot WHERE episode_id=?", (eid,)):
        for e in con.execute("SELECT e.description FROM shot_entity se "
                            "JOIN entity e ON e.id = se.entity_id WHERE se.shot_id=?",
                            (r["id"],)):
            if e["description"] not in r["prompt"]:
                miss += 1
    rep.add("实体描述逐字出现在 prompt 中", miss == 0, "%d 处缺失" % miss)

    multi = con.execute(
        "SELECT COUNT(*) FROM (SELECT se.shot_id FROM shot_entity se JOIN shot s ON s.id=se.shot_id "
        "WHERE s.episode_id=? GROUP BY se.shot_id "
        "HAVING SUM(CASE WHEN se.role='subject' THEN 1 ELSE 0 END) > 1)", (eid,)).fetchone()[0]
    fused = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND "
                        "negative_prompt LIKE '%人物融合%'", (eid,)).fetchone()[0]
    rep.add("多主体镜头追加了一致性负面词", multi > 0 and fused == multi,
            "多主体 %d 个，已追加 %d 个" % (multi, fused))
    con.close()

    # ---- 13. 反向验证：模板被改坏时必须立刻失败 ----
    print("\n=== 12. 反向验证：破坏模板应立刻失败 ===")
    tpl = os.path.join(C.ROOT, "pipeline", "prompts", "shot_prompt.md")
    backup = tpl + ".selftest-bak"
    shutil.copy2(tpl, backup)
    try:
        with open(tpl, "w", encoding="utf-8") as f:
            # 故意丢掉 $entity_block —— 逐字校验应当因此失败。
            # 变量名要与当前模板一致，否则会先撞上"未知变量"，测的就不是逐字校验了。
            f.write("【景别】$shot_size 【运镜】$camera_line\n\n【动作】$action\n")
        p = run_stage("stage_prompts.py", ["--episode-id", str(eid), "--db", TEST_DB])
        blob = p.stdout + p.stderr
        rep.add("模板丢掉实体块时报错退出", p.returncode != 0, "退出码 %d" % p.returncode)
        err = next((l for l in blob.splitlines() if "逐字" in l), "")
        rep.add("报错指明了原因（逐字校验）", bool(err), err.strip()[:60])
    finally:
        shutil.copy2(backup, tpl)
        os.remove(backup)

    p = run_stage("stage_prompts.py", ["--episode-id", str(eid), "--db", TEST_DB])
    rep.add("恢复模板后可重新跑通", p.returncode == 0, C.tail(p.stderr, 40))

    # ---- 14. 改了实体描述后重跑，提示词应随之更新 ----
    print("\n=== 13. 实体描述变更应传导到所有含它的镜头 ===")
    con = C.connect(TEST_DB)
    con.execute("UPDATE entity SET description=? WHERE name='林薇'",
                ("32岁女性，东亚面孔，齐肩黑直发，杏眼，左眉尾浅疤，新加了红色围巾",))
    con.commit()
    con.close()
    run_stage("stage_prompts.py", ["--episode-id", str(eid), "--db", TEST_DB])
    con = C.connect(TEST_DB)
    hit = con.execute(
        "SELECT COUNT(*) FROM shot s JOIN shot_entity se ON se.shot_id = s.id "
        "JOIN entity e ON e.id = se.entity_id WHERE s.episode_id=? AND e.name='林薇' "
        "AND s.prompt LIKE '%新加了红色围巾%'", (eid,)).fetchone()[0]
    total_lin = con.execute(
        "SELECT COUNT(DISTINCT s.id) FROM shot s JOIN shot_entity se ON se.shot_id = s.id "
        "JOIN entity e ON e.id = se.entity_id WHERE s.episode_id=? AND e.name='林薇'",
        (eid,)).fetchone()[0]
    rep.add("新描述传导到了全部含该实体的镜头", total_lin > 0 and hit == total_lin,
            "%d / %d 个镜头已更新" % (hit, total_lin))
    con.close()

    # ---- 隔离性 ----
    print("\n=== 14. 隔离性 ===")
    prod = C.connect(C.DEFAULT_DB)
    n = prod.execute("SELECT COUNT(*) FROM script").fetchone()[0]
    prov = prod.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    prod.close()
    _unch, _why = C.prod_unchanged(PROD0)
    rep.add("生产库未被写入", _unch, _why)

    srv.shutdown()
    code = rep.finish()
    # 自检造的项目档案要清掉，否则会留在知识库里（它不是真的项目）
    if _brief_made and os.path.exists(BRIEF_PATH):
        os.remove(BRIEF_PATH)
    if args.keep:
        print("\n产物保留在:", os.path.relpath(TEST_ROOT, C.ROOT))
    else:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print("已清理测试目录")
    return code


if __name__ == "__main__":
    sys.exit(main())
