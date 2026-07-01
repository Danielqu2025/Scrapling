# 上海市环评公开信息抓取与检索系统

本目录包含两层能力：

1. **命令行示例**（`01` ~ `03`）：验证抓取逻辑
2. **团队 Web 应用**（`04_run_server.py`）：给同事用的搜索、同步、下载界面

目标数据源：[上海市生态环境局环评公示](https://link.sthj.sh.gov.cn/shhj/fa/cms/shhj/hpgs_gz_login.jsp?applyItem=1&gongshiType=3&approvType=1)

支持的三种公示类型：

| 类型 | 说明 |
|------|------|
| 受理信息 | 受理阶段的公示与公众参与材料 |
| 拟审批公示 | 拟审批决定前的项目概况与措施文件 |
| 审批决定公告 | 环评报告 + 政府批复文件 |

---

## 一、环境准备

在仓库根目录：

```powershell
cd D:\github\Scrapling
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[fetchers]"
python -m scrapling.cli install

cd examples\sh_eia
python -m pip install -r requirements-app.txt
```

> 安装浏览器时请在 **仓库根目录** 使用 `python -m scrapling.cli install`，不要直接在子目录运行 `scrapling install`。

---

## 二、团队 Web 应用（推荐给同事使用）

### 启动服务

```powershell
cd D:\github\Scrapling\examples\sh_eia
python 04_run_server.py
```

浏览器打开：`http://127.0.0.1:8080`

局域网同事访问：`http://你的电脑IP:8080`

### 功能

- **关键字搜索**：项目名称、建设单位、批文号等（支持中文模糊匹配）
- **类型筛选**：三种公示类型可多选
- **手动同步**：「每类 1 页」适合日常增量；「全量同步」抓取官网全部列表
- **批量下载**：勾选文件后打包 ZIP 下载
- **定期同步**：通过环境变量开启

### 定期自动更新本地库

```powershell
$env:SH_EIA_SYNC_HOURS = "24"          # 每 24 小时同步一次
$env:SH_EIA_SYNC_MAX_PAGES = "1"       # 定时任务每类同步 1 页；设为 all 表示全量
python 04_run_server.py
```

也可部署到一台内网服务器，同事统一访问。

### 数据存储位置

| 路径 | 内容 |
|------|------|
| `data/eia.db` | 本地 SQLite 数据库（项目 + 文件索引） |
| `data/downloads/` | 可选的下载缓存目录 |

---

## 三、命令行示例（开发调试用）

```powershell
python 01_explore_list.py      # 探路：过告知页并查看列表
python 02_crawl_spider.py        # 抓取审批决定元数据
python 03_download_files.py      # 下载 JSONL 中的附件
```

---

## 四、目录结构

```
examples/sh_eia/
├── _common.py              # 三种公示解析、告知页自动化
├── db/store.py             # SQLite + 全文/模糊搜索
├── sync/crawler.py         # 同步官网数据到本地库
├── app/main.py             # FastAPI 后端
├── app/static/index.html   # Web 界面
├── 01_explore_list.py
├── 02_crawl_spider.py
├── 03_download_files.py
├── 04_run_server.py        # 启动团队服务
├── requirements-app.txt
└── data/eia.db             # 运行后生成
```

---

### 启动时检查官网更新

服务启动后会自动抓取官网每类公示的第 1 页，与本地库比对：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SH_EIA_STARTUP_CHECK` | `1` | 设为 `0` 关闭启动检查 |
| `SH_EIA_STARTUP_CHECK_MODE` | `remind` | `remind` 有新数据时页面提醒；`auto` 自动同步；`off` 不检查 |

无新数据时不弹窗、不提醒。

```powershell
$env:SH_EIA_STARTUP_CHECK_MODE = "auto"   # 发现新公示后自动同步
python 04_run_server.py
```

---

## 五、部署建议（给同事长期使用）

| 场景 | 建议 |
|------|------|
| 3~10 人内网使用 | 一台 Windows/Linux 主机运行 `04_run_server.py` |
| 需要账号权限 | 前面加 Nginx + 公司 SSO，或后续加简单登录 |
| 数据量变大 | 将 SQLite 换为 PostgreSQL |
| 稳定定时更新 | `SH_EIA_SYNC_HOURS=24` + 服务器常驻运行 |

---

## 六、便携版打包（免安装 Python）

适合分发给不熟悉环境的同事：打成单个 `ShEIA.exe`，双击即可使用。

### 打包步骤（只需打包者操作一次）

在仓库根目录完成 Scrapling 安装后：

```powershell
cd E:\Cursor\Scrapling\examples\sh_eia
pip install -r requirements-app.txt
pip install -r requirements-portable.txt
python build_portable.py
```

默认会把 Playwright Chromium 浏览器（约 650MB）一并打入 `browsers/` 目录，同事解压后可直接同步，无需再下载。

若希望缩小体积、由使用者自行下载浏览器：

```powershell
$env:SH_EIA_BUNDLE_BROWSERS = "0"
python build_portable.py
```

生成文件：

| 文件 | 说明 |
|------|------|
| `dist/ShEIA.exe` | 单文件可执行程序 |
| `dist/ShEIA_portable.zip` | 含 exe、data 目录、使用说明，可直接转发 |

### 同事如何使用

1. 解压 `ShEIA_portable.zip`
2. 双击 `ShEIA.exe`，浏览器会自动打开
3. 数据保存在同目录的 `data/eia.db`

**无需安装 Python、pip 或虚拟环境。**

### 数据库导入 / 导出

在 Web 页面中：

- **导出数据库 (ZIP)**：打包本地 `eia.db` 及统计信息
- **导入数据库**：上传 `.zip` 或 `.db` 文件，自动备份当前库后恢复

这样 A 同事同步好的数据，导出后发给 B 同事导入即可直接检索，无需重新抓取。

### 首次同步说明

便携版默认**已内置浏览器**，解压即可同步官网。

若打包时设置了 `SH_EIA_BUNDLE_BROWSERS=0`，首次同步会自动下载 Chromium（约 200MB，仅一次）。若仅导入他人数据库做查询，可跳过同步。

---

## 七、合规提示

- 仅抓取政府依法公开的信息
- 控制同步频率，避免对官网造成压力
- 全量同步约 181 页 × 3 类，首次建议在非高峰时段执行
