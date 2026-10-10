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
