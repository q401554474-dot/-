# 基金云端邮箱提醒

这个仓库会通过 GitHub Actions 云端定时检查基金涨跌幅。电脑关机后也能运行。

## 当前规则

1. 每小时检查一次基金。
2. 涨跌幅达到 `MONITOR_THRESHOLD`，默认 3%，发送“基金特殊提醒”。
3. 每天北京时间 13:00 发送“全部基金状态”。
4. 全部基金状态邮件会包含：
   - 今日/当前估算幅度
   - 昨日/最近净值日幅度
   - 当前估值
   - 更新时间

## 1. 修改基金号码和阈值

进入仓库页面：

```text
Settings -> Secrets and variables -> Actions -> Variables
```

修改这些 Variables：

```text
FUND_CODES=161725,025701
MONITOR_THRESHOLD=3
EMAIL_SUBJECT=基金特殊提醒
EMAIL_STATUS_SUBJECT=每日基金状态
```

说明：

- `FUND_CODES`：基金号码，多个用英文逗号隔开。
- `MONITOR_THRESHOLD`：特殊提醒阈值。填 `3` 表示涨跌达到 3% 才发特殊提醒。
- `EMAIL_SUBJECT`：每小时特殊提醒邮件标题。
- `EMAIL_STATUS_SUBJECT`：每天 13:00 全部基金状态邮件标题。这个可以不填，不填默认“每日基金状态”。

## 2. 修改邮箱 SMTP

进入：

```text
Settings -> Secrets and variables -> Actions -> Secrets
```

修改这些 Secrets：

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

## 3. 手动测试运行

进入：

```text
Actions -> Fund email monitor -> Run workflow
```

运行时可以选择：

```text
alerts  特殊提醒模式：只有达到阈值才发邮件
status  全部状态模式：无论是否达到阈值都发送全部基金状态
```

如果你想马上测试邮箱是否能发，选择 `status` 运行一次最方便。

## 4. 定时规则

当前设置为每小时检查一次：

```text
0 * * * *
```

GitHub 使用 UTC 时间。北京时间 13:00 是 UTC 05:00，所以脚本会在 UTC 05 点这一轮自动发送全部基金状态。

GitHub 的定时任务可能会有几分钟延迟，这是正常现象。

## 重要提醒

- 电脑关机也能运行，因为任务在 GitHub 云端执行。
- 每小时只在达到阈值时发送特殊提醒。
- 每天北京时间 13:00 会发送全部基金状态。
- 不要把邮箱授权码写进代码文件，只放在 GitHub Secrets 里。
