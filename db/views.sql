-- ============================================================================
--  仿真人短剧生产库 · 索引与视图（第三段）
--
--  必须在 migrate() 之后执行：这里的索引会引用迁移新增的列
--  （例如 idx_shot_scene 引用 shot.scene_id），提前执行会报 no such column。
--  由 db/init_db.py 自动按序调用，不需手工执行。
-- ============================================================================

-- ---------------------------------------------------------------- 索引
CREATE INDEX IF NOT EXISTS idx_character_project ON character(project_id);
CREATE INDEX IF NOT EXISTS idx_episode_project   ON episode(project_id);
CREATE INDEX IF NOT EXISTS idx_script_episode    ON script(episode_id);
CREATE INDEX IF NOT EXISTS idx_scene_script      ON scene(script_id);
CREATE INDEX IF NOT EXISTS idx_shot_episode      ON shot(episode_id);
CREATE INDEX IF NOT EXISTS idx_shot_scene        ON shot(scene_id);
CREATE INDEX IF NOT EXISTS idx_shot_char         ON shot(char_id);
CREATE INDEX IF NOT EXISTS idx_gen_shot          ON generation(shot_id);
CREATE INDEX IF NOT EXISTS idx_gen_status        ON generation(status);
CREATE INDEX IF NOT EXISTS idx_gen_task          ON generation(task_id);
CREATE INDEX IF NOT EXISTS idx_asset_gen         ON asset(generation_id);
CREATE INDEX IF NOT EXISTS idx_cost_episode      ON cost_ledger(episode_id);
CREATE INDEX IF NOT EXISTS idx_entity_project    ON entity(project_id);
CREATE INDEX IF NOT EXISTS idx_entity_kind       ON entity(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_entity_ref_entity ON entity_ref(entity_id);
CREATE INDEX IF NOT EXISTS idx_imagegen_entity   ON image_generation(entity_id);
CREATE INDEX IF NOT EXISTS idx_imagegen_status   ON image_generation(status);
CREATE INDEX IF NOT EXISTS idx_shot_entity_ent   ON shot_entity(entity_id);
CREATE INDEX IF NOT EXISTS idx_shot_continues    ON shot(continues_from);
CREATE INDEX IF NOT EXISTS idx_checkpoint_ep     ON checkpoint(episode_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_stage  ON checkpoint(episode_id, stage, substage);
CREATE INDEX IF NOT EXISTS idx_checkpoint_status ON checkpoint(status);
CREATE INDEX IF NOT EXISTS idx_decision_ep       ON decision_log(episode_id);
CREATE INDEX IF NOT EXISTS idx_decision_cat      ON decision_log(category);

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

-- 剧本 → 场 → 镜头 的覆盖情况。
-- 刻意用相关子查询而非多表 JOIN：场与镜头是两条独立的分支，
-- 一起 JOIN 会做笛卡尔积，SUM(duration_s) 出来的秒数会成倍虚高。
DROP VIEW IF EXISTS v_episode_outline;
CREATE VIEW v_episode_outline AS
SELECT  e.id       AS episode_id,
        e.seq      AS episode_seq,
        e.title,
        e.status,
        (SELECT version FROM script
          WHERE episode_id = e.id AND is_current = 1 LIMIT 1)    AS script_version,
        (SELECT COUNT(*) FROM scene sc JOIN script s ON s.id = sc.script_id
          WHERE s.episode_id = e.id AND s.is_current = 1)        AS scenes,
        (SELECT COALESCE(SUM(sc.duration_s), 0) FROM scene sc JOIN script s ON s.id = sc.script_id
          WHERE s.episode_id = e.id AND s.is_current = 1)        AS planned_seconds,
        (SELECT COUNT(*) FROM shot WHERE episode_id = e.id)      AS shots,
        (SELECT COALESCE(SUM(duration_s), 0) FROM shot
          WHERE episode_id = e.id)                               AS shot_seconds
FROM episode e;

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

-- 实体参考图齐备度。用途：跑「定妆照」「三视图」两阶段时，
-- 一眼看出哪些实体还缺图、缺哪个视角、有没有指定主参考图。
DROP VIEW IF EXISTS v_entity_readiness;
CREATE VIEW v_entity_readiness AS
SELECT  en.id        AS entity_id,
        en.kind      AS kind,               -- character / location / prop
        en.name      AS name,
        en.role      AS role,
        COUNT(r.id)  AS refs,
        SUM(CASE WHEN r.view = 'fullbody'    THEN 1 ELSE 0 END) AS has_fullbody,
        SUM(CASE WHEN r.view = 'headshot'    THEN 1 ELSE 0 END) AS has_headshot,
        SUM(CASE WHEN r.view = 'scene_front' THEN 1 ELSE 0 END) AS has_scene_front,
        SUM(CASE WHEN r.view = 'scene_top'   THEN 1 ELSE 0 END) AS has_scene_top,
        SUM(CASE WHEN r.view = 'blocking'    THEN 1 ELSE 0 END) AS has_blocking,
        SUM(CASE WHEN r.view = 'prop_ref'    THEN 1 ELSE 0 END) AS has_prop_ref,
        SUM(CASE WHEN r.is_primary = 1         THEN 1 ELSE 0 END) AS has_primary,
        SUM(CASE WHEN r.approved = 1           THEN 1 ELSE 0 END) AS approved_refs,
        (SELECT COUNT(*) FROM shot_entity se WHERE se.entity_id = en.id) AS used_in_shots
FROM entity en
LEFT JOIN entity_ref r ON r.entity_id = en.id
GROUP BY en.id;

-- 管线各阶段进度。用途：整条流程的总看板——
-- 剧本/场/镜头/提示词/抽帧/成片 六个阶段的完成量。
DROP VIEW IF EXISTS v_pipeline_status;
CREATE VIEW v_pipeline_status AS
SELECT  e.id  AS episode_id,
        e.seq AS episode_seq,
        e.title,
        e.status,
        (SELECT COUNT(*) FROM script s
          WHERE s.episode_id = e.id AND s.is_current = 1)                  AS scripts,
        (SELECT COUNT(*) FROM scene sc JOIN script s ON s.id = sc.script_id
          WHERE s.episode_id = e.id AND s.is_current = 1)                  AS scenes,
        (SELECT COUNT(*) FROM shot WHERE episode_id = e.id)                AS shots,
        (SELECT COUNT(*) FROM shot
          WHERE episode_id = e.id AND prompt IS NOT NULL AND prompt <> '') AS shots_with_prompt,
        (SELECT COUNT(*) FROM shot
          WHERE episode_id = e.id
            AND (first_frame_path IS NOT NULL OR last_frame_path IS NOT NULL))
                                                                           AS shots_with_frames,
        (SELECT COUNT(DISTINCT g.shot_id) FROM generation g
           JOIN shot sh ON sh.id = g.shot_id
          WHERE sh.episode_id = e.id AND g.status = 'succeeded')           AS shots_with_video
FROM episode e;

-- 每集每阶段的门禁现状（取最大 revision）。用途：一眼看出卡在哪道门。
DROP VIEW IF EXISTS v_stage_gate;
CREATE VIEW v_stage_gate AS
SELECT  e.id      AS episode_id,
        e.seq     AS episode_seq,
        c.stage,
        c.substage,
        c.status,
        c.revision,
        c.human_approved,
        CASE WHEN c.human_approved = 1        THEN 'approved'
             WHEN c.status = 'awaiting_human' THEN 'awaiting_human'
             ELSE c.status END                AS gate_state,
        c.started_at,
        c.finished_at
FROM episode e
JOIN checkpoint c ON c.episode_id = e.id
WHERE c.revision = (SELECT MAX(c2.revision) FROM checkpoint c2
                     WHERE c2.episode_id = c.episode_id
                       AND c2.stage = c.stage
                       AND c2.substage = c.substage);

-- 正在等人批的阶段。执行层每次开工先查它——有东西等人批就不该往下走。
DROP VIEW IF EXISTS v_pending_approval;
CREATE VIEW v_pending_approval AS
SELECT  e.seq     AS episode_seq,
        c.stage,
        c.substage,
        c.revision,
        c.started_at,
        c.metadata
FROM checkpoint c
JOIN episode e ON e.id = c.episode_id
WHERE c.status = 'awaiting_human' AND c.human_approved = 0;

-- 决策时间线：每次选择的备选、理由、置信度与出处。
DROP VIEW IF EXISTS v_decision_trail;
CREATE VIEW v_decision_trail AS
SELECT  d.id,
        e.seq        AS episode_seq,
        d.stage,
        d.category,
        d.decision,
        d.alternatives,
        d.rationale,
        d.confidence,
        d.source,
        d.created_at
FROM decision_log d
LEFT JOIN episode e ON e.id = d.episode_id
ORDER BY d.created_at DESC, d.id DESC;
