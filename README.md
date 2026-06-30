# 基金云端邮箱提醒

这个仓库会通过 GitHub Actions 云端定时检查基金涨跌幅。电脑关机后也能运行。

## 当前规则

1. 每小时检查一次基金。
2. 涨跌幅达到 `MONITOR_THRESHOLD`，默认 3%，发送“基金特殊提醒”。
3. 每天北京时间 13:00 发送“全部基金状态”。
4. 邮件使用手机友好的卡片排版，不再使用很宽的表格，邮箱页面不需要左右滑动。
5. 每只基金都会显示：
   - 今日/当前涨跌幅，并明确写出“上涨 / 下跌 / 持平”。
   - 本基金近四日历史涨跌幅。
   - 今日/当前涨跌幅与近四日平均涨跌幅的对比。
   - 科技基准指数近四日涨跌幅，以及本基金相对科技基准的强弱。
   - 规则化操作参考：卖出 / 买入 / 补仓 / 观察。
6. 达到特殊提醒阈值的重点基金会标红显示。

> 操作参考由固定规则自动生成，只用于提醒复盘，不构成投资建议。请结合仓位、风险承受能力和基金持仓再决定。

## 1. 修改基金号码、阈值、科技基准

进入仓库页面：

```text
Settings -> Secrets and variables -> Actions -> Variables
```

常用 Variables：

```text
FUND_CODES=161725,025701
MONITOR_THRESHOLD=3
EMAIL_SUBJECT=基金特殊提醒
EMAIL_STATUS_SUBJECT=每日基金状态
TECH_BENCHMARK_CODE=sh000688
TECH_BENCHMARK_NAME=科创50指数
```

说明：

- `FUND_CODES`：基金号码，多个用英文逗号隔开。
- `MONITOR_THRESHOLD`：特殊提醒阈值。填 `3` 表示涨跌达到 3% 才发特殊提醒。
- `EMAIL_SUBJECT`：每小时特殊提醒邮件标题。
- `EMAIL_STATUS_SUBJECT`：每天 13:00 全部基金状态邮件标题。
- `TECH_BENCHMARK_CODE`：科技基准指数代码。默认 `sh000688`，也可以填例如 `sz399006`。
- `TECH_BENCHMARK_NAME`：科技基准指数显示名称。默认“科创50指数”。

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

如果你想马上测试“全部基金状态邮件”，选择 `status` 运行一次最方便。

## 4. 定时规则

当前有两条云端定时规则：

```text
0 * * * *     每小时检查一次，达到 3% 等阈值才发特殊提醒
0 5 * * *     UTC 05:00，也就是北京时间 13:00，发送全部基金状态
```

北京时间 13:00 这一轮会跳过普通每小时提醒，避免重复发送。

GitHub 的定时任务可能会有几分钟延迟，这是正常现象。

## 重要提醒

- 电脑关机也能运行，因为任务在 GitHub 云端执行。
- 每小时只在达到阈值时发送特殊提醒。
- 每天北京时间 13:00 会发送全部基金状态。
- 全部基金状态会显示今日/当前幅度、近四日涨跌幅、科技基准对比和操作参考。
- 不要把邮箱授权码写进代码文件，只放在 GitHub Secrets 里。
