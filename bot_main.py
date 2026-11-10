"""Theo dõi IPv4 public và báo Telegram khi địa chỉ thay đổi (Python 3.9+)."""

import fcntl
import ipaddress
import json
import logging
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
    payload = urlencode({
        "chat_id": chat_id,
        "text": f"Địa chỉ IP public đã thay đổi\nIP cũ: {old_ip}\nIP mới: {new_ip}",
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
        while not stop.is_set():
            try:
                check_once(token, chat_id)
            except (RuntimeError, OSError, ValueError) as exc:
                LOG.error("%s", exc)
            stop.wait(interval)


if __name__ == "__main__":
    main()
