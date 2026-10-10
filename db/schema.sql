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
    description     TEXT,                           -- 画面描述
    dialogue        TEXT,                           -- 台词
    sfx             TEXT,                           -- 音效/环境音
    prompt          TEXT,                           -- 视频提示词
    negative_prompt TEXT,
    char_id         INTEGER REFERENCES character(id) ON DELETE SET NULL,
    strategy        TEXT    NOT NULL DEFAULT 'draft',   -- draft | direct
    draws_planned   INTEGER NOT NULL DEFAULT 3,     -- 计划抽卡次数
    status          TEXT    NOT NULL DEFAULT 'pending',
                    -- pending | approved | rejected | final
    UNIQUE (episode_id, seq)
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
