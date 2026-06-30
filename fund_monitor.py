from __future__ import annotations

import json
import os
import re
import smtplib
import time
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from html import escape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HistoryChange:
    trade_date: date
    change_percent: float


@dataclass(frozen=True)
class FundQuote:
    code: str
    name: str
    price: float
    change_percent: float
    timestamp: datetime
    recent_changes: tuple[HistoryChange, ...] = ()

    @property
    def direction(self) -> str:
        if self.change_percent > 0:
            return "上涨"
        if self.change_percent < 0:
            return "下跌"
        return "持平"

    @property
    def previous_trade_date(self) -> date | None:
        return self.recent_changes[0].trade_date if self.recent_changes else None

    @property
    def previous_change_percent(self) -> float | None:
        return self.recent_changes[0].change_percent if self.recent_changes else None

    @property
    def previous_change_text(self) -> str:
        if self.previous_change_percent is None:
            return "暂无"
        label = f"{self.previous_trade_date:%Y-%m-%d}" if self.previous_trade_date else "最近净值日"
        return f"{label} {format_percent(self.previous_change_percent)}"

    @property
    def recent_changes_text(self) -> str:
        if not self.recent_changes:
            return "暂无近三日数据"
        return "；".join(
            f"{item.trade_date:%Y-%m-%d} {format_percent(item.change_percent)}"
            for item in self.recent_changes
        )

    @property
    def recent_average(self) -> float | None:
        if not self.recent_changes:
            return None
        return sum(item.change_percent for item in self.recent_changes) / len(self.recent_changes)

    @property
    def three_day_compare_text(self) -> str:
        average = self.recent_average
        if average is None:
            return "暂无近三日数据，无法对比"
        diff = self.change_percent - average
        if abs(diff) < 0.005:
            return f"与近三日平均 {format_percent(average)} 基本持平"
        relation = "高于" if diff > 0 else "低于"
        return f"近三日平均 {format_percent(average)}，今日/当前{relation} {abs(diff):.2f} 个百分点"

    @property
    def line(self) -> str:
        return (
            f"{self.name}({self.code}) | "
            f"今日/当前 {format_percent(self.change_percent)}（{self.direction}） | "
            f"近三日 {self.recent_changes_text} | "
            f"{self.three_day_compare_text} | "
            f"当前估值 {self.price:.4f} | "
            f"更新时间 {self.timestamp:%Y-%m-%d %H:%M:%S}"
        )

    @property
    def alert_message(self) -> str:
        return (
            f"【基金特殊提醒】{self.name}({self.code}) "
            f"今日/当前{self.direction} {abs(self.change_percent):.2f}%，"
            f"{self.three_day_compare_text}，"
            f"近三日：{self.recent_changes_text}，"
            f"当前估值 {self.price:.4f}，时间 {self.timestamp:%Y-%m-%d %H:%M:%S}。"
            "仅为行情提醒，不构成投资建议。"
        )


def main() -> int:
    codes = split_codes(os.getenv("FUND_CODES", ""))
    if not codes:
        print("FUND_CODES is empty. Example: FUND_CODES=161725,025701")
        return 1

    mode = os.getenv("REPORT_MODE", "alerts").strip().lower()
    threshold = env_float("MONITOR_THRESHOLD", 3.0)
    timeout = env_int("REQUEST_TIMEOUT_SECONDS", 10)

    print(f"Checking {len(codes)} fund(s), threshold={threshold}%, mode={mode}")
    quotes: list[FundQuote] = []
    alerts: list[FundQuote] = []
    failures: list[str] = []

    for code in codes:
        try:
            quote = fetch_fund_quote(code, timeout)
        except Exception as exc:
            failures.append(f"{code}: {exc}")
            print(f"! {code} failed: {exc}")
            continue

        quotes.append(quote)
        print(
            f"- {quote.name}({quote.code}) today={format_percent(quote.change_percent)} "
            f"direction={quote.direction} recent3={quote.recent_changes_text} "
            f"price={quote.price:.4f}"
        )
        if abs(quote.change_percent) >= threshold:
            alerts.append(quote)

    if mode in {"status", "all", "daily"}:
        subject = os.getenv("EMAIL_SUBJECT") or "每日基金状态"
        body = build_status_email_body(quotes, alerts, threshold, failures)
        html = build_status_email_html(quotes, alerts, threshold, failures)
        send_email(body, subject, html)
        print(f"Daily status email sent for {len(quotes)} fund(s), {len(alerts)} alert(s).")
        return 0

    if not alerts:
        print("No alert: no fund reached the threshold.")
        if failures:
            print("Failures:")
            for failure in failures:
                print("  " + failure)
        return 0

    subject = os.getenv("EMAIL_SUBJECT") or "基金特殊提醒"
    body = build_alert_email_body(alerts, threshold, failures)
    html = build_alert_email_html(alerts, threshold, failures)
    send_email(body, subject, html)
    print(f"Alert email sent for {len(alerts)} alert(s).")
    return 0


def split_codes(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\s,，;；]+", raw) if part.strip()]


def fetch_fund_quote(code: str, timeout_seconds: int) -> FundQuote:
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("fund code must be 6 digits")

    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
    request = Request(
        url,
        headers={
            "Referer": "https://fund.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 fund-monitor/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("fund quote request failed") from exc

    match = re.search(r"jsonpgz\((.*)\);?", text.strip())
    if not match:
        raise RuntimeError("fund quote response is empty or invalid")

    payload = json.loads(match.group(1))
    price = to_float(payload.get("gsz")) or to_float(payload.get("dwjz"))
    change_percent = to_float(payload.get("gszzl"))
    if price <= 0:
        raise RuntimeError("fund price is invalid")

    timestamp = parse_time(payload.get("gztime") or payload.get("jzrq"))
    recent_changes = fetch_recent_changes(code, timestamp.date(), timeout_seconds)

    return FundQuote(
        code=code,
        name=str(payload.get("name") or code),
        price=price,
        change_percent=change_percent,
        timestamp=timestamp,
        recent_changes=recent_changes,
    )


def fetch_recent_changes(code: str, current_date: date, timeout_seconds: int, limit: int = 3) -> tuple[HistoryChange, ...]:
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=10&startDate=&endDate="
    request = Request(
        url,
        headers={
            "Referer": "https://fundf10.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 fund-monitor/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
        payload = json.loads(text)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return ()

    rows = payload.get("Data", {}).get("LSJZList", [])
    previous_days: list[HistoryChange] = []
    fallback_days: list[HistoryChange] = []
    for row in rows:
        trade_date = parse_date(row.get("FSRQ"))
        if trade_date is None:
            continue
        item = HistoryChange(trade_date=trade_date, change_percent=to_float(row.get("JZZZL")))
        if trade_date < current_date:
            previous_days.append(item)
        else:
            fallback_days.append(item)
        if len(previous_days) >= limit:
            break

    if previous_days:
        return tuple(previous_days[:limit])
    return tuple(fallback_days[:limit])


def build_alert_email_body(alerts: list[FundQuote], threshold: float, failures: list[str]) -> str:
    lines = [
        f"基金特殊提醒：以下基金今日/当前涨跌幅已达到 {threshold:.2f}% 阈值。",
        "重点基金已在 HTML 邮件中标红展示。",
        "",
    ]
    for quote in sorted(alerts, key=lambda item: abs(item.change_percent), reverse=True):
        lines.append(quote.alert_message)
    append_failures(lines, failures)
    lines.extend(["", "本邮件由 GitHub Actions 云端每小时自动检查发送。"])
    return "\n".join(lines)


def build_status_email_body(
    quotes: list[FundQuote],
    alerts: list[FundQuote],
    threshold: float,
    failures: list[str],
) -> str:
    lines = [
        f"每日全部基金状态：共获取到 {len(quotes)} 只基金。",
        f"特殊提醒阈值：今日/当前涨跌幅达到 {threshold:.2f}% 。",
        "HTML 邮件中会标红达到阈值的重点消息。",
        "",
    ]

    if alerts:
        lines.append("【达到特殊提醒阈值】")
        for quote in sorted(alerts, key=lambda item: abs(item.change_percent), reverse=True):
            lines.append(quote.alert_message)
        lines.append("")

    lines.append("【全部基金状态：今日/当前幅度 + 近三天对比】")
    if quotes:
        for quote in sorted(quotes, key=lambda item: item.change_percent, reverse=True):
            lines.append(quote.line)
    else:
        lines.append("本次没有成功获取到基金状态。")

    append_failures(lines, failures)
    lines.extend(["", "本邮件由 GitHub Actions 云端每日 13:00（北京时间）自动发送。"])
    return "\n".join(lines)


def build_alert_email_html(alerts: list[FundQuote], threshold: float, failures: list[str]) -> str:
    sorted_alerts = sorted(alerts, key=lambda item: abs(item.change_percent), reverse=True)
    return build_email_html(
        title="基金特殊提醒",
        subtitle=f"以下基金今日/当前涨跌幅已达到 {threshold:.2f}% 阈值，重点消息已标红。",
        quotes=sorted_alerts,
        alerts=sorted_alerts,
        threshold=threshold,
        failures=failures,
        footer="本邮件由 GitHub Actions 云端每小时自动检查发送。",
    )


def build_status_email_html(
    quotes: list[FundQuote],
    alerts: list[FundQuote],
    threshold: float,
    failures: list[str],
) -> str:
    sorted_quotes = sorted(quotes, key=lambda item: item.change_percent, reverse=True)
    sorted_alerts = sorted(alerts, key=lambda item: abs(item.change_percent), reverse=True)
    return build_email_html(
        title="每日基金状态",
        subtitle=f"共获取到 {len(quotes)} 只基金；涨跌幅达到 {threshold:.2f}% 的基金已标红提醒。",
        quotes=sorted_quotes,
        alerts=sorted_alerts,
        threshold=threshold,
        failures=failures,
        footer="本邮件由 GitHub Actions 云端每日 13:00（北京时间）自动发送。",
    )


def build_email_html(
    title: str,
    subtitle: str,
    quotes: list[FundQuote],
    alerts: list[FundQuote],
    threshold: float,
    failures: list[str],
    footer: str,
) -> str:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_count = len(alerts)
    rows_html = "\n".join(fund_row_html(quote, threshold) for quote in quotes)
    if not rows_html:
        rows_html = """
        <tr>
          <td colspan="5" style="padding:16px;border:1px solid #e5e7eb;color:#666;text-align:center;">
            本次没有成功获取到基金状态。
          </td>
        </tr>
        """

    alert_html = ""
    if alerts:
        alert_items = "\n".join(alert_card_html(quote, threshold) for quote in alerts)
        alert_html = f"""
        <div style="margin:18px 0 20px 0;padding:14px 16px;border:1px solid #f1b8b3;background:#fff5f5;border-radius:8px;">
          <div style="font-size:16px;font-weight:700;color:#d93025;margin-bottom:10px;">重点提醒</div>
          {alert_items}
        </div>
        """

    failures_html = ""
    if failures:
        failure_items = "".join(f"<li>{escape(item)}</li>" for item in failures)
        failures_html = f"""
        <div style="margin-top:18px;padding:12px 14px;border:1px solid #f5c2c7;background:#fff5f5;border-radius:8px;color:#842029;">
          <div style="font-weight:700;margin-bottom:6px;">以下基金本次获取失败</div>
          <ul style="margin:0;padding-left:20px;">{failure_items}</ul>
        </div>
        """

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f8fa;font-family:Arial,'Microsoft YaHei',sans-serif;color:#1f2328;">
    <div style="max-width:980px;margin:0 auto;padding:24px 12px;">
      <div style="background:#ffffff;border:1px solid #d8dee4;border-radius:10px;overflow:hidden;">
        <div style="padding:20px 22px;background:#24292f;color:#ffffff;">
          <div style="font-size:22px;font-weight:700;line-height:1.3;">{escape(title)}</div>
          <div style="font-size:13px;opacity:.85;margin-top:6px;">检查时间：{escape(checked_at)}</div>
        </div>

        <div style="padding:18px 22px;">
          <div style="font-size:15px;line-height:1.7;margin-bottom:12px;">{escape(subtitle)}</div>

          <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin:12px 0 4px 0;">
            <tr>
              <td style="width:33.33%;padding:12px;border:1px solid #e5e7eb;background:#f6f8fa;">
                <div style="font-size:12px;color:#57606a;">基金数量</div>
                <div style="font-size:20px;font-weight:700;margin-top:4px;">{len(quotes)}</div>
              </td>
              <td style="width:33.33%;padding:12px;border:1px solid #e5e7eb;background:#f6f8fa;">
                <div style="font-size:12px;color:#57606a;">特殊提醒</div>
                <div style="font-size:20px;font-weight:700;color:#d93025;margin-top:4px;">{alert_count}</div>
              </td>
              <td style="width:33.33%;padding:12px;border:1px solid #e5e7eb;background:#f6f8fa;">
                <div style="font-size:12px;color:#57606a;">提醒阈值</div>
                <div style="font-size:20px;font-weight:700;margin-top:4px;">{threshold:.2f}%</div>
              </td>
            </tr>
          </table>

          {alert_html}

          <div style="font-size:16px;font-weight:700;margin:20px 0 10px 0;">全部基金状态</div>
          <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #d8dee4;font-size:13px;">
            <thead>
              <tr>
                <th align="left" style="padding:10px;border:1px solid #d8dee4;background:#f6f8fa;">基金</th>
                <th align="left" style="padding:10px;border:1px solid #d8dee4;background:#f6f8fa;">今日/当前</th>
                <th align="left" style="padding:10px;border:1px solid #d8dee4;background:#f6f8fa;">近三天涨跌</th>
                <th align="left" style="padding:10px;border:1px solid #d8dee4;background:#f6f8fa;">对比结论</th>
                <th align="left" style="padding:10px;border:1px solid #d8dee4;background:#f6f8fa;">估值/时间</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>

          {failures_html}

          <div style="margin-top:18px;padding-top:12px;border-top:1px solid #e5e7eb;color:#57606a;font-size:12px;line-height:1.6;">
            {escape(footer)}<br>
            仅为行情提醒，不构成投资建议。
          </div>
        </div>
      </div>
    </div>
  </body>
</html>"""


def alert_card_html(quote: FundQuote, threshold: float) -> str:
    color = change_color(quote.change_percent, threshold)
    return f"""
    <div style="margin:8px 0;padding:10px 12px;background:#ffffff;border-left:4px solid #d93025;border-radius:6px;">
      <div style="font-weight:700;color:#d93025;">
        {escape(quote.name)}（{escape(quote.code)}）达到特殊提醒阈值
      </div>
      <div style="margin-top:6px;line-height:1.7;">
        今日/当前 <span style="font-weight:700;color:{color};">{format_percent(quote.change_percent)}（{escape(quote.direction)}）</span>；
        {escape(quote.three_day_compare_text)}；
        当前估值 {quote.price:.4f}
      </div>
    </div>
    """


def fund_row_html(quote: FundQuote, threshold: float) -> str:
    important = abs(quote.change_percent) >= threshold
    row_background = "#fff5f5" if important else "#ffffff"
    emphasis = "font-weight:700;color:#d93025;" if important else ""
    return f"""
    <tr style="background:{row_background};">
      <td style="padding:10px;border:1px solid #d8dee4;vertical-align:top;">
        <div style="font-weight:700;{emphasis}">{escape(quote.name)}</div>
        <div style="color:#57606a;margin-top:3px;">{escape(quote.code)}</div>
        {threshold_badge_html(quote, threshold)}
      </td>
      <td style="padding:10px;border:1px solid #d8dee4;vertical-align:top;">
        <div style="font-size:16px;font-weight:700;color:{change_color(quote.change_percent, threshold)};">
          {format_percent(quote.change_percent)}
        </div>
        <div style="margin-top:3px;color:#57606a;">{escape(quote.direction)}</div>
      </td>
      <td style="padding:10px;border:1px solid #d8dee4;vertical-align:top;">
        {recent_changes_html(quote)}
      </td>
      <td style="padding:10px;border:1px solid #d8dee4;vertical-align:top;line-height:1.6;">
        {escape(quote.three_day_compare_text)}
      </td>
      <td style="padding:10px;border:1px solid #d8dee4;vertical-align:top;line-height:1.6;">
        <div>估值：<strong>{quote.price:.4f}</strong></div>
        <div style="color:#57606a;">{quote.timestamp:%Y-%m-%d %H:%M:%S}</div>
      </td>
    </tr>
    """


def threshold_badge_html(quote: FundQuote, threshold: float) -> str:
    if abs(quote.change_percent) < threshold:
        return ""
    return """
    <div style="display:inline-block;margin-top:7px;padding:3px 7px;background:#d93025;color:#ffffff;border-radius:999px;font-size:12px;">
      重点提醒
    </div>
    """


def recent_changes_html(quote: FundQuote) -> str:
    if not quote.recent_changes:
        return '<span style="color:#57606a;">暂无近三日数据</span>'
    rows = []
    for item in quote.recent_changes:
        rows.append(
            "<div style=\"line-height:1.7;white-space:nowrap;\">"
            f"<span style=\"color:#57606a;\">{item.trade_date:%m-%d}</span> "
            f"<span style=\"font-weight:700;color:{small_change_color(item.change_percent)};\">"
            f"{format_percent(item.change_percent)}</span>"
            "</div>"
        )
    return "".join(rows)


def append_failures(lines: list[str], failures: list[str]) -> None:
    if failures:
        lines.extend(["", "以下基金本次获取失败："])
        lines.extend(failures)


def send_email(body: str, subject: str, html_body: str | None = None) -> None:
    host = required_env("EMAIL_SMTP_HOST")
    port = env_int("EMAIL_SMTP_PORT", 465)
    user = required_env("EMAIL_SMTP_USER")
    password = required_env("EMAIL_SMTP_PASSWORD")
    to_email = required_env("ALERT_TO_EMAIL")
    from_email = os.getenv("EMAIL_FROM") or user
    use_ssl = env_bool("EMAIL_SMTP_SSL", True)

    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = from_email
    email["To"] = to_email
    email.set_content(body)
    if html_body:
        email.add_alternative(html_body, subtype="html")

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            smtp.login(user, password)
            smtp.send_message(email)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(email)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def to_float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_percent(value: float) -> str:
    return f"{value:+.2f}%"


def change_color(value: float, threshold: float) -> str:
    if abs(value) >= threshold:
        return "#d93025"
    return small_change_color(value)


def small_change_color(value: float) -> str:
    if value > 0:
        return "#d93025"
    if value < 0:
        return "#188038"
    return "#57606a"


def parse_time(value: object) -> datetime:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    return datetime.now()


def parse_date(value: object) -> date | None:
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
