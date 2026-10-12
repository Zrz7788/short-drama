# -*- coding: utf-8 -*-
"""建立/迁移仿真人短剧生产库。

用法：
    python db/init_db.py            不存在则建，存在则做增量迁移与校验
    python db/init_db.py --rebuild  删库重建（会丢数据）

三段式执行顺序（不能颠倒）：
    1. db/schema.sql  建表，全部 IF NOT EXISTS —— 纯加法
    2. migrate()      给**已存在**的表补新列
    3. db/views.sql   建索引与视图 —— 会引用第 2 步新加的列
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "drama.db")
SCHEMA = os.path.join(ROOT, "db", "schema.sql")
VIEWS = os.path.join(ROOT, "db", "views.sql")

# 本次会话核实过的价目。置信度含义见 knowledge/02-模型与成本/
PRICES = [
    ("doubao-seedance-2-5-260628", "旗舰", "1080p", "second", 1.45,
     "用户提供（火山方舟控制台）", "high"),
    ("doubao-seedance-2-0-260128", "标准", "1080p", "second", 1.00,
     "用户提供（火山方舟控制台）", "high"),
    ("doubao-seedance-2-5-260628", "Draft 样片", "480p", "second", 0.26,
     "由官方省费口径反推：5s/1080P、4抽命中省57%，20抽1省77%", "medium"),
    ("doubao-seedream-4-0-250828", "图片", None, "image", 0.20,
     "估算，需在控制台核对", "low"),
]

# schema 版本。加表改 schema.sql 即可；**加列必须同时登记到 COLUMN_MIGRATIONS**。
SCHEMA_VERSION = 9

# 给已存在的表补列。CREATE TABLE IF NOT EXISTS 对已存在的表完全不动，
# 所以每个新增列都要在这里显式登记，否则老库升不上来。
# 格式：(表, 列, ALTER 语句)
COLUMN_MIGRATIONS = [
    ("shot", "scene_id",
     "ALTER TABLE shot ADD COLUMN scene_id INTEGER REFERENCES scene(id)"),
    # 视频衔接所需：抽帧路径与续接关系
    ("shot", "first_frame_path",
     "ALTER TABLE shot ADD COLUMN first_frame_path TEXT"),
    ("shot", "last_frame_path",
     "ALTER TABLE shot ADD COLUMN last_frame_path TEXT"),
    ("shot", "continues_from",
     "ALTER TABLE shot ADD COLUMN continues_from INTEGER"),
    # 抽卡多次后必须能指明"哪条是选中的"，否则拼片时不知道用哪个文件
    ("shot", "selected_generation_id",
     "ALTER TABLE shot ADD COLUMN selected_generation_id INTEGER REFERENCES generation(id)"),
    # 第 5 阶段产出、第 6 阶段消费：可见动作与光线
    ("shot", "action", "ALTER TABLE shot ADD COLUMN action TEXT"),
    ("shot", "lighting", "ALTER TABLE shot ADD COLUMN lighting TEXT"),
    # 第 7 阶段产出的关键帧（首帧图）。
    # 刻意与 first_frame_path 分开：后者的语义是"这一镜实际用的首帧"，
    # 会被第 9 阶段的续接逻辑覆盖（续接时它等于上一镜的尾帧）。
    ("shot", "keyframe_path", "ALTER TABLE shot ADD COLUMN keyframe_path TEXT"),
    # ---- v5：镜头语言字段 ----
    # 依据：用户提供的《拾音人第一集》工艺规范（25 镜逐镜拆解，每镜 15 个维度）
    # 与《6、女频氛围感》《9、镜头语言》。原来的 shot 表只有景别/运镜/描述，
    # 撑不起一份可执行的镜头设计，模型只能自由发挥。
    ("shot", "camera_angle",
     "ALTER TABLE shot ADD COLUMN camera_angle TEXT"),      # 机位角度与高度
    ("shot", "lens_mm", "ALTER TABLE shot ADD COLUMN lens_mm TEXT"),   # 焦段
    ("shot", "composition",
     "ALTER TABLE shot ADD COLUMN composition TEXT"),       # 构图（三分/对称/对角线…）
    ("shot", "dof", "ALTER TABLE shot ADD COLUMN dof TEXT"),  # 景深与光圈
    ("shot", "movement_speed",
     "ALTER TABLE shot ADD COLUMN movement_speed TEXT"),    # 运动速度与加减速
    ("shot", "color_temp",
     "ALTER TABLE shot ADD COLUMN color_temp TEXT"),        # 色温与光比
    ("shot", "material_notes",
     "ALTER TABLE shot ADD COLUMN material_notes TEXT"),    # 材质要点（皮肤/织物/道具）
    ("shot", "fg_mg_bg",
     "ALTER TABLE shot ADD COLUMN fg_mg_bg TEXT"),          # 前中后景关系（竖屏 70/30）
    ("shot", "transition",
     "ALTER TABLE shot ADD COLUMN transition TEXT"),        # 转场（硬切/闪白/黑场/J Cut）
    ("shot", "ambient_sound",
     "ALTER TABLE shot ADD COLUMN ambient_sound TEXT"),     # 环境声音与音效
    # 8 种人物出场技巧之一（动态闯入 / 光影渐显 / 他人视线 / 前景遮挡 /
    # 道具引入 / 先声后人 / 运镜移焦 / 局部先入）。
    # 依据《11、人物出场8技巧》：画面平淡的根因是"让人物直接站在镜头里，
    # 没有用镜头展现他的动机和目的"。
    ("shot", "entrance",
     "ALTER TABLE shot ADD COLUMN entrance TEXT"),
    # 该镜使用的情绪标注：说话动词 + 标点策略 + 音色 + 语气语速。
    # 依据《2、台词情绪力》：原生音频路线下，提示词是唯一的情绪控制手段。
    ("shot", "emotion",
     "ALTER TABLE shot ADD COLUMN emotion TEXT"),
    # 是否需要**单独生成**（面部大特写、最大特效、首尾同构镜）。
    # 依据《拾音人》§5.1：面部大特写单独生成单独抽卡，素材只给 1 张。
    ("shot", "solo_generation",
     "ALTER TABLE shot ADD COLUMN solo_generation INTEGER DEFAULT 0"),
    # ---- v6：运镜的情绪化命名与防崩坏说明 ----
    # 依据《运镜与分镜规范》（漫剧资源包）：
    #   同一个"推镜头"，写成"聚焦式推进"就同时是动作描述和情绪指令。
    #   提示词模板固定为「命名 + 从X到Y + 速度(秒/帧) + 防崩坏说明 + 风格」，
    #   其中"速度"与"防崩坏说明"是我们原来完全缺的两样。
    ("shot", "camera_emotion",
     "ALTER TABLE shot ADD COLUMN camera_emotion TEXT"),
    ("shot", "camera_lock",
     "ALTER TABLE shot ADD COLUMN camera_lock TEXT"),
    # ---- v7：参照图内容指纹 ----
    # 依据 dsh-film 的交接规范：「**绝不因为文件名看着对就绑定或接受一个文件；
    # 字节变了就是另一个版本**」。
    # 我们原来 entity_ref.path 只是一个路径——文件被替换、同名覆盖、或手动换了
    # 一张，系统完全不知道，后面几十集会一直用错的锚点图。
    ("entity_ref", "sha256",
     "ALTER TABLE entity_ref ADD COLUMN sha256 TEXT"),
    # ---- v8：连续性具名锁 + 画面文字归属 ----
    # 依据 `knowledge/02-模型与成本/Seedance提示词与连续性锁定.md`：
    # 「**约束不属于末尾的负面提示词。它们属于「具名块」，且通常用正面措辞
    #   陈述"什么保持不变"**」——我们原来把约束全塞进 negative_prompt，那是
    # 最弱的写法，而且否定式对画面本来就无效。
    #
    # 这四条是**每镜**都要写的锁，所以给独立列，而不是拼进 description：
    # 分镜阶段据此逐镜核对，第 6 阶段据此拼成具名正块。
    #
    # ⚠️ 加列只能追加在列表**末尾**，且必须与 db/schema.sql 的列顺序一致——
    #    插到中间会让「新建的库」与「升级上来的库」列顺序不同（踩过 3 次）。
    ("shot", "screen_direction",
     "ALTER TABLE shot ADD COLUMN screen_direction TEXT"),   # 轴线：谁永远在画面哪一侧
    ("shot", "count_lock",
     "ALTER TABLE shot ADD COLUMN count_lock TEXT"),         # 计数锁：画面里正好几个人/几件物
    ("shot", "stillness_lock",
     "ALTER TABLE shot ADD COLUMN stillness_lock TEXT"),     # 静止锁：这一镜"不发生什么"
    ("shot", "positive_locks",
     "ALTER TABLE shot ADD COLUMN positive_locks TEXT"),     # 结尾正面重申跨镜保持不变的东西
    # 画面文字的**归属**：哪一镜、读到的精确内容是什么。
    # ⚠️ 精确数字（倒计时、门牌、日期）绝不能每镜都显示——跨镜保持文字完全一致
    #    模型做不到，看起来就是纯漂移。所以必须点名归属到唯一一镜。
    ("shot", "on_screen_text",
     "ALTER TABLE shot ADD COLUMN on_screen_text TEXT"),
    # ---- v9：实体的领域事实溯源 ----
    # 依据一次真实翻车：第 1 集我把「冷柜间」写成化妆间的别名，又把冷柜/推车/
    # 化妆台塞进一间屋，模型忠实照画 —— 于是**空间、载体、动作三样全错**。
    # 根因不是模型，是**我现场编了一个职业的常识**，而当时没有任何地方
    # 承载"这个职业真实是什么样"。
    #
    # 所以：location / prop 实体必须能指回 `knowledge/03-项目档案/<剧名>.md`
    # 的「领域事实」表里某一条。指不回去 = 凭想象编的 = 第 3/4 阶段不许出图。
    # 实体描述是生图提示词的**直接输入**，错了每张图 ¥0.30，必须先拦。
    #
    # 人物、以及纯剧情道具（一封信、一张化验单）不需要领域事实：
    # domain_fact 填 'story' 显式声明，而不是留空——留空分不清"不需要"
    # 和"忘了填"。
    ("entity", "domain_fact",
     "ALTER TABLE entity ADD COLUMN domain_fact TEXT"),
    ("entity", "domain_source",
     "ALTER TABLE entity ADD COLUMN domain_source TEXT"),
]

# 目标要求覆盖的数据域 -> 承载它们的表
DOMAINS = {
    "剧本":     ["script", "scene"],
    "集":       ["episode"],
    "镜头":     ["shot", "shot_entity"],
    "素材":     ["asset"],
    "生成任务": ["generation", "image_generation"],
    "成本":     ["cost_ledger", "price_book"],
    "实体":     ["entity", "entity_ref"],
    # 声明式清单引入的两项：门禁存档与决策记录
    "门禁存档": ["checkpoint"],
    "决策记录": ["decision_log"],
}


def migrate(con):
    """幂等地把已知的列变更补上，返回本次实际应用的列表。"""
    applied = []
    for table, column, ddl in COLUMN_MIGRATIONS:
        cols = {r[1] for r in con.execute('PRAGMA table_info("%s")' % table)}
        if not cols:
            continue          # 表不存在：全新库，schema.sql 已是目标状态
        if column not in cols:
            con.execute(ddl)
            applied.append("%s.%s" % (table, column))
    if applied:
        con.commit()
    return applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="删库重建")
    ap.add_argument("--db", default=DB_PATH,
                    help="数据库路径（默认 data/drama.db；自检用它指向临时库）")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    if args.rebuild and os.path.exists(db_path):
        os.remove(db_path)
        print("已删除旧库:", db_path)

    fresh = not os.path.exists(db_path)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")

    # 第 1 段：建表
    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())

    # 第 2 段：补列
    applied = migrate(con)
    if applied:
        print("已迁移列:", ", ".join(applied))

    # 第 3 段：索引与视图（依赖第 2 段）
    with open(VIEWS, encoding="utf-8") as f:
        con.executescript(f.read())

    con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),))
    con.commit()

    if con.execute("SELECT COUNT(*) FROM price_book").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO price_book "
            "(model_id, variant, resolution, unit, unit_price, source, confidence) "
            "VALUES (?,?,?,?,?,?,?)",
            PRICES,
        )
        con.commit()
        print("已写入 %d 条价目" % len(PRICES))

    print("\n库文件 :", db_path, "(%.1f KB)" % (os.path.getsize(db_path) / 1024.0))
    print("新建   :", "是" if fresh else "否（已存在，做增量迁移）")

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    views = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_%' ORDER BY name")]

    print("\n表 (%d)  : %s" % (len(tables), ", ".join(tables)))
    print("视图 (%d): %s" % (len(views), ", ".join(views)))
    print("索引 (%d): %s" % (len(idx), ", ".join(idx)))

    print("\n--- 价目表 v_price_book ---")
    for row in con.execute("SELECT * FROM v_price_book"):
        print("  %-28s %-10s %-6s %-7s %8.2f %-4s %-7s %s" % row)

    print("\n--- 数据域覆盖检查 ---")
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = 0
    for domain, tabs in DOMAINS.items():
        hit = [t for t in tabs if t in have]
        gone = [t for t in tabs if t not in have]
        missing += len(gone)
        print("  %-10s %-22s%s" % (domain, ", ".join(hit) or "(无)",
                                   "  缺: " + ", ".join(gone) if gone else ""))
    print("  schema 版本:", con.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])

    bad = list(con.execute("PRAGMA foreign_key_check"))
    print("\n外键自检:", "通过" if not bad else "**异常** %s" % bad)
    print("数据域缺失项:", missing)

    con.close()
    return 0 if missing == 0 and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
