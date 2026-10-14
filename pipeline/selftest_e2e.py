# -*- coding: utf-8 -*-
"""全链路集成测试：让数据真的从剧本流到成片。

前面三组自检是按阶段分组的（1/2/5/6、3/4/7/8、9/10），
**没有任何一条测试让数据穿过全部十个阶段**。这个文件补上那个缺口。

做法：本地起两个假服务（一个假的 OpenAI 兼容 LLM，一个假的火山方舟），
按生产顺序真跑十个阶段，最后产出一集成片并校验它。

关键是假 Ark 必须返回**真能解码的 MP4**——否则第 9 阶段的抽帧与第 10 阶段的
拼接没法真跑，那这个测试就退化成了接口 mocking，证明不了任何东西。

阶段顺序刻意按推荐的流程走：**实体出图在分镜之前**。
分镜的提示词质量取决于是否已经看到并锁定视觉方向；先写 20 条再发现风格不对，
就是全部返工。

假服务的实现直接复用两个自检模块里的 Handler 与测试数据——它们导入时无副作用。

用法： python pipeline/selftest_e2e.py [--keep]
"""
import argparse
import http.server
import os
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import common as C  # noqa: E402
from pipeline import stage_entities as E
from pipeline import selftest_llm as L  # noqa: E402  假 LLM 服务与剧本/实体/分镜数据
from pipeline import selftest_ark as K  # noqa: E402  假 Ark 服务

TEST_ROOT = os.path.join(C.ROOT, ".tools", "e2e-test")
TEST_DB = os.path.join(TEST_ROOT, "drama.db")
TEST_ASSETS = os.path.join(TEST_ROOT, "assets")
TEST_OUT = os.path.join(TEST_ROOT, "out")

rep = C.Report("全链路集成测试")
PROD0 = C.prod_fingerprint()   # 开工前记生产库指纹（供隔离性检查比对）


def _assets_snapshot():
    """真实 assets/ 的文件清单快照（相对路径集合）。

    ⚠️ 别用「真实 assets/ 不存在」当隔离性断言——**真跑过第 3/4 阶段之后它必然存在**，
    而那正是生产该有的样子。要比的是"自检有没有往里加东西"。
    """
    root = os.path.join(C.ROOT, "assets")
    out = set()
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            out.add(os.path.relpath(os.path.join(dp, fn), root))
    return out


ASSETS0 = _assets_snapshot()   # 开工前记素材目录清单


def run(script, args, llm_base, ark_base):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(
        LLM_BASE_URL=llm_base, LLM_API_KEY="test-key", LLM_MODEL="mock-model",
        ARK_BASE_URL=ark_base, ARK_API_KEY="test-key",
        DSH_ASSETS_ROOT=TEST_ASSETS,
        DSH_NO_DOTENV="1",   # 密闭：不读工作区 .env 的真实凭证
        # 单集预算闸门：自检里"花的钱"是假 Ark 造出来的（每镜 ¥4），
        # 跑满十个阶段必然超过真实上限——**这是测试数据，不是真实消费**，
        # 所以在这里把上限放到很高。真实生产用默认 150 或自己设。
        DSH_EPISODE_BUDGET="100000",
    )
    return subprocess.run([sys.executable, "-X", "utf8",
                           os.path.join(C.ROOT, "pipeline", script)] + args,
                          cwd=C.ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def stage(rep, label, script, args, llm_base, ark_base, expect_ok=True):
    """跑一个阶段并断言退出码。失败时把子进程输出打出来，否则排查全靠猜。"""
    p = run(script, args, llm_base, ark_base)
    ok = (p.returncode == 0) == expect_ok
    rep.add(label, ok, C.tail(p.stderr, 56) or ("退出码 %d" % p.returncode))
    if not ok:
        print("  --- stdout(tail) ---")
        print("\n".join(p.stdout.strip().splitlines()[-22:]))
        print("  --- stderr(tail) ---")
        print("\n".join(p.stderr.strip().splitlines()[-8:]))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if os.path.isdir(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)
    os.makedirs(TEST_ROOT)

    # ---- 0. 假服务与素材 ----
    print("=== 0. 假服务与素材 ===")
    K.PNG = K.make_png()
    K.VIDEO, video_note = K.make_mp4(TEST_ROOT)
    rep.add("测试用 MP4 可解码", len(K.VIDEO) > 4096,
            "%s，%d 字节" % (video_note, len(K.VIDEO)))
    if len(K.VIDEO) <= 4096:
        rep.add("需要真 MP4 才能跑抽帧与拼接", False,
                "ffmpeg 不可用或生成失败，本测试无法证明第 9/10 阶段")
        return rep.finish()

    llm_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), L.Handler)
    ark_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), K.Handler)
    llm_base = "http://127.0.0.1:%d" % llm_srv.server_address[1]
    ark_base = "http://127.0.0.1:%d" % ark_srv.server_address[1]
    threading.Thread(target=llm_srv.serve_forever, daemon=True).start()
    threading.Thread(target=ark_srv.serve_forever, daemon=True).start()
    rep.add("假 LLM 服务已启动", True, llm_base)
    rep.add("假 Ark 服务已启动", True, ark_base)

    # ---- 1. 临时库与项目 ----
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(C.ROOT, "db", "init_db.py"),
                        "--db", TEST_DB, "--rebuild"],
                       cwd=C.ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep.add("临时库建立", p.returncode == 0)
    con = C.connect(TEST_DB)
    # 价目**从生产库复制**，不硬编码。
    # 原来写死 0.20 / 1.45——0.20 是我们已弃用的 seedream-4-0 的价格，
    # 当前默认模型的真实价格是 0.30。硬编码意味着价格一变测试就测老世界。
    # 不补价目的话 stage_refs / stage_firstframes 会因"价目缺失"直接拒绝继续
    # （那是刻意的安全行为），整个链路就断在这。
    from pipeline import ark as _A
    _prod = C.connect(C.DEFAULT_DB)
    for mid, unit in ((_A.MODEL_IMAGE, "image"), (_A.MODEL_VIDEO_25, "second")):
        if con.execute("SELECT 1 FROM price_book WHERE model_id=? AND unit=?",
                       (mid, unit)).fetchone():
            continue
        r = _prod.execute(
            "SELECT variant, unit_price, confidence FROM price_book "
            "WHERE model_id=? AND unit=? ORDER BY unit_price LIMIT 1",
            (mid, unit)).fetchone()
        if r:
            con.execute("INSERT INTO price_book (model_id, variant, unit, unit_price,"
                        " confidence, source) VALUES (?,?,?,?,?,'复制自生产库')",
                        (mid, r["variant"], unit, r["unit_price"], r["confidence"]))
    _prod.close()
    pid = con.execute("INSERT INTO project (name, logline, target_episode_seconds) "
                      "VALUES ('端到端自检剧','一条化验单引发的对峙',80)").lastrowid
    eid = con.execute("INSERT INTO episode (project_id, seq, target_seconds) "
                      "VALUES (?,1,80)", (pid,)).lastrowid
    con.commit()
    con.close()
    rep.add("项目与第 1 集已建", True, "episode_id=%d" % eid)

    # ⚠️ 项目档案（含「领域事实」章）。第 2 阶段会拿它做**硬门禁**——没有就阻断
    # 后续所有阶段，所以端到端自检必须给它一份。跑完删掉，不污染生产档案。
    E2E_BRIEF = os.path.join(C.ROOT, "knowledge", "03-项目档案", "端到端自检剧.md")
    _e2e_brief = not os.path.exists(E2E_BRIEF)
    if _e2e_brief:
        os.makedirs(os.path.dirname(E2E_BRIEF), exist_ok=True)
        with open(E2E_BRIEF, "w", encoding="utf-8") as f:
            f.write("# 端到端自检剧\n\n## 领域事实\n\n### 已确认\n\n"
                    "| 编号 | 类别 | 事实 | 出处 | 置信度 |\n|---|---|---|---|---|\n"
                    "| F-01 | 空间 | 城中村出租屋的常见结构与年代特征 | 自检夹具 | 高 |\n"
                    "| F-02 | 空间 | 深夜医院走廊的空间与照明特征 | 自检夹具 | 高 |\n"
                    "| F-03 | 空间 | 老旧居民楼天台的常见结构与铁栏杆 | 自检夹具 | 高 |\n\n"
                    "## 角色\n\n见数据库。\n")
    rep.add("自检项目的项目档案已就位", os.path.exists(E2E_BRIEF))

    print("\n=== 1-2. 剧本 → 实体（LLM 阶段）===")
    stage(rep, "第 1 阶段 生成剧本", "stage_script.py",
          ["--episode-id", str(eid), "--db", TEST_DB,
           "--brief", "一个关于化验单的悬疑短剧"], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_sc = con.execute("SELECT COUNT(*) FROM scene").fetchone()[0]
    rep.add("剧本已落库", n_sc == len(L.SCENES), "%d 场" % n_sc)
    con.close()

    stage(rep, "第 2 阶段 提取实体", "stage_entities.py",
          ["--episode-id", str(eid), "--db", TEST_DB], llm_base, ark_base)
    con = C.connect(TEST_DB)
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM entity GROUP BY kind").fetchall())
    rep.add("三类实体齐备",
            set(kinds) == E.KINDS,   # 从 stage_entities 取，不抄枚举
            " ".join("%s=%d" % kv for kv in sorted(kinds.items())))
    con.close()

    print("\n=== 3-4. 人物素材 → 场景素材（Ark 图像阶段）===")
    stage(rep, "第 3 阶段 定妆照", "stage_refs.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--phase", "characters"],
          llm_base, ark_base)
    stage(rep, "第 4 阶段 场景素材", "stage_refs.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--phase", "scenes"],
          llm_base, ark_base)
    con = C.connect(TEST_DB)
    views = dict(con.execute("SELECT view, COUNT(*) FROM entity_ref GROUP BY view").fetchall())
    n_main = con.execute("SELECT COUNT(*) FROM entity WHERE role='main'").fetchone()[0]
    # 新素材体系（三视图已废弃）：人物出 fullbody（人人有）+ headshot（main 才有），
    # 场景出 scene_front。按实体数推导，不写死视角名与张数。
    n_char = con.execute("SELECT COUNT(*) FROM entity WHERE kind='character' "
                         "AND role IN ('main','secondary')").fetchone()[0]
    n_char_main = con.execute("SELECT COUNT(*) FROM entity WHERE kind='character' "
                              "AND role='main'").fetchone()[0]
    n_loc = con.execute("SELECT COUNT(*) FROM entity WHERE kind='location' "
                        "AND role IN ('main','secondary')").fetchone()[0]
    rep.add("参考图视角齐备（人物全身照 + main 大头照 + 场景正视图）",
            views.get("fullbody", 0) >= n_char
            and views.get("headshot", 0) >= n_char_main
            and views.get("scene_front", 0) >= n_loc,
            " ".join("%s=%d" % kv for kv in sorted(views.items()))
            + "　（人物%d/main%d，场景%d）" % (n_char, n_char_main, n_loc))
    bg = con.execute("SELECT COUNT(*) FROM entity_ref r JOIN entity e ON e.id=r.entity_id "
                     "WHERE e.role='background'").fetchone()[0]
    rep.add("background 级未出图（分级兑现成钱）", bg == 0, "%d 张" % bg)
    con.close()

    print("\n=== 5-6. 分镜 → 提示词 ===")
    stage(rep, "第 5 阶段 生成分镜", "stage_storyboard.py",
          ["--episode-id", str(eid), "--db", TEST_DB], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_shot = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=?", (eid,)).fetchone()[0]
    strat = dict(con.execute("SELECT strategy, COUNT(*) FROM shot WHERE episode_id=? "
                             "GROUP BY strategy", (eid,)).fetchall())
    rep.add("分镜已落库", n_shot == len(L.SHOT_PLAN), "%d 个镜头" % n_shot)
    rep.add("抽卡策略已分化", len(strat) == 2,
            " ".join("%s=%d" % kv for kv in sorted(strat.items())))
    con.close()

    stage(rep, "第 6 阶段 提示词定稿", "stage_prompts.py",
          ["--episode-id", str(eid), "--db", TEST_DB], llm_base, ark_base)
    con = C.connect(TEST_DB)
    miss = 0
    for r in con.execute("SELECT id, prompt FROM shot WHERE episode_id=?", (eid,)):
        for e in con.execute("SELECT e.description FROM shot_entity se JOIN entity e "
                            "ON e.id=se.entity_id WHERE se.shot_id=?", (r["id"],)):
            if e["description"] not in (r["prompt"] or ""):
                miss += 1
    n_p = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND prompt<>''",
                      (eid,)).fetchone()[0]
    rep.add("全部镜头提示词已定稿", n_p == n_shot, "%d / %d" % (n_p, n_shot))
    rep.add("实体描述逐字内联（跨镜头一致性的根据）", miss == 0, "%d 处缺失" % miss)
    con.close()

    print("\n=== 7. 首帧图（最后一道便宜的关卡）===")
    stage(rep, "第 7 阶段 生成首帧图", "stage_firstframes.py",
          ["--episode-id", str(eid), "--db", TEST_DB], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_kf = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND keyframe_path "
                       "IS NOT NULL AND keyframe_path<>''", (eid,)).fetchone()[0]
    rep.add("每镜都有首帧图", n_kf == n_shot, "%d / %d" % (n_kf, n_shot))
    con.close()

    print("\n=== 8. 视频生成（draft → direct → final）===")
    stage(rep, "第 8 阶段 draft", "stage_video.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "draft",
           "--poll-interval", "0.05", "--poll-timeout", "30"], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_draft = con.execute("SELECT COUNT(*) FROM generation WHERE is_draft=1 "
                          "AND status='succeeded'").fetchone()[0]
    # SHOT_PLAN 是 (场, 时长, 景别, 运镜, 策略, 抽卡数, 实体)
    want_draft = sum(p[5] for p in L.SHOT_PLAN if p[4] == "draft")
    rep.add("样片数量 = 各 draft 镜头的计划抽卡数之和",
            n_draft == want_draft, "%d（预期 %d）" % (n_draft, want_draft))
    con.close()

    # strategy='direct' 的镜头不走 draft，必须单独跑 direct，
    # 否则它们没有任何视频，第 10 阶段拼接会直接中止
    stage(rep, "第 8 阶段 direct", "stage_video.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "direct",
           "--poll-interval", "0.05", "--poll-timeout", "30"], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_direct = con.execute("SELECT COUNT(*) FROM generation WHERE is_draft=0 "
                           "AND resolution='1080p' AND status='succeeded'").fetchone()[0]
    want_direct = sum(1 for p in L.SHOT_PLAN if p[4] == "direct")
    rep.add("direct 镜头已直出", n_direct == want_direct,
            "%d（预期 %d）" % (n_direct, want_direct))
    con.close()

    stage(rep, "第 8 阶段 final", "stage_video.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--mode", "final",
           "--poll-interval", "0.05", "--poll-timeout", "30"], llm_base, ark_base)
    con = C.connect(TEST_DB)
    n_final = con.execute("SELECT COUNT(*) FROM generation WHERE is_draft=0 "
                          "AND status='succeeded'").fetchone()[0]
    n_sel = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND "
                        "selected_generation_id IS NOT NULL", (eid,)).fetchone()[0]
    rep.add("成片总数（direct + final）", n_final >= n_shot, "%d 条" % n_final)
    rep.add("每镜都有选中的成片", n_sel == n_shot, "%d / %d" % (n_sel, n_shot))
    con.close()

    print("\n=== 9-10. 抽帧衔接 → 拼接成集 ===")
    stage(rep, "第 9 阶段 抽帧与衔接", "frames.py",
          ["--episode-id", str(eid), "--db", TEST_DB], llm_base, ark_base)
    con = C.connect(TEST_DB)
    rows = [dict(r) for r in con.execute(
        "SELECT id, seq, first_frame_path, last_frame_path, continues_from FROM shot "
        "WHERE episode_id=? ORDER BY seq", (eid,))]
    rep.add("每镜都抽出了首尾帧",
            all(r["last_frame_path"] and r["first_frame_path"] for r in rows),
            "%d / %d" % (sum(1 for r in rows if r["last_frame_path"]), len(rows)))
    # 默认**不**串接：连续性靠角色资产 + 具名锁，不靠上一镜的尾帧。
    # （110 分钟 Seedance 长片全片禁用起始帧；中文短剧 SOP 也把首尾帧标为可跳。）
    rep.add("默认不建立续接关系（全片硬切）",
            all(r["continues_from"] is None for r in rows),
            "有续接的镜数 %d" % sum(1 for r in rows if r["continues_from"]))
    # 显式要求时那条路仍然通
    p2 = run("frames.py", ["--episode-id", str(eid), "--db", TEST_DB, "--continuity"],
             llm_base, ark_base)
    rep.add("显式 --continuity 仍可用", p2.returncode == 0, C.tail(p2.stderr, 50))
    chained_n = con.execute("SELECT COUNT(*) FROM shot WHERE episode_id=? AND "
                            "continues_from IS NOT NULL", (eid,)).fetchone()[0]
    rep.add("显式串接时后一镜首帧 = 前一镜尾帧",
            chained_n == len(rows) - 1, "%d 组（预期 %d）" % (chained_n, len(rows) - 1))
    con.execute("UPDATE shot SET continues_from=NULL WHERE episode_id=?", (eid,))
    con.commit()
    con.close()

    srt_rel = os.path.join(".tools", "e2e-test", "assets", "subs", "E01.srt")
    os.makedirs(os.path.dirname(C.abs_path(srt_rel)), exist_ok=True)
    with open(C.abs_path(srt_rel), "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,500 --> 00:00:03,000\n你到底是谁？\n"
                "2\n00:00:04,000 --> 00:00:07,000\n我是你姐。\n")
    stage(rep, "第 10 阶段 拼接 + 烧字幕", "assemble.py",
          ["--episode-id", str(eid), "--db", TEST_DB, "--out", TEST_OUT,
           "--srt", srt_rel], llm_base, ark_base)

    out1 = os.path.join(TEST_OUT, "E01.mp4")
    rep.add("成片文件存在", os.path.exists(out1),
            "%.2f MB" % (os.path.getsize(out1) / 1048576.0) if os.path.exists(out1) else "缺失")

    # ---- 最终验收 ----
    print("\n=== 最终验收 ===")
    if os.path.exists(out1):
        d = C.duration(out1, cwd=C.ROOT)
        # 假 Ark **按请求的时长生成素材**（加了 U0 规格门之后必须这样，否则规格门
        # 会因为时长不符把合法请求拦掉）。所以成片时长 = 各镜时长之和，
        # 从库里读，不要写死"素材各 1 秒"。
        _c = C.connect(TEST_DB)
        expected = _c.execute(
            "SELECT COALESCE(SUM(duration_s),0) FROM shot WHERE status IN "
            "('approved','final','pending') OR status IS NOT NULL").fetchone()[0]
        _c.close()
        rep.add("成片时长 = 各镜时长之和", abs(d - expected) < 1.0,
                "%.2f 秒（预期 %.1f，各镜 %d 个）" % (d, expected, n_shot))

    con = C.connect(TEST_DB)
    vcost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger "
                        "WHERE category='video'").fetchone()[0]
    gcost = con.execute("SELECT COALESCE(SUM(cost),0) FROM generation").fetchone()[0]
    rep.add("cost_ledger 与 generation.cost 账目一致", abs(vcost - gcost) < 1e-6,
            "¥%.2f / ¥%.2f" % (vcost, gcost))
    icost = con.execute("SELECT COALESCE(SUM(amount),0) FROM cost_ledger "
                        "WHERE category='image'").fetchone()[0]
    rep.add("总成本已完整记账", vcost + icost > 0,
            "视频 ¥%.2f + 图片 ¥%.2f = ¥%.2f" % (vcost, icost, vcost + icost))

    for view in ("v_pipeline_status", "v_episode_cost", "v_shot_progress",
                 "v_entity_readiness", "v_episode_outline"):
        try:
            con.execute("SELECT * FROM %s LIMIT 1" % view).fetchone()
            okv = True
            detail = ""
        except Exception as e:
            okv, detail = False, str(e)[:50]
        rep.add("视图可用：%s" % view, okv, detail)

    st = con.execute("SELECT status FROM episode WHERE id=?", (eid,)).fetchone()["status"]
    rep.add("episode 状态推进到 assembled", st == "assembled", st)
    n_asset = con.execute("SELECT COUNT(*) FROM asset WHERE kind='final'").fetchone()[0]
    rep.add("成片已登记到 asset 表", n_asset == 1, "%d 条" % n_asset)
    con.close()

    _unch, _why = C.prod_unchanged(PROD0)
    rep.add("生产库全程未被写入", _unch, _why)
    # ⚠️ 原来断言「真实 assets/ 未被创建」——**真跑过第 3 阶段之后必然失效**：
    # 生产目录本来就该有参考图与首帧，而那不是"自检污染了生产目录"。
    # 改成检查**自检用的是隔离的素材根**——这才是它真正想保证的事。
    _test = os.environ.get("DSH_ASSETS_ROOT", "")
    _added = sorted(_assets_snapshot() - ASSETS0)
    rep.add("自检没有往真实 assets/ 里写东西", not _added,
            "无新增" if not _added else "新增 %d 个：%s" % (len(_added), _added[:3]))

    llm_srv.shutdown()
    ark_srv.shutdown()
    code = rep.finish()
    # 自检造的项目档案要清掉，否则会留在知识库里（它不是真的项目）
    if _e2e_brief and os.path.exists(E2E_BRIEF):
        os.remove(E2E_BRIEF)
    if args.keep:
        print("\n产物保留在:", os.path.relpath(TEST_ROOT, C.ROOT))
        print("  成片:", os.path.relpath(out1, C.ROOT))
    else:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        print("已清理测试目录")
    return code


if __name__ == "__main__":
    sys.exit(main())
