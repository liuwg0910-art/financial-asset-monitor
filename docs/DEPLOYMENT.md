# 安装、部署与备份

## 1、Python 一键部署（推荐，无需 Docker）

先安装 Python 3.11–3.13，Windows 安装时勾选 **Add python.exe to PATH**。下载项目 ZIP 后完整解压，进入包含 `start.bat`、`backend` 和 `frontend` 的项目根目录。

**Windows：双击 `start.bat` 即可。** 也可在项目根目录的 PowerShell 中运行：

```powershell
.\start.bat
```

**macOS / Linux：** 在项目根目录打开终端运行：

```bash
bash start.sh
```

脚本会自动创建 `.venv`、安装 Python 依赖并启动网站。首次运行需要联网下载依赖，等待终端显示启动完成后，打开 [http://127.0.0.1:19192](http://127.0.0.1:19192)。不需要手动激活虚拟环境，也不需要 Node.js。依赖未变化时，再次启动会跳过安装。

下次使用仍运行同一个脚本。保持启动窗口运行，按 `Ctrl+C` 停止；关闭窗口或电脑休眠后，监控与微信推送停止。网页关闭后，只要后端仍在运行且联网，微信通知可以继续。

如果提示找不到 Python，请重新安装并勾选 PATH，关闭并重新打开终端；如果依赖下载失败，检查网络后重新运行脚本；如果提示 19192 端口被占用，请先关闭已经运行的本程序，再重新启动。此方式只监听本机地址。

## 2、Docker 部署（可选）

先安装并启动 Docker Desktop（或 Docker Engine 与 Compose 插件），然后在项目根目录运行：

```bash
docker compose up -d --build
```

等待构建完成后打开 [http://localhost:19191](http://localhost:19191)。查看运行状态和日志：

```bash
docker compose ps
docker compose logs --tail=100 backend
```

停止：`docker compose down`。更新代码后重新执行构建启动命令。Docker 是可选部署工具，Python 一键部署不依赖它。

Docker 前端 Nginx 监听 19191，并转发 `/api/`、`/ws` 至后端 9090；当前 Compose 将宿主 `backend/` 挂载至 `/app/data` 保存运行数据。JSON 存储应保持一个后端进程/实例，不要增加 workers 或横向扩容。

## 3、使用 AI 工具自动化安装

适合使用 WorkBuddy、Codex、Claude 等具备本地文件访问和终端执行能力的 AI 工具。先下载并解压项目，在 AI 工具中打开项目根目录，再复制下面的指令。若尚未下载，可把仓库地址一并提供给 AI 工具；使用时将占位内容替换为实际地址和路径。

```text
请帮我在本机安装并启动“金融资产监控看板”。请实际执行安装、启动和检查，不要只给操作建议。

项目目录：<填写项目解压后的完整路径>
仓库地址：<已下载可省略；否则填写实际 GitHub 仓库地址>

1. 阅读项目 README.md、docs/DEPLOYMENT.md 和启动脚本，检查操作系统及 Python 环境。
2. 优先采用 Python 一键部署，不依赖 Docker。需要 Python 3.11–3.13；缺少合适版本时，说明需要安装的内容，并按工具权限完成安装，保留系统现有环境。
3. 在项目根目录启动：Windows 使用 start.bat；macOS / Linux 使用 bash start.sh。脚本会创建 .venv、安装 backend/requirements.txt 并运行网站。遇到错误时检查原因并修复安装环境。
4. 检查 19192 端口。如果本项目已在运行，复用现有服务；如果被其他程序占用，先说明情况，不要终止不相关进程。将服务留在可持续运行的终端或工具支持的后台进程中。
5. 验证 http://127.0.0.1:19192/api/health 和首页能访问，然后打开 http://127.0.0.1:19192/。行情源暂时不可用时，分别说明本地服务与外部数据的状态。
6. 保留已有自选和预警设置，不读取或展示通知密钥，不发送微信测试通知。安装完成后告诉我访问地址、服务如何保持运行，以及停止和下次启动的方法。
```

AI 工具需要具备并获得本地目录、联网下载和执行命令的权限；只有聊天能力的工具不能直接安装。若工具请求系统安装或执行权限，请核对内容后按需授权。运行是否能在 AI 会话结束后继续，取决于工具的进程管理方式；如服务随会话结束而停止，直接运行 `start.bat` 或 `bash start.sh` 并保持终端打开即可。

## 配置

| 项 | 默认值 / 行为 |
| --- | --- |
| `WATCHLIST_FILE` | 本机默认`backend/custom_watchlist.json` |
| Docker中的`WATCHLIST_FILE` | `/app/data/custom_watchlist.json` |
| 规则、微信与历史 | 与自选同目录下`alerts_state.json` |
| 贴水快照 | 与自选同目录下`basis_snapshot.json` |
| 微信SendKey | 在预警中心填写，当前不通过环境变量配置 |

自定义自选路径前，先建立其父目录并确保进程可写。项目不自动加载`.env`；如使用自定义环境文件，需要由Shell或容器工具明确注入。

## 服务器与访问控制

当前Compose端口映射没有限定回环地址，可能监听宿主所有接口；后端本地独立启动也监听所有接口。请通过宿主防火墙限制访问，仅开放给可信设备，或在对外发布前设置经过验证的认证、HTTPS及访问控制。仓库公开不代表应直接公开运行中的服务。

服务器使用Nginx同源转发API与WebSocket，HTTPS站点使用WSS。代理需保留Host及WebSocket升级头。第三方行情域名需要可达；增加访问认证时同时保护页面、API、API文档与WebSocket。

目前没有提供“开箱即用的公网多用户部署”。GitHub Pages只能承载静态内容，不能运行FastAPI和后台推送，[官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)。

## 备份和恢复

1. 先停止后端，防止复制时文件发生变化。
2. 备份自选目录中的`custom_watchlist.json`、`alerts_state.json`和可选`basis_snapshot.json`。
3. 将备份保存在仓库以外的受控位置；`alerts_state.json`包含SendKey，不要发送到Issue或公开网盘。
4. 恢复时停止服务，把文件放回对应目录，再启动后端。

不要在服务运行时直接编辑JSON。删除贴水快照会使历史在下次访问时重新获取，不能据此恢复普通波动规则的内存采样。

## 排错

先访问`/api/health`，再检查日志。页面可以打开但没有价格时，分别确认后端连接、外部行情源访问和交易时段。API通而WebSocket不通时，检查代理的升级头。微信推送失败时，先核对本地配置和第三方服务额度，公开日志前进行脱敏。
