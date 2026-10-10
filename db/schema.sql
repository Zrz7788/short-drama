-- ============================================================================
--  仿真人短剧生产库 · 表结构（第一段）
--
--  执行顺序由 db/init_db.py 保证：
--      1. 本文件       建表（全部 IF NOT EXISTS，纯加法）
--      2. migrate()    对已存在的表补新列（ALTER TABLE，见 init_db.py）
--      3. db/views.sql 建索引与视图（依赖迁移后的列，必须放最后）
--
--  ⚠️ 为什么必须分三段：CREATE TABLE IF NOT EXISTS 对**已存在**的表完全不动，
--     所以给它加列不会被迁移。把索引/视图放在最后，才能引用到新加的列。
--     实测踩过一次：shot 表加 scene_id 后，索引直接报 no such column。
--
--  重建：python db/init_db.py --rebuild
--  增量：python db/init_db.py
-- ============================================================================

-- ---------------------------------------------------------------- 元信息
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------- 项目
CREATE TABLE IF NOT EXISTS project (
    id                     INTEGER PRIMARY KEY,
    name                   TEXT    NOT NULL UNIQUE,
    logline                TEXT,
    genre                  TEXT,
    aspect_ratio           TEXT    NOT NULL DEFAULT '9:16',
    target_episode_seconds INTEGER NOT NULL DEFAULT 80,
    notes                  TEXT,
    created_at             TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------- 角色（一致性要素集中在此）
CREATE TABLE IF NOT EXISTS character (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name           TEXT    NOT NULL,
    role           TEXT,                          -- 主角 / 配角 / 客串
    appearance     TEXT,                          -- 外貌设定，写进每次 prompt
    personality    TEXT,
    ref_image_path TEXT,                          -- 定妆参考图
    seed           INTEGER,                       -- 固化的随机种子
    voice_id       TEXT,                          -- TTS 音色标识
    notes          TEXT,
    UNIQUE (project_id, name)
);

-- ---------------------------------------------------------------- 统一实体（人物 / 场景 / 道具）
-- 为什么合成一张表：在本项目的流程里这三者待遇完全相同——都要定妆照、
-- 都要三视图、都会被镜头引用。拆成三张表会让后续每一段脚本都写三遍同样的逻辑。
--
-- ⚠️ 命名区分（重要）：
--     entity(kind='location') = 场景资产，即"布景"，要出定妆照和三视图的那个
--     scene 表                = 剧本的"场"，是剧作结构（第几场、什么 beat、多长）
--   两者同名不同物，勿混。
CREATE TABLE IF NOT EXISTS entity (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL CHECK (kind IN ('character','location','prop')),
    name        TEXT    NOT NULL,
    alias       TEXT,                    -- 剧本里的其他叫法，供自动提取时匹配
    role        TEXT,                    -- 主要 / 次要 / 背景
    description TEXT,                    -- 可整段粘进 prompt 的外观描述块
    style_note  TEXT,                    -- 材质、年代、色调等美术约束
    voice_id    TEXT,                    -- 仅人物有意义
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (project_id, kind, name)
);

-- ---------------------------------------------------------------- 图片生成任务（与 generation 表对称）
-- generation 记视频调用，本表记图片调用。
-- 刻意不复用 generation：它的 shot_id 是 NOT NULL，且带 duration_s / is_draft 等
-- 视频专属字段；SQLite 无法把 NOT NULL 改成可空，重建表的代价高于新建对称表。
CREATE TABLE IF NOT EXISTS image_generation (
    id             INTEGER PRIMARY KEY,
    entity_id      INTEGER REFERENCES entity(id) ON DELETE SET NULL,
    view           TEXT,                 -- 本次要出的视角
    model_id       TEXT    NOT NULL,
    prompt         TEXT,
    size           TEXT,                 -- 1K / 2K / 4K
    seed           INTEGER,
    ref_image_path TEXT,                 -- 图生图时的输入参考
    task_id        TEXT,
    status         TEXT    NOT NULL DEFAULT 'queued',
                   -- queued | running | succeeded | failed | cancelled | expired
    image_url      TEXT,
    local_path     TEXT,
    error          TEXT,
    cost           REAL    NOT NULL DEFAULT 0,
    submitted_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at    TEXT
);

-- ---------------------------------------------------------------- 实体参考图（人物/场景/道具素材角度）
-- 新素材体系（三视图已废弃）：人物产出 fullbody(全身照) / headshot(大头照)，
-- 场景产出 scene_front / blocking / scene_top，道具产出 prop_ref。
CREATE TABLE IF NOT EXISTS entity_ref (
    id           INTEGER PRIMARY KEY,
    entity_id    INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    view         TEXT    NOT NULL,
                 -- portrait(定妆照) | front/side/back(三视图)
                 -- | three_quarter | wide | detail
    path         TEXT,
    image_gen_id INTEGER REFERENCES image_generation(id) ON DELETE SET NULL,
    seed         INTEGER,
    prompt       TEXT,
    is_primary   INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
    approved     INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0,1)),
    notes        TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (entity_id, view)
);

-- ---------------------------------------------------------------- 集
CREATE TABLE IF NOT EXISTS episode (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    title          TEXT,
    synopsis       TEXT,
    target_seconds INTEGER,
    status         TEXT    NOT NULL DEFAULT 'draft',
                   -- draft | scripted | storyboarded | generating | assembled | done
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (project_id, seq)
);

-- ---------------------------------------------------------------- 剧本（一集多版本，is_current=1 为现行稿）
CREATE TABLE IF NOT EXISTS script (
    id         INTEGER PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL DEFAULT 1,
    title      TEXT,
    logline    TEXT,                              -- 本集一句话
    content    TEXT,                              -- 完整剧本文本（markdown）
    word_count INTEGER,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1)),
    notes      TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (episode_id, version)
);

-- ---------------------------------------------------------------- 场（剧本的结构化拆解，分镜从场来）
CREATE TABLE IF NOT EXISTS scene (
    id          INTEGER PRIMARY KEY,
    script_id   INTEGER NOT NULL REFERENCES script(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    slug        TEXT,                             -- 场景标题，如「内景 · 夜 · 客厅」
    location    TEXT,
    time_of_day TEXT,
    action      TEXT,                             -- 动作描述
    dialogue    TEXT,                             -- 对白
    beat        TEXT,                             -- 节奏位置：钩子/建立/升级/高潮/扣子
    duration_s  REAL,                             -- 预估时长
    UNIQUE (script_id, seq)
);

-- ---------------------------------------------------------------- 镜头（分镜表）
CREATE TABLE IF NOT EXISTS shot (
    id              INTEGER PRIMARY KEY,
    episode_id      INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    scene_id        INTEGER REFERENCES scene(id) ON DELETE SET NULL,
    seq             INTEGER NOT NULL,
    duration_s      REAL    NOT NULL DEFAULT 4,     -- 2.x 支持 4-15 秒
    shot_size       TEXT,                           -- 特写/近景/中景/全景
    camera_move     TEXT,                           -- 推/拉/摇/移/跟
    description     TEXT,                           -- 给人看的：情绪与意图
    dialogue        TEXT,                           -- 台词
    sfx             TEXT,                           -- 音效/环境音
    prompt          TEXT,                           -- 视频提示词
    negative_prompt TEXT,
    char_id         INTEGER REFERENCES character(id) ON DELETE SET NULL,
                    -- 已弃用：改用 shot_entity 关联表（一个镜头常同时含人物+布景+道具）
    strategy        TEXT    NOT NULL DEFAULT 'draft',   -- draft | direct
    draws_planned   INTEGER NOT NULL DEFAULT 3,     -- 计划抽卡次数
    status          TEXT    NOT NULL DEFAULT 'pending',
                    -- pending | approved | rejected | final
    -- ⚠️ 以下列的顺序**必须与 COLUMN_MIGRATIONS 的追加顺序一致**，
    -- 否则新建的库与升级上来的库列顺序不同（按名取值时无影响，
    -- 但两个路径产出不同 schema 是隐患）。
    first_frame_path TEXT,                          -- 本镜实际用的首帧（续接时 = 上一镜尾帧）
    last_frame_path  TEXT,                          -- 本镜的尾帧图（供下一镜续接）
    continues_from   INTEGER REFERENCES shot(id) ON DELETE SET NULL,
                    -- 从哪个镜头的尾帧接过来；NULL = 本镜独立起头
    selected_generation_id INTEGER REFERENCES generation(id) ON DELETE SET NULL,
                    -- 抽卡多次后选中的那一条；拼片时以它为准
    action          TEXT,                           -- 给模型看的：可见的动作与微表情
    lighting        TEXT,                           -- 光线与氛围（第 6 阶段组装 prompt 用）
    keyframe_path    TEXT,                          -- 第 7 阶段生成的关键帧（出视频的锚点）
    -- 镜头语言（v5）。依据《拾音人第一集》工艺规范：25 镜逐镜拆解，
    -- 每镜 15 个维度。原来只有景别/运镜/描述，撑不起一份可执行的镜头设计。
    camera_angle     TEXT,                          -- 机位角度与高度
    lens_mm          TEXT,                          -- 焦段
    composition      TEXT,                          -- 构图（三分/对称/对角线/框架…）
    dof              TEXT,                          -- 景深与光圈
    movement_speed   TEXT,                          -- 运动速度与加减速曲线
    color_temp       TEXT,                          -- 色温与光比
    material_notes   TEXT,                          -- 材质要点（皮肤/织物/道具）
    fg_mg_bg         TEXT,                          -- 前中后景关系（竖屏 70/30 法则）
    transition       TEXT,                          -- 转场：硬切/闪白/黑场/J Cut
    ambient_sound    TEXT,                          -- 环境声音与音效
    entrance         TEXT,                          -- 8 种人物出场技巧之一
    emotion          TEXT,                          -- 情绪标注：说话动词+标点+音色+语气
    solo_generation  INTEGER NOT NULL DEFAULT 0,    -- 是否需单独生成（大特写/大特效）
    UNIQUE (episode_id, seq)
);

-- ---------------------------------------------------------------- 镜头 ↔ 实体（多对多）
-- 一个镜头通常同时含人物 + 布景 + 道具，所以不做外键而做关联表。
CREATE TABLE IF NOT EXISTS shot_entity (
    shot_id   INTEGER NOT NULL REFERENCES shot(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    role      TEXT,          -- subject(主体) / background(背景) / prop(道具)
    PRIMARY KEY (shot_id, entity_id)
);

-- ---------------------------------------------------------------- 生成任务
CREATE TABLE IF NOT EXISTS generation (
    id           INTEGER PRIMARY KEY,
    shot_id      INTEGER NOT NULL REFERENCES shot(id) ON DELETE CASCADE,
    attempt_no   INTEGER NOT NULL DEFAULT 1,        -- 第几次抽卡
    model_id     TEXT    NOT NULL,
    resolution   TEXT,                              -- 480p/720p/1080p/4k
    duration_s   REAL,
    is_draft     INTEGER NOT NULL DEFAULT 0 CHECK (is_draft IN (0,1)),
    ratio        TEXT,
    task_id      TEXT,                              -- 火山方舟任务 ID
    status       TEXT    NOT NULL DEFAULT 'queued',
                 -- queued | running | succeeded | failed | cancelled | expired
    video_url    TEXT,
    local_path   TEXT,
    seed         INTEGER,
    error        TEXT,
    cost         REAL    NOT NULL DEFAULT 0,        -- 该次调用实际花费（元）
    submitted_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at  TEXT,
    UNIQUE (shot_id, attempt_no)
);

-- ---------------------------------------------------------------- 素材
CREATE TABLE IF NOT EXISTS asset (
    id            INTEGER PRIMARY KEY,
    generation_id INTEGER REFERENCES generation(id) ON DELETE SET NULL,
    shot_id       INTEGER REFERENCES shot(id) ON DELETE SET NULL,
    kind          TEXT    NOT NULL,                 -- video/image/audio/subtitle/final
    path          TEXT    NOT NULL,
    bytes         INTEGER,
    sha256        TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------- 成本流水
CREATE TABLE IF NOT EXISTS cost_ledger (
    id         INTEGER PRIMARY KEY,
    ts         TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    category   TEXT    NOT NULL,                    -- video/image/tts/llm/other
    model_id   TEXT,
    quantity   REAL,                                -- 秒数 / 张数 / 千 token
    unit       TEXT,                                -- second/image/ktoken
    unit_price REAL,
    amount     REAL    NOT NULL,                    -- 实际金额（元）
    episode_id INTEGER REFERENCES episode(id) ON DELETE SET NULL,
    shot_id    INTEGER REFERENCES shot(id) ON DELETE SET NULL,
    note       TEXT
);

-- ---------------------------------------------------------------- 价格参考表（不是账单，是价目）
CREATE TABLE IF NOT EXISTS price_book (
    id         INTEGER PRIMARY KEY,
    model_id   TEXT    NOT NULL,
    variant    TEXT,                                -- 档位说明，如 "Draft 样片"
    resolution TEXT,
    unit       TEXT    NOT NULL DEFAULT 'second',   -- second/image/ktoken
    unit_price REAL    NOT NULL,
    currency   TEXT    NOT NULL DEFAULT 'CNY',
    source     TEXT,                                -- 出处
    confidence TEXT    NOT NULL DEFAULT 'medium',   -- high/medium/low
    updated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (model_id, variant, resolution, unit)
);

-- ---------------------------------------------------------------- 阶段存档与门禁状态
-- 门禁协议的地基。没有这张表，"清单里的门禁开关"就只是文档。
--
-- 一个阶段可以有多条记录（revision 递增）：被取代的版本**保留不删**，
-- 这样"门禁转移与阶段版本永不销毁"才是真的。查当前状态取 MAX(revision)。
CREATE TABLE IF NOT EXISTS checkpoint (
    id             INTEGER PRIMARY KEY,
    episode_id     INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    stage          TEXT    NOT NULL,
                   -- spec|script|cast|art|storyboard|prompts|keyframes|motion|continuity|assemble
    substage       TEXT    NOT NULL DEFAULT '',
                   -- portrait|turnaround|draft|direct|final；无子阶段时为空串
    status         TEXT    NOT NULL,
                   -- in_progress | awaiting_human | completed | failed
    revision       INTEGER NOT NULL DEFAULT 1,
    artifacts      TEXT,                            -- JSON：该阶段的产出工件
    metadata       TEXT,                            -- JSON：审查发现、花费快照、耗时、partial_progress
    human_approved INTEGER NOT NULL DEFAULT 0 CHECK (human_approved IN (0,1)),
    approved_at    TEXT,
    approved_note  TEXT,
    started_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at    TEXT,
    UNIQUE (episode_id, stage, substage, revision)
);

-- ---------------------------------------------------------------- 决策记录
-- 清单里 decision_log 是一等工件，这张表就是它的落脚点。
-- 用途：半年后回看"这个模型/这个档位当时为什么这么选"，答案在这里。
CREATE TABLE IF NOT EXISTS decision_log (
    id           INTEGER PRIMARY KEY,
    episode_id   INTEGER REFERENCES episode(id) ON DELETE SET NULL,
    stage        TEXT,
    category     TEXT    NOT NULL,
                 -- model_choice | entity_tier | approval_policy | budget
                 -- | visual_accuracy_check | continuity | other
    decision     TEXT    NOT NULL,                  -- 选了什么
    alternatives TEXT,                              -- JSON 数组：当时还考虑了什么
    rationale    TEXT,                              -- 为什么这么选
    confidence   TEXT    NOT NULL DEFAULT 'medium', -- high | medium | low
    source       TEXT,                              -- 依据出处（控制台/官方文档/反推/估算）
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
