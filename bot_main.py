"""Báo đổi IPv4 public và dự báo thời tiết theo lịch UTC (Python 3.9+)."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import fcntl
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import signal
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)
STATE_FILE = Path(__file__).resolve().parent / "last_ip.txt"
TIMEOUT = 15
UTC = timezone.utc
VIETNAM = timezone(timedelta(hours=7))
WEATHER_FIELDS = (
    "temperature_2m", "precipitation_probability", "rain", "showers",
    "weather_code", "cloud_cover", "wind_speed_10m", "is_day", "sunshine_duration",
)
WEATHER_CODES = {
    0: "Trời quang", 1: "Ít mây", 2: "Mây rải rác", 3: "Nhiều mây, âm u",
    45: "Sương mù", 48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ", 53: "Mưa phùn vừa", 55: "Mưa phùn dày",
    56: "Mưa phùn đóng băng nhẹ", 57: "Mưa phùn đóng băng dày",
    61: "Mưa nhẹ", 63: "Mưa vừa", 65: "Mưa to",
    66: "Mưa đóng băng nhẹ", 67: "Mưa đóng băng to",
    71: "Tuyết nhẹ", 73: "Tuyết vừa", 75: "Tuyết dày", 77: "Hạt tuyết",
    80: "Mưa rào nhẹ", 81: "Mưa rào vừa", 82: "Mưa rào rất to",
    85: "Mưa tuyết rào nhẹ", 86: "Mưa tuyết rào to",
    95: "Dông", 96: "Dông kèm mưa đá nhỏ", 97: "Dông mạnh",
    99: "Dông kèm mưa đá lớn",
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 97, 99}


def get_public_ip():
    # Cả hai endpoint chỉ trả IPv4, tránh nhầm đổi IP do chuyển IPv4/IPv6.
    for endpoint in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            with urlopen(endpoint, timeout=TIMEOUT) as response:
                value = response.read(128).decode("ascii").strip()
            return str(ipaddress.IPv4Address(value))
        except (OSError, URLError, ValueError, UnicodeError):
            continue
    raise RuntimeError("Không lấy được IPv4 public; giữ nguyên IP đã lưu.")


def send_telegram(token, chat_id, old_ip, new_ip):
    send_telegram_message(
        token, chat_id,
        f"Địa chỉ IP public đã thay đổi\nIP cũ: {old_ip}\nIP mới: {new_ip}",
    )


def send_telegram_message(token, chat_id, text):
    payload = urlencode({
        "chat_id": chat_id,
        "text": text,
    }).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Không log URL của request vì URL chứa token.
        raise RuntimeError(f"Telegram trả HTTP {exc.code}; kiểm tra token/chat_id/quyền gửi.") from None
    except (OSError, URLError, ValueError):
        raise RuntimeError("Không xác nhận được Telegram đã nhận tin; sẽ thử lại.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram không xác nhận gửi thành công; sẽ thử lại.")


def save_ip(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    temporary.replace(path)


def check_once(token, chat_id, state_file=STATE_FILE):
    try:
        old_ip = str(ipaddress.IPv4Address(state_file.read_text(encoding="utf-8").strip()))
    except FileNotFoundError:
        old_ip = None
    # File hỏng gây lỗi, không âm thầm xóa mốc so sánh.
    new_ip = get_public_ip()
    if old_ip is None:
        save_ip(state_file, new_ip)
        LOG.info("Ghi nhận IP ban đầu: %s (không gửi Telegram).", new_ip)
    elif old_ip != new_ip:
        send_telegram(token, chat_id, old_ip, new_ip)
        save_ip(state_file, new_ip)
        LOG.info("Đã báo thay đổi IP: %s -> %s", old_ip, new_ip)


def parse_notify_times(value):
    """Đọc danh sách HH:MM theo UTC, ví dụ 00:00,09:00,17:00."""
    result = set()
    for item in value.split(","):
        item = item.strip()
        try:
            parsed = datetime.strptime(item, "%H:%M").time()
        except ValueError:
            raise ValueError("TELEGRAM_NOTIFY_TIMES_UTC phải là danh sách HH:MM (UTC).") from None
        if parsed.strftime("%H:%M") != item:
            raise ValueError("TELEGRAM_NOTIFY_TIMES_UTC phải là danh sách HH:MM (UTC).")
        result.add(parsed)
    return sorted(result)


def get_weather_forecast(latitude, longitude, now):
    # Lấy cả hai đầu mút: lượng mưa/nắng tại T là tổng của giờ TRƯỚC T.
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = (now.astimezone(UTC) + timedelta(hours=72)).replace(
        minute=0, second=0, microsecond=0,
    )
    if end < now.astimezone(UTC) + timedelta(hours=72):
        end += timedelta(hours=1)
    query = urlencode({
        "latitude": latitude, "longitude": longitude,
        "hourly": ",".join(WEATHER_FIELDS),
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
        "timezone": "GMT", "timeformat": "unixtime",
        "temperature_unit": "celsius", "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    })
    try:
        with urlopen("https://api.open-meteo.com/v1/forecast?" + query, timeout=TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Open-Meteo trả HTTP {exc.code}; sẽ thử lại.") from None
    except (OSError, URLError, ValueError):
        raise RuntimeError("Không lấy được dự báo Open-Meteo; sẽ thử lại.") from None
    try:
        hourly = result["hourly"]
        times = hourly["time"]
        expected = list(range(int(start.timestamp()), int(end.timestamp()) + 1, 3600))
        if times != expected:
            raise ValueError
        for field in WEATHER_FIELDS:
            values = hourly[field]
            if len(values) != len(times) or any(
                not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in values
            ):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Open-Meteo thiếu dữ liệu dự báo 72 giờ; sẽ thử lại.") from None
    return hourly


def format_weather_forecast(hourly, now, latitude, longitude):
    start = now.astimezone(UTC)
    end = start + timedelta(hours=72)
    local = lambda value: value.astimezone(VIETNAM).strftime("%d/%m %H:%M")
    lines = [
        f"DỰ BÁO THỜI TIẾT 72 GIỜ ({latitude}, {longitude})",
        f"{local(start)} → {local(end)} (UTC+7)",
    ]
    for day in range(3):
        lower = (start + timedelta(days=day)).timestamp()
        upper = lower + 86400
        indices = [i for i, stamp in enumerate(hourly["time"]) if lower <= stamp < upper]
        values = lambda field: [hourly[field][i] for i in indices]
        # Cắt phần giao với từng khoảng 24h; phân bổ đều phần lẻ trong giờ.
        weights = [max(0, min(stamp, upper) - max(stamp - 3600, lower)) / 3600
                   for stamp in hourly["time"]]
        rain = sum((r + s) * w for r, s, w in zip(hourly["rain"], hourly["showers"], weights))
        sunshine = sum(s * w for s, w in zip(hourly["sunshine_duration"], weights)) / 3600
        codes = Counter(values("weather_code"))
        conditions = "; ".join(WEATHER_CODES.get(code, f"Mã thời tiết {code}")
                               for code, _ in codes.most_common(2))
        rain_types = [WEATHER_CODES[code] for code in sorted(codes) if code in RAIN_CODES]
        rain_text = "; ".join(rain_types) if rain_types else (
            "Có mưa" if rain > 0 else "Chưa dự báo có mưa"
        )
        daytime_clouds = [hourly["cloud_cover"][i] for i in indices if hourly["is_day"][i] == 1]
        cloud_text = (f"mây ban ngày TB {sum(daytime_clouds) / len(daytime_clouds):.0f}%"
                      if daytime_clouds else "không có giờ ban ngày")
        temperatures = values("temperature_2m")
        winds = values("wind_speed_10m")
        lines.extend([
            "",
            f"{local(datetime.fromtimestamp(lower, UTC))} → {local(datetime.fromtimestamp(upper, UTC))}",
            f"• {conditions}",
            f"• Nhiệt độ: {min(temperatures):.1f}–{max(temperatures):.1f} °C",
            f"• {rain_text}; lượng mưa ~{rain:.1f} mm; xác suất cao nhất {max(values('precipitation_probability')):.0f}%",
            f"• Gió: {min(winds):.1f}–{max(winds):.1f} km/h",
            f"• Nắng ~{sunshine:.1f} giờ; {cloud_text}",
        ])
    lines.append("\nNguồn: Open-Meteo (https://open-meteo.com/). Dữ liệu theo giờ, phần lẻ được ước tính.")
    return "\n".join(lines)


def notify_weather_if_due(token, chat_id, notify_times, latitude, longitude,
                          last_sent=None, now=None):
    """Trả về mốc gửi mới nhất để vòng lặp giữ trong bộ nhớ, không lưu file."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    # Cho phép lệch tối đa 5 phút do vòng lặp/network; không gửi bù lịch cũ.
    slots = [datetime.combine((now - timedelta(days=day)).date(), at, tzinfo=UTC)
             for day in (0, 1) for at in notify_times]
    due = [slot for slot in slots if 0 <= (now - slot).total_seconds() < 300]
    if not due:
        return last_sent
    slot = max(due)
    if last_sent is not None and last_sent >= slot:
        return last_sent
    hourly = get_weather_forecast(latitude, longitude, now)
    send_telegram_message(token, chat_id, format_weather_forecast(hourly, now, latitude, longitude))
    LOG.info("Đã gửi dự báo thời tiết cho lịch %s UTC.", slot.strftime("%Y-%m-%d %H:%M"))
    # Chỉ cập nhật bộ nhớ khi Telegram xác nhận thành công, để lỗi API còn được thử lại.
    return slot


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "-5477430973").strip()
    if not token or not chat_id:
        raise SystemExit("Cần đặt TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID.")
    try:
        interval = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
        if interval < 1:
            raise ValueError
    except ValueError:
        raise SystemExit("CHECK_INTERVAL_SECONDS phải là số nguyên dương.") from None
    try:
        notify_times = parse_notify_times(os.environ.get("TELEGRAM_NOTIFY_TIMES_UTC", "09:00"))
        latitude = float(os.environ.get("WEATHER_LATITUDE", "21.028333"))
        longitude = float(os.environ.get("WEATHER_LONGITUDE", "105.853333"))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("WEATHER_LATITUDE/WEATHER_LONGITUDE nằm ngoài phạm vi tọa độ.")
    except ValueError as exc:
        raise SystemExit(f"Cấu hình thời tiết không hợp lệ: {exc}") from None

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Linux: ngăn hai tiến trình cùng gửi thông báo cho một lần đổi IP.
    with STATE_FILE.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Bot đã chạy trong tiến trình khác.") from None
        LOG.info("Theo dõi IPv4 public mỗi %s giây. Ctrl+C để dừng.", interval)
        LOG.info("Lịch thời tiết UTC: %s (giờ Việt Nam = UTC + 7).",
                 ",".join(at.strftime("%H:%M") for at in notify_times))
        next_ip_check = datetime.now(UTC)
        last_weather_sent = None
        while not stop.is_set():
            if datetime.now(UTC) >= next_ip_check:
                try:
                    check_once(token, chat_id)
                except (RuntimeError, OSError, ValueError) as exc:
                    LOG.error("%s", exc)
                next_ip_check = datetime.now(UTC) + timedelta(seconds=interval)
            try:
                last_weather_sent = notify_weather_if_due(
                    token, chat_id, notify_times, latitude, longitude,
                    last_sent=last_weather_sent,
                )
            except (RuntimeError, OSError, ValueError) as exc:
                LOG.error("%s", exc)
            # Lịch thời tiết vẫn được kiểm tra mỗi tối đa 30s dù interval IP lớn.
            delay = max(0, (next_ip_check - datetime.now(UTC)).total_seconds())
            stop.wait(min(30, delay))


if __name__ == "__main__":
    main()
