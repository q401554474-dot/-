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


RECENT_DAYS = 4
DEFAULT_TECH_BENCHMARK_CODE = "sh000688"
DEFAULT_TECH_BENCHMARK_NAME = "科创50指数"


@dataclass(frozen=True)
class Change:
    day: date
    pct: float


@dataclass(frozen=True)
class Benchmark:
    code: str
    name: str
    changes: tuple[Change, ...]

    @property
    def latest(self) -> float | None:
        return self.changes[0].pct if self.changes else None

    @property
    def avg(self) -> float | None:
        return avg_pct(self.changes)

    @property
    def text(self) -> str:
        return changes_text(self.changes, "暂无科技基准数据")


@dataclass(frozen=True)
class Suggestion:
    action: str
    reason: str


@dataclass(frozen=True)
class Fund:
    code: str
    name: str
    price: float
    pct: float
    ts: datetime
    changes: tuple[Change, ...]

    @property
    def direction(self) -> str:
        if self.pct > 0:
            return "上涨"
        if self.pct < 0:
            return "下跌"
        return "持平"

    @property
    def avg(self) -> float | None:
        return avg_pct(self.changes)

    @property
    def four_day_text(self) -> str:
        return changes_text(self.changes, "暂无近四日数据")

    @property
    def four_day_compare(self) -> str:
        if self.avg is None:
            return "暂无近四日数据，无法对比"
        diff = self.pct - self.avg
        if abs(diff) < 0.005:
            return f"与近四日平均 {fmt_pct(self.avg)} 基本持平"
        return f"近四日平均 {fmt_pct(self.avg)}，今日/当前{'高于' if diff > 0 else '低于'} {abs(diff):.2f} 个百分点"


def main() -> int:
    codes = split_codes(os.getenv("FUND_CODES", ""))
    if not codes:
        print("FUND_CODES is empty. Example: FUND_CODES=161725,025701")
        return 1

    mode = os.getenv("REPORT_MODE", "alerts").strip().lower()
    threshold = env_float("MONITOR_THRESHOLD", 3.0)
    timeout = env_int("REQUEST_TIMEOUT_SECONDS", 10)

    benchmark, benchmark_error = load_benchmark(timeout)
    if benchmark:
        print(f"Technology benchmark: {benchmark.name}({benchmark.code}) recent4={benchmark.text}")
    elif benchmark_error:
        print(f"! technology benchmark failed: {benchmark_error}")

    print(f"Checking {len(codes)} fund(s), threshold={threshold}%, mode={mode}")
    funds: list[Fund] = []
    alerts: list[Fund] = []
    failures: list[str] = []

    for code in codes:
        try:
            fund = fetch_fund(code, timeout)
        except Exception as exc:
            failures.append(f"{code}: {exc}")
            print(f"! {code} failed: {exc}")
            continue
        funds.append(fund)
        if abs(fund.pct) >= threshold:
            alerts.append(fund)
        print(
            f"- {fund.name}({fund.code}) today={fmt_pct(fund.pct)} "
            f"direction={fund.direction} recent4={fund.four_day_text} "
            f"benchmark={benchmark_compare(fund, benchmark)} price={fund.price:.4f}"
        )

    if benchmark_error:
        failures.append(f"科技基准指数: {benchmark_error}")

    if mode in {"status", "all", "daily"}:
        subject = os.getenv("EMAIL_SUBJECT") or "每日基金状态"
        body = build_status_text(funds, alerts, threshold, failures, benchmark)
        html = build_email_html("每日基金状态", funds, alerts, threshold, failures, benchmark, daily=True)
        send_email(body, subject, html)
        print(f"Daily status email sent for {len(funds)} fund(s), {len(alerts)} alert(s).")
        return 0

    if not alerts:
        print("No alert: no fund reached the threshold.")
        for failure in failures:
            print("  " + failure)
        return 0

    subject = os.getenv("EMAIL_SUBJECT") or "基金特殊提醒"
    body = build_alert_text(alerts, threshold, failures, benchmark)
    html = build_email_html("基金特殊提醒", alerts, alerts, threshold, failures, benchmark, daily=False)
    send_email(body, subject, html)
    print(f"Alert email sent for {len(alerts)} alert(s).")
    return 0


def split_codes(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\s,，;；]+", raw) if part.strip()]


def fetch_fund(code: str, timeout: int) -> Fund:
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("fund code must be 6 digits")

    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
    request = Request(
        url,
        headers={"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0 fund-monitor/1.0"},
    )
    try:
        text = read_text(request, timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        fallback = fetch_fund_from_history(code, timeout)
        if fallback:
            return fallback
        raise RuntimeError("fund quote request failed") from exc

    match = re.search(r"jsonpgz\((.*)\);?", text.strip())
    if not match:
        raise RuntimeError("fund quote response is empty or invalid")

    payload = json.loads(match.group(1))
    price = to_float(payload.get("gsz")) or to_float(payload.get("dwjz"))
    if price <= 0:
        raise RuntimeError("fund price is invalid")

    ts = parse_time(payload.get("gztime") or payload.get("jzrq"))
    return Fund(
        code=code,
        name=str(payload.get("name") or code),
        price=price,
        pct=to_float(payload.get("gszzl")),
        ts=ts,
        changes=fetch_fund_changes(code, ts.date(), timeout),
    )


def fetch_fund_from_history(code: str, timeout: int) -> Fund | None:
    rows = fund_history_rows(code, timeout)
    if not rows:
        return None
    latest = rows[0]
    trade_day = parse_date(latest.get("FSRQ"))
    price = to_float(latest.get("DWJZ"))
    if trade_day is None or price <= 0:
        return None
    changes = tuple(
        Change(day=row_day, pct=to_float(row.get("JZZZL")))
        for row in rows[1 : RECENT_DAYS + 1]
        if (row_day := parse_date(row.get("FSRQ"))) is not None
    )
    return Fund(
        code=code,
        name=code,
        price=price,
        pct=to_float(latest.get("JZZZL")),
        ts=datetime.combine(trade_day, datetime.min.time()),
        changes=changes,
    )


def fetch_fund_changes(code: str, current_day: date, timeout: int) -> tuple[Change, ...]:
    previous: list[Change] = []
    fallback: list[Change] = []
    for row in fund_history_rows(code, timeout):
        trade_day = parse_date(row.get("FSRQ"))
        if trade_day is None:
            continue
        item = Change(day=trade_day, pct=to_float(row.get("JZZZL")))
        if trade_day < current_day:
            previous.append(item)
        else:
            fallback.append(item)
        if len(previous) >= RECENT_DAYS:
            break
    return tuple((previous or fallback)[:RECENT_DAYS])


def fund_history_rows(code: str, timeout: int) -> list[dict[str, object]]:
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=12&startDate=&endDate="
    request = Request(
        url,
        headers={"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0 fund-monitor/1.0"},
    )
    try:
        data = json.loads(read_text(request, timeout))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []
    rows = data.get("Data", {}).get("LSJZList", [])
    return rows if isinstance(rows, list) else []


def load_benchmark(timeout: int) -> tuple[Benchmark | None, str | None]:
    code = os.getenv("TECH_BENCHMARK_CODE", DEFAULT_TECH_BENCHMARK_CODE).strip() or DEFAULT_TECH_BENCHMARK_CODE
    name = os.getenv("TECH_BENCHMARK_NAME", DEFAULT_TECH_BENCHMARK_NAME).strip() or DEFAULT_TECH_BENCHMARK_NAME
    try:
        return fetch_benchmark(code, name, timeout), None
    except Exception as exc:
        return None, str(exc)


def fetch_benchmark(raw_code: str, name: str, timeout: int) -> Benchmark:
    display_code, symbol = normalize_index_code(raw_code)
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={RECENT_DAYS + 2}"
    )
    request = Request(
        url,
        headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0 fund-monitor/1.0"},
    )
    try:
        rows = json.loads(read_text(request, timeout))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("technology benchmark request failed") from exc

    closes: list[tuple[date, float]] = []
    for row in rows if isinstance(rows, list) else []:
        trade_day = parse_date(row.get("day")) if isinstance(row, dict) else None
        close = to_float(row.get("close")) if isinstance(row, dict) else 0
        if trade_day and close > 0:
            closes.append((trade_day, close))

    changes = [
        Change(day=current[0], pct=((current[1] - previous[1]) / previous[1]) * 100)
        for previous, current in zip(closes, closes[1:])
        if previous[1] > 0
    ]
    if not changes:
        raise RuntimeError("technology benchmark response has no kline data")
    return Benchmark(code=display_code, name=name, changes=tuple(reversed(changes[-RECENT_DAYS:])))


def normalize_index_code(raw_code: str) -> tuple[str, str]:
    compact = raw_code.strip().lower().replace(".", "")
    if compact.startswith("sh"):
        exchange, code = "sh", compact[2:]
    elif compact.startswith("sz"):
        exchange, code = "sz", compact[2:]
    else:
        exchange, code = "sh", compact
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("technology benchmark code must be sh000688, sz399006, or a 6-digit index code")
    return f"{exchange}{code}", f"{exchange}{code}"


def build_alert_text(alerts: list[Fund], threshold: float, failures: list[str], benchmark: Benchmark | None) -> str:
    lines = [
        f"基金特殊提醒：以下基金今日/当前涨跌幅已达到 {threshold:.2f}% 阈值。",
        "邮件内已按近四日对比、科技基准和规则化操作参考整理。",
        "",
        benchmark_line(benchmark),
        "",
    ]
    for fund in sorted(alerts, key=lambda item: abs(item.pct), reverse=True):
        suggestion = suggest(fund, benchmark, threshold)
        lines.append(
            f"【{fund.name}({fund.code})】今日/当前 {fmt_pct(fund.pct)}（{fund.direction}）；"
            f"{fund.four_day_compare}；{benchmark_compare(fund, benchmark)}；"
            f"操作参考：{suggestion.action}，{suggestion.reason}"
        )
    append_failures(lines, failures)
    lines.extend(["", "本邮件由 GitHub Actions 云端每小时自动检查发送。"])
    return "\n".join(lines)


def build_status_text(
    funds: list[Fund],
    alerts: list[Fund],
    threshold: float,
    failures: list[str],
    benchmark: Benchmark | None,
) -> str:
    lines = [
        f"每日全部基金状态：共获取到 {len(funds)} 只基金。",
        f"特殊提醒阈值：今日/当前涨跌幅达到 {threshold:.2f}% 。",
        "",
        benchmark_line(benchmark),
        "",
    ]
    if alerts:
        lines.append("【达到特殊提醒阈值】")
        lines.extend(build_alert_text(alerts, threshold, [], benchmark).splitlines()[5:])
        lines.append("")
    lines.append("【全部基金状态：今日/当前幅度 + 近四日对比 + 科技基准 + 操作参考】")
    for fund in sorted(funds, key=lambda item: item.pct, reverse=True):
        suggestion = suggest(fund, benchmark, threshold)
        lines.append(
            f"{fund.name}({fund.code}) | 今日/当前 {fmt_pct(fund.pct)}（{fund.direction}） | "
            f"近四日 {fund.four_day_text} | {fund.four_day_compare} | "
            f"{benchmark_compare(fund, benchmark)} | 操作参考：{suggestion.action}，{suggestion.reason} | "
            f"估值 {fund.price:.4f} | 更新时间 {fund.ts:%Y-%m-%d %H:%M:%S}"
        )
    if not funds:
        lines.append("本次没有成功获取到基金状态。")
    append_failures(lines, failures)
    lines.extend(["", "本邮件由 GitHub Actions 云端每日 13:00（北京时间）自动发送。"])
    return "\n".join(lines)


def build_email_html(
    title: str,
    funds: list[Fund],
    alerts: list[Fund],
    threshold: float,
    failures: list[str],
    benchmark: Benchmark | None,
    daily: bool,
) -> str:
    cards = "\n".join(fund_card(fund, benchmark, threshold) for fund in sorted(funds, key=lambda item: item.pct, reverse=True))
    if not cards:
        cards = '<div style="padding:16px;border:1px solid #e5e7eb;border-radius:8px;text-align:center;color:#666;">本次没有成功获取到基金状态。</div>'
    alert_block = ""
    if alerts:
        alert_block = (
            '<div style="margin:14px 0;padding:12px;border:1px solid #f1b8b3;background:#fff5f5;border-radius:8px;">'
            '<div style="font-weight:700;color:#d93025;margin-bottom:8px;">重点提醒</div>'
            + "".join(fund_card(fund, benchmark, threshold, compact=True) for fund in sorted(alerts, key=lambda item: abs(item.pct), reverse=True))
            + "</div>"
        )
    failure_block = ""
    if failures:
        items = "".join(f"<li>{escape(item)}</li>" for item in failures)
        failure_block = f'<div style="margin-top:14px;padding:12px;border:1px solid #f5c2c7;background:#fff5f5;border-radius:8px;color:#842029;"><strong>以下项目本次获取失败</strong><ul>{items}</ul></div>'
    footer = "本邮件由 GitHub Actions 云端每日 13:00（北京时间）自动发送。" if daily else "本邮件由 GitHub Actions 云端每小时自动检查发送。"
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f8fa;font-family:Arial,'Microsoft YaHei',sans-serif;color:#1f2328;">
    <div style="max-width:720px;margin:0 auto;padding:14px 10px;">
      <div style="background:#fff;border:1px solid #d8dee4;border-radius:10px;overflow:hidden;">
        <div style="padding:17px;background:#24292f;color:#fff;">
          <div style="font-size:21px;font-weight:700;">{escape(title)}</div>
          <div style="font-size:13px;opacity:.85;margin-top:5px;">检查时间：{datetime.now():%Y-%m-%d %H:%M:%S}</div>
        </div>
        <div style="padding:15px;">
          <div style="border:1px solid #e5e7eb;background:#f6f8fa;border-radius:8px;padding:11px;line-height:1.8;">
            <div>基金数量：<strong>{len(funds)}</strong></div>
            <div>特殊提醒：<strong style="color:#d93025;">{len(alerts)}</strong></div>
            <div>提醒阈值：<strong>{threshold:.2f}%</strong></div>
          </div>
          {benchmark_card(benchmark)}
          {alert_block}
          <div style="font-size:16px;font-weight:700;margin:18px 0 10px;">全部基金状态</div>
          {cards}
          {failure_block}
          <div style="margin-top:16px;padding-top:12px;border-top:1px solid #e5e7eb;color:#57606a;font-size:12px;line-height:1.7;">
            {escape(footer)}<br>
            操作参考由固定规则自动生成，只用于提醒你复盘，不构成投资建议。请结合仓位、风险承受能力和基金持仓再决定。
          </div>
        </div>
      </div>
    </div>
  </body>
</html>"""


def benchmark_card(benchmark: Benchmark | None) -> str:
    if benchmark is None:
        return '<div style="margin:12px 0;padding:12px;border:1px solid #f5c2c7;background:#fff5f5;border-radius:8px;color:#842029;">科技基准指数：本次没有获取到数据。</div>'
    latest = "暂无" if benchmark.latest is None else fmt_pct(benchmark.latest)
    average = "暂无" if benchmark.avg is None else fmt_pct(benchmark.avg)
    color = small_color(benchmark.latest or 0)
    return f"""
    <div style="margin:12px 0;padding:12px;border:1px solid #d8dee4;background:#f6f8fa;border-radius:8px;line-height:1.75;">
      <div style="font-weight:700;">科技基准指数：{escape(benchmark.name)}（{escape(benchmark.code)}）</div>
      <div>最近一日：<strong style="color:{color};">{latest}</strong>；近四日平均：<strong>{average}</strong></div>
      <div style="color:#57606a;">近四日：{escape(benchmark.text)}</div>
    </div>
    """


def fund_card(fund: Fund, benchmark: Benchmark | None, threshold: float, compact: bool = False) -> str:
    important = abs(fund.pct) >= threshold
    suggestion = suggest(fund, benchmark, threshold)
    border = "#d93025" if important else "#d8dee4"
    bg = "#fff5f5" if important else "#fff"
    badge = '<span style="margin-left:6px;padding:2px 7px;background:#d93025;color:#fff;border-radius:999px;font-size:12px;">重点提醒</span>' if important else ""
    details = "" if compact else f"""
      <div>当前估值：<strong>{fund.price:.4f}</strong></div>
      <div style="color:#57606a;">更新时间：{fund.ts:%Y-%m-%d %H:%M:%S}</div>
      <div style="margin-top:7px;">本基金近四日：{escape(fund.four_day_text)}</div>
      <div>近四日对比：{escape(fund.four_day_compare)}</div>
      <div>科技基准对比：{escape(benchmark_compare(fund, benchmark))}</div>
    """
    return f"""
    <div style="margin:0 0 11px;padding:12px 13px;border:1px solid {border};background:{bg};border-radius:8px;line-height:1.75;word-break:break-word;">
      <div style="font-weight:700;">{escape(fund.name)}（{escape(fund.code)}）{badge}</div>
      <div style="margin-top:4px;font-size:20px;font-weight:700;color:{change_color(fund.pct, threshold)};">
        {fmt_pct(fund.pct)} <span style="font-size:13px;color:#57606a;font-weight:400;">{escape(fund.direction)}</span>
      </div>
      {details}
      <div style="margin-top:8px;padding:9px 10px;background:#fff8e1;border:1px solid #f0d98c;border-radius:6px;">
        操作参考：<strong>{escape(suggestion.action)}</strong><br>
        <span style="color:#5f4b00;">{escape(suggestion.reason)}</span>
      </div>
    </div>
    """


def suggest(fund: Fund, benchmark: Benchmark | None, threshold: float) -> Suggestion:
    current = fund.pct
    bench_latest = benchmark.latest if benchmark else None
    if current >= threshold:
        return Suggestion("卖出/分批止盈", "今日涨幅达到特殊提醒阈值，若已有持仓可考虑分批止盈；不建议急涨时一次性追高买入。")
    if current <= -threshold:
        if bench_latest is not None and current < bench_latest - 1:
            return Suggestion("补仓/小额分批", "跌幅达到阈值且弱于科技基准，补仓需更谨慎，适合小额分批并等待止跌信号。")
        return Suggestion("补仓/小额分批", "跌幅达到阈值，若长期看好且仓位不高，可考虑小额分批补仓，避免一次性重仓。")
    if fund.avg is not None and current > fund.avg + 1:
        if bench_latest is None or current >= bench_latest:
            return Suggestion("买入/轻仓试探", "今日表现高于近四日平均且不弱于科技基准，若未持有可小额试探，已有持仓继续观察。")
        return Suggestion("观察/暂缓买入", "今日强于本基金近四日平均，但弱于科技基准，说明相对科技板块不算强，建议先观察。")
    if fund.avg is not None and current < fund.avg - 1:
        return Suggestion("补仓/继续观察", "今日表现低于近四日平均，若长期持有可分批补仓；若仓位已高，先观察风险。")
    if benchmark and benchmark.avg is not None and current >= 0 and current >= benchmark.avg:
        return Suggestion("买入/小额观察", "当前表现不弱于科技基准近四日平均，但未达到强提醒阈值，更适合小额观察而不是重仓。")
    return Suggestion("观察/暂不操作", "当前涨跌幅没有达到明显信号，建议等待更清晰的趋势或按原定定投计划执行。")


def benchmark_compare(fund: Fund, benchmark: Benchmark | None) -> str:
    if benchmark is None or benchmark.latest is None:
        return "科技基准暂无数据，无法对比"
    diff = fund.pct - benchmark.latest
    relative = "与科技基准基本持平" if abs(diff) < 0.005 else f"{'强于' if diff > 0 else '弱于'}科技基准 {abs(diff):.2f} 个百分点"
    if fund.avg is not None and benchmark.avg is not None:
        return f"{benchmark.name}最近一日 {fmt_pct(benchmark.latest)}，本基金{relative}；近四日平均：本基金 {fmt_pct(fund.avg)}，科技基准 {fmt_pct(benchmark.avg)}"
    return f"{benchmark.name}最近一日 {fmt_pct(benchmark.latest)}，本基金{relative}"


def benchmark_line(benchmark: Benchmark | None) -> str:
    if benchmark is None:
        return "科技基准指数：本次没有获取到数据。"
    latest = "暂无" if benchmark.latest is None else fmt_pct(benchmark.latest)
    average = "暂无" if benchmark.avg is None else fmt_pct(benchmark.avg)
    return f"科技基准指数：{benchmark.name}({benchmark.code})，最近一日 {latest}，近四日平均 {average}，近四日：{benchmark.text}"


def append_failures(lines: list[str], failures: list[str]) -> None:
    if failures:
        lines.extend(["", "以下项目本次获取失败："])
        lines.extend(failures)


def read_text(request: Request, timeout: int, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(1 + attempt)
    if last_error:
        raise last_error
    raise RuntimeError("request failed")


def send_email(body: str, subject: str, html: str | None = None) -> None:
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
    if html:
        email.add_alternative(html, subtype="html")

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
        return 0.0 if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return 0.0


def avg_pct(changes: tuple[Change, ...]) -> float | None:
    return None if not changes else sum(item.pct for item in changes) / len(changes)


def changes_text(changes: tuple[Change, ...], empty: str) -> str:
    if not changes:
        return empty
    return "；".join(f"{item.day:%Y-%m-%d} {fmt_pct(item.pct)}" for item in changes)


def fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def change_color(value: float, threshold: float) -> str:
    return "#d93025" if abs(value) >= threshold else small_color(value)


def small_color(value: float) -> str:
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
