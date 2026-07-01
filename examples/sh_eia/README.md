# 上海市环评公开信息抓取与检索系统

整合上海市生态环境局**投用前**（[link.sthj](https://link.sthj.sh.gov.cn)）与**投用后**（[e2.sthj](https://e2.sthj.sh.gov.cn)）两套公示数据，提供搜索、全生命周期查看、同步与便携分发。

## 能力概览

| 能力 | 说明 |
|------|------|
| 双源同步 | link：受理 / 拟审批 / 审批决定；e2：中后期建设、调试、验收 |
| Web 检索 | 关键字 + 类型 / 年份 / 区域 / 阶段 / 来源筛选 |
| 全生命周期 | 7 阶段时间轴（基本信息 → 投用前三步 → 建设 / 调试 / 验收） |
| 环评轮次 | 同名项目不同批文（如初评 / 变更）分列展示 |
| 验证码下载 | e2 附件在应用内输入验证码代下载 |
| 数据库导入导出 | 同事间共享已同步数据，无需重复抓取 |
| 便携版 | 打包为 exe + zip，免安装 Python |

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

> 安装浏览器请在**仓库根目录**执行 `python -m scrapling.cli install`。

---

## 二、启动 Web 应用

```powershell
cd examples\sh_eia
python 04_run_server.py
```

浏览器打开：`http://127.0.0.1:8080`（局域网：`http://<本机IP>:8080`）

### 主要功能

- **搜索**：项目名称、建设单位、批文号；左侧面板筛选年份、区域、阶段、来源
- **环评轮次**：同一项目名若有不同批文（如 2023 初评 / 2026 变更），结果中分别显示；进展弹窗按轮次过滤
- **全量同步**：先检查本地是否完整，完整则跳过下载；仅 e2 不全时只重下 e2
- **强制全量重下**：勾选工具栏「强制全量重下」后点全量同步，跳过检查、link + e2 全部重抓
- **导出/导入数据库**：页面顶部导出 ZIP 或导入同事备份

### 全量同步与完整性检查

| 操作 | 行为 |
|------|------|
| 全量同步（默认） | 对比官网列表页统计；已完整则提示并跳过 |
| 勾选「强制全量重下」 | 跳过检查，强制重新下载 |
| 仅同步中后期（1 页） | 增量试探，不跑完整性检查 |

API：`GET /api/sync/completeness` 可单独查看检查报告。

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `SH_EIA_HOST` | `0.0.0.0` | 监听地址 |
| `SH_EIA_PORT` | `8080` | 端口 |
| `SH_EIA_SYNC_HOURS` | `0` | 定时同步间隔（小时）；`0` 关闭 |
| `SH_EIA_SYNC_MAX_PAGES` | `1` | 定时任务每类页数；`all` 为全量 |
| `SH_EIA_STARTUP_CHECK` | `1` | 启动时检查官网是否有新公示 |
| `SH_EIA_STARTUP_CHECK_MODE` | `remind` | `remind` / `auto` / `off` |

### 数据文件

| 路径 | 内容 |
|------|------|
| `data/eia.db` | SQLite（项目主档 + 事件 + 附件索引） |
| `data/manifest.json` | 同步成功后写入的数据清单 |
| `data/downloads/` | 可选下载缓存 |

---

## 三、命令行示例（开发调试）

```powershell
python 01_explore_list.py       # link 列表探路
python 02_crawl_spider.py       # link 审批决定爬虫示例
python 03_download_files.py     # 按 JSONL 下载附件
python 05_explore_e2_list.py    # e2 列表探路
```

---

## 四、目录结构

```
examples/sh_eia/
├── app/
│   ├── main.py              # FastAPI：搜索、同步、下载、导入导出
│   └── static/index.html    # Web 界面
├── db/
│   ├── store.py             # SQLite 存储与搜索
│   ├── resolver.py          # 项目归并（不同批文号分列）
│   └── timeline_view.py     # 7 阶段全生命周期视图
├── sources/
│   ├── link_sthj/           # 投用前数据源
│   └── e2_qygk/             # 投用后数据源（含详情页、验证码下载）
├── sync/
│   ├── crawler.py           # 双源同步编排
│   ├── checker.py           # 官网增量检查
│   └── completeness.py      # 全量同步前完整性对比
├── portable_launcher.py     # 便携版 exe 入口
├── portable.manifest.json   # 便携打包配置
├── build_portable.py        # 兼容入口（委托 examples/package_app.py）
├── 04_run_server.py         # 开发环境启动
└── data/eia.db              # 运行后生成
```

---

## 五、便携版打包

适合分发给未安装 Python 的同事。通用说明见 [examples/README.md](../README.md)。

### 打包（已有 manifest，直接 build）

```powershell
cd examples\sh_eia
pip install -r requirements-app.txt
pip install -r ..\requirements-portable.txt
python ..\package_app.py build sh_eia
```

不内置浏览器（减小约 650MB）：

```powershell
$env:SH_EIA_BUNDLE_BROWSERS = "0"
python ..\package_app.py build sh_eia --no-browsers
```

或在 `sh_eia` 目录：`python build_portable.py`

### 输出

| 文件 | 说明 |
|------|------|
| `dist/ShEIA_portable/` | 解压即用目录（exe + data + 说明） |
| `dist/ShEIA_portable.zip` | 可直接转发 |

### 同事使用

1. 解压 zip
2. 双击 `ShEIA.exe`，浏览器自动打开
3. 数据在 `data/eia.db`；可通过页面导出/导入与同事共享

### 重新生成打包配置

若大幅改动应用结构，可重新 scaffold（会覆盖，先备份）：

```powershell
python ..\package_app.py init sh_eia --title "上海环评资料检索" --exe ShEIA --force
```

---

## 六、部署建议

| 场景 | 建议 |
|------|------|
| 3~10 人内网 | 一台主机运行 `04_run_server.py` 或便携版 exe |
| 权限控制 | 前置 Nginx + SSO |
| 长期更新 | `SH_EIA_SYNC_HOURS=24` + 服务器常驻 |
| 数据共享 | A 全量同步 → 导出数据库 → B 导入 |

---

## 七、合规提示

- 仅抓取政府依法公开的信息
- 控制同步频率，避免对官网造成压力
- 全量同步 link 约 200 页 × 3 类；e2 按年份遍历，首次建议在非高峰时段执行
