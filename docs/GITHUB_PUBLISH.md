# 首次上传GitHub

本文适合已经注册GitHub、尚未创建本项目仓库的情况。建议仓库名为`financial-asset-monitor`，也可以自定。以下步骤不会把网站自动部署到公网。

## 1. 新建空仓库

打开[创建仓库](https://github.com/new)，填写名称及说明，按需要选择Public或Private。建议说明：

> 多市场金融行情与期货监控工作台，支持自选、真实K线、IC/IM年化贴水和微信预警。

本地已经提供README、MIT许可证和忽略文件，新建仓库时**不要再生成这些文件**，避免首次推送冲突。创建后复制仓库HTTPS地址。

可选Topics：`fastapi`、`javascript`、`finance`、`market-data`、`futures`、`dashboard`、`docker`。

## 2. 首次提交前检查

不得上传：

- `backend/alerts_state.json`：包含微信SendKey及个人规则。
- `backend/custom_watchlist.json`：个人自选。
- `backend/basis_snapshot.json`：本地运行快照。
- `.env`、私钥、日志、备份、临时探测脚本及个人截图。
- 与网站无关的演示稿、构建目录及本地助手配置。

这些类别已加入`.gitignore`，但如果之前已被Git跟踪或上传，忽略文件不能清除历史。密钥已泄露时应先在对应服务撤销/更换，不要只删除当前文件。GitHub网页的文件上传不会代替你自动检查所有敏感文件。

## 3. 使用Git上传

安装Git后，在本项目根目录运行。以下适用于首次创建的本地仓库；若已经有Git历史，先检查`git status`和`git remote -v`，不要重新创建或覆盖远端。

```bash
git init -b main
git add .gitignore README.md LICENSE CHANGELOG.md CONTRIBUTING.md SECURITY.md start.bat start.sh scripts docker-compose.yml backend frontend docs .github
git status --short
git diff --cached --stat
```

确认暂存列表没有上述运行数据、凭证或无关文件，然后提交：

```bash
git commit -m "Initial release of 金融资产监控看板"
```

把下一行中的`YOUR_NAME/YOUR_REPO`替换为创建仓库时得到的真实用户名和仓库名：

```bash
git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

如Git提示未设置身份，按自己的提交身份配置此仓库；不要把示例名称或邮箱当作真实身份提交。认证请使用Git提供的浏览器登录或凭证管理器，不要把访问令牌写进远端URL、文档或命令历史。

## 4. 检查仓库

- 首页README与文档链接是否正常。
- GitHub是否识别MIT许可证。
- Actions中的CI是否通过；首次运行前本地文档中的“已配置”不代表远程验证成功。
- 文件列表没有个人自选、SendKey、状态文件、日志或本地构建产物。

仓库创建后，如需托管在线应用，另行按[部署说明](DEPLOYMENT.md)运行后端与Nginx。GitHub Pages不能执行本项目的FastAPI与后台监控。

若使用提供的`金融资产监控看板-github.zip`，请先解压再作为仓库文件上传，**不要只上传ZIP本身**。推荐使用Git或GitHub Desktop，以保留隐藏的`.github`和`.gitignore`配置。

参考：[GitHub README指南](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)、[MIT说明](https://choosealicense.com/licenses/mit/)。
