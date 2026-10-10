# 复盘

`01-制作方法/` 和 `02-模型与成本/` 里的数字在跑真实项目之前**都是假设**。
本目录存放实测数据，用来回头修正那些假设。

**没有这一环，知识库就只是猜想的集合。**

## 每部剧跑完后回答

### 成本类（修正 `02-模型与成本/`）

| 问题 | 假设值 | 实测值 |
|---|---|---|
| 每镜平均实际抽卡次数 | 3 | ? |
| 单集实际视频成本 | ¥178 | ? |
| 实际崩脸率 | ? | ? |
| API 失败/超时导致的重试占比 | 0 | ? |
| 返工（分镜改稿作废已有素材）成本占比 | 0 | ? |

### 方法类（修正 `01-制作方法/`）

| 问题 | 出处 |
|---|---|
| 4 秒够不够完成一个动作？ | 竖屏短剧结构模板 |
| 钩子放第几秒流失最少？ | 竖屏短剧结构模板 |
| 哪类镜头崩得最多？ | 角色一致性SOP |
| 一个角色要几张定妆图？ | 角色一致性SOP |
| Draft 的成片和样片实际偏差有多大？ | 抽卡策略与Draft模式 |
| 计划抽卡次数 vs 实际次数的偏差 | 分镜表字段规范 |

## 数据从哪来

不要靠回忆，从数据库取：

```sql
-- 实际抽卡次数分布（验证 3 次的假设）
SELECT attempts, COUNT(*) AS n FROM v_shot_progress GROUP BY attempts ORDER BY attempts;

-- 计划 vs 实际的偏离
SELECT shot_seq, draws_planned, attempts FROM v_shot_progress
WHERE attempts <> draws_planned ORDER BY (attempts - draws_planned) DESC;

-- 每集真实成本
SELECT episode_seq, shots, generations, video_cost, total_cost FROM v_episode_cost;

-- 失败率
SELECT status, COUNT(*) FROM generation GROUP BY status;
```

导出成文本以便进 git：

```bash
python db/export.py
```

## 每份复盘的文件头

```markdown
# 《剧名》复盘

- 制作周期：
- 实际总花费：
- 集数 / 总时长：
- 数据导出：data/export/drama-YYYYMMDD.sql
```

## 修订纪律

改 `01` 或 `02` 的结论时，**在文件里注明依据来自哪份复盘**，不要悄悄改数字。
半年后回看，你会需要知道那个数字是怎么来的。
