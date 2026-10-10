# 仿真人短剧 · 项目工程底座

用火山方舟（Ark）的 Seedance / Seedream 生产 9:16 竖屏仿真人短剧的工程仓库。

## 目录

```
knowledge/          知识库：方法与依据（人读）
  01-制作方法/        结构模板、角色一致性 SOP、抽卡策略、分镜表规范
  02-模型与成本/      模型 ID 与单价、单集成本模型
  03-项目档案/        每部剧的设定
  04-复盘/            实测数据回流，用来修正 01 和 02
db/                 数据库
  schema.sql          schema 唯一真源（8 表 / 3 视图 / 9 索引）
  init_db.py          建库与写入价目
  export.py           导出纯文本 SQL 便于进 git
data/                数据库文件与导出（*.db 不进 git）
.dsh/skills/         智能体技能
  ark-seedance/      Ark 调用参考（模型 ID、单价、Draft、避坑）
.tools/              便携工具链，不进 git
  mingit/            Git 2.56.0（免安装版）
```

## 快速开始

```powershell
$git = "$PWD\.tools\mingit\cmd\git.exe"

# 建/校验数据库
python db\init_db.py

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

### 2. 调 API 不能用 curl / Invoke-WebRequest

沙箱下这两个走 Windows schannel，会报 `SEC_E_NO_CREDENTIALS`，TLS 握手失败。
**用 Python 或 Node 发请求**（走 OpenSSL，实测正常）。

### 3. PowerShell 控制台中文乱码

跑脚本前加：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 4. ffmpeg 尚未安装

拼接、烧字幕、竖屏转码都需要它，目前**缺失**。属于后期环节，不影响出片。

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
| GitHub 远程 | ⏳ 待接入（SSH 密钥已生成） |
| ffmpeg | ❌ 缺失 |
| Ark API Key | ⏳ 待提供，之后可跑真实生成 |
