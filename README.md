# 仿真人短剧 · 项目工程底座

用火山方舟（Ark）的 Seedance / Seedream 生产 9:16 竖屏仿真人短剧的工程仓库。

## 目录

```
knowledge/          知识库：方法与依据（人读）
  01-制作方法/        剧本规范、结构模板、角色一致性 SOP、抽卡策略、分镜表规范
  02-模型与成本/      模型 ID 与单价、单集成本模型
  03-项目档案/        每部剧的设定
  04-复盘/            实测数据回流，用来修正 01 和 02
db/                 数据库
  schema.sql          表结构唯一真源（11 表），纯加法
  views.sql           索引与视图，必须最后执行
  init_db.py          建库 / 增量迁移 / 写入价目
  selftest.py         schema 自检：插模拟数据、验算视图、整体回滚
  export.py           导出纯文本 SQL 便于进 git
data/                数据库文件与导出（*.db 不进 git）
.dsh/skills/         智能体技能
  ark-seedance/      Ark 调用参考（模型 ID、单价、Draft、避坑）
.tools/              便携工具链，不进 git
  mingit/            Git 2.56.0（免安装版）
  ssh/               GitHub SSH 密钥
```

## 快速开始

```powershell
$git = "$PWD\.tools\mingit\cmd\git.exe"

# 建/校验数据库（含增量迁移）
python db\init_db.py

# schema 自检：模拟一集数据、验算视图、回滚
python db\selftest.py

# 导出文本备份
python db\export.py

# git 操作
& $git status
& $git log --oneline
```

## 本机环境要点

### 1. Git 是便携版，不在 PATH 上

系统上原本没有 git，`winget` 也是坏的（Store 别名桩，退出码非零）。
已将官方 MinGit 2.56.0 解压到 `.tools/mingit/`，用绝对路径调用：

```
.tools/mingit/cmd/git.exe
```

要让它上 PATH（新开终端生效）：

```powershell
[Environment]::SetEnvironmentVariable('Path',
  "$env:Path;$PWD\.tools\mingit\cmd", 'User')
```

### 2. git 的远程操作需要提权（重要）

受限沙箱下 **MinGit 的所有远程操作都会失败**：

```
fatal error - couldn't create signal pipe, Win32 error 5
fatal: Could not read from remote repository.
```

原因是 MinGit 基于 MSYS2，`git push/fetch/clone/ls-remote` 都要起子进程
（`ssh.exe` / `git-upload-pack`），而 MSYS 起子进程需要创建信号管道——
沙箱禁止命名管道，于是直接失败。**这不是网络问题**，SSH 链路本身是通的。

| 操作 | 受限模式 | 提权后 |
|---|---|---|
| `init` / `add` / `commit` / `log` / `status` | ✅ 正常 | ✅ 正常 |
| `push` / `fetch` / `clone` / `ls-remote` | ❌ 信号管道失败 | ✅ 正常 |

**本地版本控制完全可用**，只有同步到远程时需要批准一次提权。

### 3. 调 API 不能用 curl / Invoke-WebRequest

沙箱下这两个走 Windows schannel，会报 `SEC_E_NO_CREDENTIALS`，TLS 握手失败。
**用 Python 或 Node 发请求**（走 OpenSSL，实测正常）。

### 4. PowerShell 控制台中文乱码

跑脚本前加：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 5. ffmpeg 尚未安装

拼接、烧字幕、竖屏转码都需要它，目前**缺失**。属于后期环节，不影响出片。

## 数据库结构

三段式执行，顺序不能颠倒（`db/init_db.py` 自动按序调用）：

```
1. db/schema.sql   建表，全部 IF NOT EXISTS —— 纯加法
2. migrate()       给已存在的表补新列（ALTER TABLE）
3. db/views.sql    建索引与视图 —— 引用第 2 步新加的列
```

**为什么必须分三段**：`CREATE TABLE IF NOT EXISTS` 对已存在的表完全不动，
所以加列不会被自动迁移。实测踩过：给 `shot` 加 `scene_id` 后，
跟在后面的索引直接报 `no such column`。

**加列的正确做法**：改 `schema.sql`（新库的目标状态）+ 在 `init_db.py` 的
`COLUMN_MIGRATIONS` 里登记一条 ALTER（老库的升级路径），然后递增 `SCHEMA_VERSION`。

数据域与承载表：

| 数据域 | 表 |
|---|---|
| 剧本 | `script`、`scene` |
| 集 | `episode` |
| 镜头 | `shot` |
| 素材 | `asset` |
| 生成任务 | `generation` |
| 成本 | `cost_ledger`、`price_book` |

辅助表：`project`、`character`、`meta`。视图：`v_episode_cost`、`v_episode_outline`、
`v_shot_progress`、`v_price_book`。

## 项目纪律

1. **密钥不进 git。** `ARK_API_KEY` 放环境变量或本地 `.env`（已被 `.gitignore` 挡住）
2. **价格与模型 ID 必须标来源和置信度。** 无出处的数字不写进 `knowledge/02-*`
3. **`knowledge/01-*` 的方法在拿到真实数据前都是假设**，跑完回流到 `04-复盘/`
4. **角色技术设定只在数据库维护**，不要在项目档案里重复
5. **知识库与 skill 要同步改**——两处不同步就会误导智能体

## 当前状态

| 环节 | 状态 |
|---|---|
| Git 本地仓库 | ✅ 已初始化 |
| SQLite 生产库 | ✅ 8 表 / 3 视图 / 9 索引，价目已入库 |
| 知识库 | ✅ 6 篇文档 |
| `ark-seedance` skill | ✅ 已建，会话内即时生效 |
| GitHub 远程 | ⏳ 待接入（SSH 密钥已生成、链路实测通；受限模式下 push 需提权） |
| ffmpeg | ❌ 缺失 |
| Ark API Key | ⏳ 待提供，之后可跑真实生成 |
