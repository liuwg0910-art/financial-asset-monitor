# 贡献指南

感谢参与金融资产监控看板。请先阅读[README](README.md)与[功能说明](docs/FEATURES.md)，确认建议与当前支持范围。

## 问题反馈

通过Issues提供复现步骤、操作系统、Python/Node版本、启动方式、品种代码、周期和时间。截图及日志请脱敏，不要上传SendKey、令牌、完整运行状态或个人备份。公开源暂时限流、休市与产品代码错误应分开描述。

安全问题按[安全说明](SECURITY.md)处理，避免公开利用细节和凭证。

## 开发与验证

从仓库建立自己的分支，在虚拟环境安装`backend/requirements.txt`，使用`python backend/tests/serve_ui.py`运行本地页面。保持原生前端与现有API兼容，不为页面开发强制引入构建依赖。

提交前运行：

```bash
python -m unittest discover -s backend/tests -p "test_*.py"
node backend/tests/frontend_regressions.cjs
node backend/tests/frontend_alerts.cjs
```

新增行情适配应验证代码路由、字段口径、时间戳、异常与不可用状态。新增计算应保留符号、单位、合约身份和来源；不得用模拟价格冒充真实行情。离线测试使用隔离文件和模拟外部响应，不发送实际微信通知、不依赖公开源实时可用。

只有修改外部适配或数据契约时，按需手动运行`live_*.py`。生成报告留在本地，不把网络实测当作确定性的CI门槛。

## Pull Request

描述具体问题、修改后的行为和验证方法。功能或部署方式变化时同步文档；界面调整提供已脱敏截图。不要把本地配置、运行JSON、快照、日志及无关文件加入提交。

代码和随附文档按仓库MIT许可证贡献；新增第三方内容时说明其来源与许可。
