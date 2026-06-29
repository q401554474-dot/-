# 基金云端邮箱提醒

这个仓库会通过 GitHub Actions 定时检查基金涨跌幅。达到阈值时，会自动发送邮箱提醒。

## 1. 设置基金号码和阈值

进入仓库页面：

```text
Settings -> Secrets and variables -> Actions -> Variables
```

添加这些 Variables：

```text
FUND_CODES=161725,025701
MONITOR_THRESHOLD=3
EMAIL_SUBJECT=基金行情提醒
```

说明：

- `FUND_CODES`：基金号码，多个用英文逗号隔开。
- `MONITOR_THRESHOLD`：提醒阈值。填 `3` 表示涨跌达到 3% 才发邮件。
- `EMAIL_SUBJECT`：邮件标题。

## 2. 设置邮箱 SMTP

进入：

```text
Settings -> Secrets and variables -> Actions -> Secrets
```

添加这些 Secrets：

```text
ALERT_TO_EMAIL=接收提醒的邮箱
EMAIL_SMTP_HOST=smtp.qq.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_USER=发件邮箱
EMAIL_SMTP_PASSWORD=邮箱SMTP授权码
EMAIL_FROM=发件邮箱
EMAIL_SMTP_SSL=true
```

QQ 邮箱常用配置：

```text
EMAIL_SMTP_HOST=smtp.qq.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_SSL=true
```

注意：`EMAIL_SMTP_PASSWORD` 不是 QQ 登录密码，要填 QQ 邮箱里生成的 SMTP 授权码。

## 3. 测试运行

进入：

```text
Actions -> Fund email monitor -> Run workflow
```

如果基金涨跌幅达到阈值，就会发送邮件。

想测试邮箱是否能发，可以临时把 `MONITOR_THRESHOLD` 改成 `1`，测试成功后再改回 `3` 或 `5`。

## 4. 定时规则

当前设置为每 5 分钟检查一次：

```text
*/5 * * * *
```

GitHub 的定时任务可能会有几分钟延迟，这是正常现象。

## 重要提醒

- 电脑关机也能运行，因为任务在 GitHub 云端执行。
- 只有涨跌幅达到阈值才发邮件，不会每 5 分钟无条件发送。
- 不要把邮箱授权码写进代码文件，只放在 GitHub Secrets 里。
