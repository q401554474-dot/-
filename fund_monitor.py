from __future__ import annotations

import json
import os
import re
import smtplib
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FundQuote:
    code: str
    name: str
    price: float
    change_percent: float
    timestamp: datetime

    @property
    def direction(self) -> str:
        return "上涨" if self.change_percent >= 0 else "下跌"

    @property
    def line(self) -> str:
        return f"{self.name}({self.code}) {self.change_percent:+.2f}% 当前估值 {self.price:.4f} 时间 {self.timestamp:%Y-%m-%d %H:%M:%S}"

    @property
    def alert_message(self) -> str:
        return (
            f"【基金特殊提醒】{self.name}({self.code}){self.direction}{abs(self.change_percent):.2f}%，"
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
        print(f"- {quote.name}({quote.code}) {quote.change_percent:+.2f}% price={quote.price:.4f}")
        if abs(quote.change_percent) >= threshold:
            alerts.append(quote)

    if mode in {"status", "all", "daily"}:
        subject = os.getenv("EMAIL_SUBJECT") or "每日基金状态"
        body = build_status_email_body(quotes, alerts, threshold, failures)
        send_email(body, subject)
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
    send_email(body, subject)
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

    return FundQuote(
        code=code,
        name=str(payload.get("name") or code),
        price=price,
        change_percent=change_percent,
        timestamp=parse_time(payload.get("gztime") or payload.get("jzrq")),
    )


def build_alert_email_body(alerts: list[FundQuote], threshold: float, failures: list[str]) -> str:
    lines = [
        f"基金特殊提醒：以下基金涨跌幅已达到 {threshold:.2f}% 阈值。",
        "",
    ]
    for quote in alerts:
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
        f"每日基金状态：共获取到 {len(quotes)} 只基金。",
        f"特殊提醒阈值：涨跌幅达到 {threshold:.2f}% 。",
        "",
    ]

    if alerts:
        lines.append("【达到特殊提醒阈值】")
        for quote in alerts:
            lines.append(quote.alert_message)
        lines.append("")

    lines.append("【全部基金状态】")
    if quotes:
        for quote in sorted(quotes, key=lambda item: item.change_percent, reverse=True):
            lines.append(quote.line)
    else:
        lines.append("本次没有成功获取到基金状态。")

    append_failures(lines, failures)
    lines.extend(["", "本邮件由 GitHub Actions 云端每日 13:00（北京时间）自动发送。"])
    return "\n".join(lines)


def append_failures(lines: list[str], failures: list[str]) -> None:
    if failures:
        lines.extend(["", "以下基金本次获取失败："])
        lines.extend(failures)


def send_email(body: str, subject: str) -> None:
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


def parse_time(value: object) -> datetime:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    return datetime.now()


if __name__ == "__main__":
    raise SystemExit(main())
