# 安全政策

## 报告漏洞

请**不要**通过公开 issue 报告安全问题。

请使用 GitHub 的
[私密漏洞报告](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
功能，或直接联系维护者。

请在报告中说明：受影响的版本、复现步骤、以及你判断的影响范围。

## Bot Token 泄露

如果不小心把 `config.toml` 或 Bot token 提交到了公开仓库：

1. 立即在 Telegram 找 [@BotFather](https://t.me/BotFather) 执行 `/revoke`，
   让旧 token 立刻失效，再生成新的。
2. 用 `git filter-repo` 或 BFG 从历史中彻底清除该文件。
   仅删除最新一次提交是不够的，历史里仍然能查到。
3. 检查是否有异常调用记录。

## 本项目不处理的数据

本 bot 只读取 NodeSeek 的公开 RSS，不需要、也不应配置任何 NodeSeek 账号凭据。
如果发现某处要求你提供第三方站点密码，那是被篡改过的版本。
