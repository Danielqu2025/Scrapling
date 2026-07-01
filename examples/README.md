# Examples 示例应用

本目录包含基于 Scrapling 的完整示例应用，以及将示例**打包为便携版**（exe + 数据库，解压即用）的通用工具。

## 目录

| 路径 | 说明 |
|------|------|
| [sh_eia/](sh_eia/) | 上海市环评双源检索 Web 应用（投用前 link + 投用后 e2） |
| [packager/](packager/) | 便携打包库（PyInstaller） |
| [package_app.py](package_app.py) | 打包 CLI：`list` / `init` / `build` |
| [requirements-portable.txt](requirements-portable.txt) | 打包工具依赖（PyInstaller） |

## 便携打包快速上手

### 1. 为已有应用生成配置（首次）

应用需为 **FastAPI Web 应用**，推荐结构：

```
examples/my_app/
  app/main.py          # 含 app = FastAPI(...)
  app/static/          # 前端（可选，会自动打进包）
  data/*.db            # 数据库（可选，打包时复制进 dist）
  requirements-app.txt
  portable_launcher.py       ← init 自动生成
  portable.manifest.json     ← init 自动生成
```

```powershell
# 在仓库根目录
python examples/package_app.py init my_app --title "我的应用"
```

`init` 会自动检测：静态目录、数据库、Playwright 依赖、uvicorn 入口等，并生成上述两个文件。  
若目录中尚无 `_paths.py`，会额外生成 `_paths.portable.example.py` 供参考。

生成后请人工确认：

- `_paths.py` 是否通过 `{前缀}_APP_ROOT` 环境变量定位 `data/`（便携版必需）
- `portable.manifest.json` 中的依赖与 `seed_data` 是否正确

### 2. 安装依赖并打包

```powershell
pip install -r examples/my_app/requirements-app.txt
pip install -r examples/requirements-portable.txt

python examples/package_app.py build my_app
# 不内置浏览器（体积更小）：
python examples/package_app.py build my_app --no-browsers
```

输出：

```
examples/my_app/dist/
  MyApp_portable/          # 解压即用目录
    MyApp.exe
    data/                  # 若打包时存在 *.db 会复制到此
    使用说明.txt
  MyApp_portable.zip       # 可直接分发给同事
```

### 3. CLI 命令一览

```powershell
python examples/package_app.py list                    # 列出已配置 manifest 的应用
python examples/package_app.py init <app> [选项]       # 自动生成 manifest + launcher
python examples/package_app.py build <app>             # 打包单个应用
python examples/package_app.py build --all             # 打包全部
```

`init` 常用选项：

| 选项 | 说明 |
|------|------|
| `--title "名称"` | 界面与说明中的显示名 |
| `--exe MyApp` | 生成的 exe 文件名（不含 `.exe`） |
| `--env-prefix MY_APP` | 环境变量前缀，默认由目录名推导 |
| `--port 8080` | 默认 Web 端口 |
| `--force` | 覆盖已存在的 manifest / launcher |

## 为新应用添加打包：检查清单

1. 应用能在开发模式下正常运行（如 `python 04_run_server.py`）
2. 运行 `python examples/package_app.py init <app>`
3. 按 `_paths.portable.example.py` 调整 `_paths.py`（读写 `data/`）
4. 检查 `portable.manifest.json`（`hidden_imports`、`collect_all`、`seed_data`）
5. `pip install` 应用依赖 + `requirements-portable.txt`
6. `python examples/package_app.py build <app>`
7. 在本机解压 `dist/*_portable.zip`，双击 exe 验证

## portable.manifest.json 字段说明

| 字段 | 含义 |
|------|------|
| `exe_name` | PyInstaller 输出的 exe 名 |
| `entry_script` | 入口脚本，通常为 `portable_launcher.py` |
| `add_data` | 打进包的静态资源（`from` → `to`） |
| `seed_data` | 打包时复制到 dist 的文件（如 `data/eia.db`） |
| `hidden_imports` / `collect_all` | PyInstaller 额外依赖 |
| `bundle_playwright_browsers` | 是否打包 Chromium（可选，体积大） |

完整示例见 [sh_eia/portable.manifest.json](sh_eia/portable.manifest.json)。

## 相关文档

- [sh_eia/README.md](sh_eia/README.md) — 环评应用功能、同步、Web 界面说明
