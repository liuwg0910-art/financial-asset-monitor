# 金融资产监控看板

**多市场行情、自选管理、国内外期货、IC / IM年化贴水与微信预警。**

金融资产监控看板是面向个人使用的金融行情工作台。前端采用原生 HTML、CSS 和 JavaScript，后端采用 FastAPI，获取真实报价与历史K线，并提供持续运行的后台监控。适合集中关注股票、指数、商品、加密货币及期货跨季价差的用户，不提供下单功能。

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 多市场自选 | 名称/代码搜索，行情验证后添加，删除、分类与关键词筛选 |
| 股票与指数 | A股、港股、美股、恒生等指数报价与历史走势 |
| 全球市场 | 全球主要股指及商品基准，独立于自选列表展示 |
| 国内期货 | 45个常用品种，涵盖商品、股指和国债，支持主力连续与指定交割月 |
| 国际期货 | 布伦特、WTI、天然气、黄金、白银、铜及主要农产品 |
| 真实历史K线 | 分钟、小时、日线，具体周期取决于品种和数据源 |
| IC / IM贴水 | 自动匹配季月，实时年化贴水、半年/一年历史与区间极值 |
| 集中预警 | 滚动价格波动、贴水阈值、冷却、防重复触发 |
| 通知 | 网站内提示、可选浏览器通知、Server酱Turbo微信通知 |
| 数据恢复 | 缓存、有限重试、贴水独立连接池与最近有效快照 |

## 安装方式

### 1、Python 一键部署（推荐，无需 Docker）

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

### 2、Docker 部署（可选）

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

### 3、使用 AI 工具自动化安装

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

更多配置见[部署与备份说明](docs/DEPLOYMENT.md)。

## 使用示例

1. 侧栏输入“招商银行”“螺纹钢”或“布伦特”，选择匹配代码，点击“添加到自选”。
2. 点击行情卡片查看K线，通过可用按钮切换周期。
3. 进入“IC / IM贴水”查看季月合约组合、带负号的年化价差及历史曲线。
4. 在“预警中心”设置波动或贴水规则，主动添加后才会启用。
5. 需要微信通知时配置Server酱Turbo SendKey，保存并发送测试通知。

代码示例：`600036`、`00700`、`AAPL`、`BTCUSDT`、`RB0`、`RB2701`、`AU0`、`IF2611`、`BZ=F`、`GCZ26.CMX`。带年月的合约会到期，能否添加以实际有效报价为准。

## IC / IM计算口径

```text
年化贴水率（%）=（下季价格－当季价格）÷ 当季价格 × 4 × 100%
```

例如当季100、下季97.5，结果为 **−10%**。数值越负，贴水越深。这是跨季合约价差，不是对现货的基差，也不是保证金收益率。

实时值与历史收盘值分开显示；历史按当日对应两腿的同日收盘价重建，缺失不补点，换季处断线。阈值告警仅使用同步且新鲜的报价。

## 文档

- [使用指南](docs/USER_GUIDE.md)
- [功能与数据说明](docs/FEATURES.md)
- [部署、配置和备份](docs/DEPLOYMENT.md)
- [API参考](docs/API.md)
- [首次上传GitHub](docs/GITHUB_PUBLISH.md)
- [贡献指南](CONTRIBUTING.md)
- [安全说明](SECURITY.md)
- [更新记录](CHANGELOG.md)

## 目录与技术

```text
backend/          FastAPI、行情适配、期货、贴水与预警
backend/tests/    离线回归与手动联网实测
frontend/static/ 原生多页面前端
frontend/nginx.conf  API与WebSocket反向代理
docs/             使用、功能、部署、API与发布说明
.github/          自动测试、Issue及PR模板
docker-compose.yml
LICENSE
```

后端采用单进程JSON存储，没有数据库、账户认证、用户隔离及交易执行功能，不适合直接作为公网多用户服务。

## 测试

安装依赖后，在项目根目录运行离线回归：

```bash
python -m unittest discover -s backend/tests -p "test_*.py"
node backend/tests/frontend_regressions.cjs
node backend/tests/frontend_alerts.cjs
```

GitHub Actions配置运行上述回归、JavaScript语法检查和Docker构建。工作流首次推送后才会在GitHub执行，当前不宣称远程CI已通过。

联网实测独立运行，不默认放入CI：

```bash
python backend/tests/live_kline_check.py
python backend/tests/live_global_market_check.py
python backend/tests/live_futures_check.py
python backend/tests/live_domestic_futures_check.py
python backend/tests/live_basis_check.py
```

报告写入本地`docs/*_live_results.json`。公开接口可能限流、延迟或变更，历史测试成功不代表持续可用。

## 数据与运行边界

- 新浪财经、Yahoo Finance、Binance提供相关行情，腾讯财经提供部分指数日线。使用外部数据与接口需遵守相应服务条款。
- 自选约15秒、全球市场约60秒、贴水约30秒刷新。注意页面显示的是获取时间还是源报价时间。
- 网页关闭后浏览器通知不可用；微信推送依赖后端持续运行、联网、SendKey与第三方服务额度。
- GitHub托管代码，不等于完整应用托管。GitHub Pages仅支持静态站点，不能运行本项目的FastAPI、后台监控和微信推送。[官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- 不要上传SendKey、个人自选、运行状态、快照及日志。忽略规则无法清除已上传的历史。

## 反馈与贡献

通过仓库Issues提交问题或功能建议，通过Pull Request贡献修复。请提供品种、周期、页面表现及已脱敏日志，不要上传密钥、令牌或完整本地状态。参与前请阅读[贡献指南](CONTRIBUTING.md)。

## 许可证

代码与随附文档使用 [MIT License](LICENSE)。第三方数据、依赖、服务和标识的权利不因本仓库许可证而转移。行情与指标仅供信息参考，不构成投资建议或收益承诺。
