-- ============================================================================
--  仿真人短剧生产库 · SQLite schema
--  设计原则：
--    1. 一次 API 调用 = 一条 generation 记录（抽卡次数的唯一真源）
--    2. 成本走 cost_ledger 流水，单价走 price_book 参考表，两者分离
--    3. 只存文本与路径，媒体文件不入库
--  重建方式：python db/init_db.py
-- ============================================================================

PRAGMA foreign_keys = ON;

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

-- ---------------------------------------------------------------- 镜头（分镜表）
CREATE TABLE IF NOT EXISTS shot (
    id              INTEGER PRIMARY KEY,
    episode_id      INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
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

-- ---------------------------------------------------------------- 索引
CREATE INDEX IF NOT EXISTS idx_character_project ON character(project_id);
CREATE INDEX IF NOT EXISTS idx_episode_project   ON episode(project_id);
CREATE INDEX IF NOT EXISTS idx_shot_episode      ON shot(episode_id);
CREATE INDEX IF NOT EXISTS idx_shot_char         ON shot(char_id);
CREATE INDEX IF NOT EXISTS idx_gen_shot          ON generation(shot_id);
CREATE INDEX IF NOT EXISTS idx_gen_status        ON generation(status);
CREATE INDEX IF NOT EXISTS idx_gen_task          ON generation(task_id);
CREATE INDEX IF NOT EXISTS idx_asset_gen         ON asset(generation_id);
CREATE INDEX IF NOT EXISTS idx_cost_episode      ON cost_ledger(episode_id);

-- ---------------------------------------------------------------- 视图
DROP VIEW IF EXISTS v_episode_cost;
CREATE VIEW v_episode_cost AS
SELECT  e.id                                   AS episode_id,
        p.name                                 AS project,
        e.seq                                  AS episode_seq,
        COUNT(DISTINCT s.id)                    AS shots,
        COUNT(DISTINCT g.id)                    AS generations,
        COALESCE(SUM(g.cost), 0)                AS video_cost,
        COALESCE((SELECT SUM(c.amount) FROM cost_ledger c
                   WHERE c.episode_id = e.id), 0) AS total_cost
FROM episode e
JOIN project p ON p.id = e.project_id
LEFT JOIN shot s       ON s.episode_id = e.id
LEFT JOIN generation g ON g.shot_id = s.id
GROUP BY e.id;

DROP VIEW IF EXISTS v_shot_progress;
CREATE VIEW v_shot_progress AS
SELECT  s.id            AS shot_id,
        e.seq           AS episode_seq,
        s.seq           AS shot_seq,
        s.duration_s,
        s.strategy,
        s.draws_planned,
        COUNT(g.id)                                     AS attempts,
        SUM(CASE WHEN g.status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
        COALESCE(SUM(g.cost), 0)                        AS cost,
        s.status
FROM shot s
JOIN episode e ON e.id = s.episode_id
LEFT JOIN generation g ON g.shot_id = s.id
GROUP BY s.id;

DROP VIEW IF EXISTS v_price_book;
CREATE VIEW v_price_book AS
SELECT  model_id                                  AS 模型ID,
        COALESCE(variant, '-')                    AS 档位,
        COALESCE(resolution, '-')                 AS 分辨率,
        unit                                      AS 计费单位,
        unit_price                                AS 单价,
        currency                                  AS 币种,
        confidence                                AS 置信度,
        COALESCE(source, '-')                     AS 来源
FROM price_book
ORDER BY model_id, variant, resolution;
