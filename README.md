# Bot thông báo đổi IP

Python 3.9+ trên Linux, không cần cài thư viện. Theo dõi **IPv4 public** của mạng đang chạy bot, không phải IP LAN.

Token đã xuất hiện trong hội thoại: thu hồi qua BotFather (`/revoke`) và lấy token mới.

Chạy tại thư mục dự án:

```bash
read -rsp 'Nhập token Telegram mới: ' TELEGRAM_BOT_TOKEN
echo
export TELEGRAM_BOT_TOKEN
export TELEGRAM_CHAT_ID='-5477430973'
export CHECK_INTERVAL_SECONDS=60
python3 bot_main.py
```

Lần đầu chỉ lưu IP vào `last_ip.txt`, không gửi tin. Mỗi lần kiểm tra sau, chỉ gửi nếu IP khác giá trị đã lưu. File này nằm cạnh script, giúp giữ mốc so sánh khi khởi động lại. Không xóa file nếu muốn giữ mốc. Dừng bằng Ctrl+C.

Nếu lấy IP hoặc gửi Telegram thất bại, giữ mốc cũ và thử lại ở vòng sau. Trường hợp Telegram đã nhận tin nhưng kết nối mất trước khi trả lời, hoặc ghi file thất bại sau khi gửi, lần thử lại có thể gửi trùng. Các thay đổi xảy ra hoàn toàn giữa hai lần kiểm tra sẽ không được phát hiện.

Chạy nền sau khi đã export các biến trên:

```bash
nohup python3 bot_main.py > bot.log 2>&1 &
echo $! > /tmp/telegram-ip-bot.pid
```

`nohup` giữ bot chạy khi đóng terminal, nhưng không tự chạy lại sau reboot. Không chạy nhiều bản bot cùng lúc; script khóa file để ngăn trùng tiến trình trong cùng thư mục.

Bot dùng [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage). Bot phải còn ở trong nhóm và có quyền gửi tin.
