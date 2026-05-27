import os
import asyncio
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from openai import OpenAI
from ddgs import DDGS

# ==================== HEALTH CHECK SERVER CHO RENDER ====================
health_app = Flask(__name__)

@health_app.route("/")
def health():
    return "Bot is running! 🚀", 200

@health_app.route("/health")
def health_check():
    return "OK", 200

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    health_app.run(host="0.0.0.0", port=port)

# ==================== CẤU HÌNH BOT ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("❌ Chưa tìm thấy BOT_TOKEN!")
if not OPENROUTER_API_KEY:
    raise ValueError("❌ Chưa tìm thấy OPENROUTER_API_KEY!")

# Xóa webhook cũ để tránh conflict
try:
    delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
    response = requests.get(delete_url)
    print("✅ Đã xóa webhook cũ:", response.json())
except Exception as e:
    print(f"⚠️ Không thể xóa webhook: {e}")

# Khởi tạo client cho OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    default_headers={
        "HTTP-Referer": "https://your-bot.onrender.com",  # Thay bằng URL Render của bạn
        "X-Title": "Telegram Bot",
    }
)

# ==================== CẤU HÌNH MODEL ====================
# Các model free đang hoạt động tốt (thử từng cái nếu cái đầu không được)
MODEL = "google/gemini-2.0-flash-exp:free"  # Khuyến nghị - ổn định nhất
# MODEL = "meta-llama/llama-3.2-3b-instruct:free"
# MODEL = "microsoft/phi-3.5-mini-128k-instruct:free"
# MODEL = "mistralai/mistral-7b-instruct:free"
# MODEL = "deepseek/deepseek-r1:free"

print(f"🧠 Đang sử dụng model: {MODEL}")

SYSTEM_PROMPT = (
    "Bạn là một người bạn đồng hành AI thông minh, thân thiện và hài hước. "
    "Bạn có tên là Gà đây!. "
    "Hãy trả lời các câu hỏi bằng tiếng Việt một cách tự nhiên, sử dụng giọng điệu gần gũi, thoải mái, "
    "đôi khi có thể dùng một chút biểu tượng cảm xúc (emoji) để cuộc trò chuyện thêm sinh động. "
    "Mục tiêu của bạn là giúp đỡ và mang lại niềm vui cho người dùng. "
    "Nhớ ngữ cảnh của cuộc trò chuyện để trả lời mạch lạc. "
    "QUAN TRỌNG: Luôn trả lời CỰC NGẮN GỌN, súc tích, đi thẳng vào vấn đề. Không dài dòng, không giải thích thừa."
)

MAX_HISTORY = 20
chat_histories: dict[int, list[dict]] = {}

# ==================== CÁC HÀM XỬ LÝ ====================

def ai_needs_search(query: str) -> bool:
    """Kiểm tra câu hỏi có cần tìm kiếm web không"""
    try:
        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-exp:free",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn là bộ phân loại câu hỏi. "
                        "Nhiệm vụ: xác định câu hỏi có cần thông tin thời gian thực không "
                        "(tin tức, thời tiết, giá cả, sự kiện hiện tại, vị trí hiện tại, kết quả mới nhất...). "
                        "Chỉ trả lời đúng 1 từ: YES hoặc NO."
                    )
                },
                {"role": "user", "content": query}
            ],
            temperature=0,
            max_tokens=5,
        )
        answer = response.choices[0].message.content.strip().upper()
        return "YES" in answer
    except Exception as e:
        print(f"⚠️ Lỗi phân loại tìm kiếm: {e}")
        return False

def web_search(query: str, max_results: int = 5) -> str:
    """Tìm kiếm web bằng DuckDuckGo"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "Không tìm thấy kết quả nào."
        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            output.append(f"{i}. {title}\n{body}\nNguồn: {href}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Lỗi tìm kiếm: {e}"

def get_history(user_id: int) -> list[dict]:
    if user_id not in chat_histories:
        chat_histories[user_id] = []
    return chat_histories[user_id]

def add_to_history(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY * 2:
        chat_histories[user_id] = history[-(MAX_HISTORY * 2):]

def call_openrouter(user_id: int, user_message: str) -> tuple[str, bool]:
    """Gọi OpenRouter API để lấy phản hồi"""
    history = get_history(user_id)
    searched = False
    system_content = SYSTEM_PROMPT

    if ai_needs_search(user_message):
        print(f"🔍 Tìm kiếm web: {user_message}")
        search_results = web_search(user_message)
        searched = True
        system_content = SYSTEM_PROMPT + f"\n\nDưới đây là kết quả tìm kiếm web mới nhất:\n\n{search_results}"

    messages = [{"role": "system", "content": system_content}] + history + [
        {"role": "user", "content": user_message}
    ]

    try:
        print(f"📤 Gửi request đến OpenRouter với model: {MODEL}")
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )
        reply = response.choices[0].message.content
        print(f"📥 Nhận phản hồi thành công, độ dài: {len(reply)} ký tự")
        return reply, searched
    except Exception as e:
        print(f"❌ LỖI CHI TIẾT TỪ OPENROUTER: {type(e).__name__} - {e}")
        if hasattr(e, 'response') and e.response:
            print(f"📄 Response nội dung: {e.response.text}")
        return f"Xin lỗi, tôi đang gặp lỗi kỹ thuật. Vui lòng thử lại sau!", False

# ==================== CÁC HÀNH ĐỘNG TELEGRAM ====================

async def start(update: Update, context):
    user_id = update.effective_user.id
    chat_histories.pop(user_id, None)
    await update.message.reply_text(
        "🤖 Chào bạn! Mình là bot AI thông minh!\n\n"
        "✨ Mình có thể:\n"
        "• Trả lời mọi câu hỏi\n"
        "• Tìm kiếm thông tin thời gian thực 🌐\n"
        "• Nhớ ngữ cảnh cuộc trò chuyện 🧠\n\n"
        "📝 Dùng /clear để xóa lịch sử hội thoại.\n"
        "🆘 Dùng /help để xem hướng dẫn."
    )

async def help_command(update: Update, context):
    await update.message.reply_text(
        "📖 Hướng dẫn sử dụng:\n\n"
        "• /start - Bắt đầu hội thoại mới\n"
        "• /clear - Xóa lịch sử hội thoại\n"
        "• /help - Xem hướng dẫn này\n\n"
        "💡 Bạn có thể hỏi tôi bất cứ điều gì!"
    )

async def clear_command(update: Update, context):
    user_id = update.effective_user.id
    chat_histories.pop(user_id, None)
    await update.message.reply_text("🗑️ Đã xóa lịch sử! Bắt đầu lại nhé 😊")

async def handle_message(update: Update, context):
    user_message = update.message.text
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    chat_type = update.message.chat.type

    # Xử lý trong group: chỉ phản hồi khi tag bot
    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username
        if f"@{bot_username}" not in user_message:
            return
        user_message = user_message.replace(f"@{bot_username}", "").strip()
        if not user_message:
            return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply, searched = await asyncio.to_thread(call_openrouter, user_id, user_message)
        
        if not reply or "lỗi" in reply.lower() and "xin lỗi" in reply.lower():
            await update.message.reply_text(reply)
        else:
            if searched:
                reply = "🌐 " + reply
            add_to_history(user_id, "user", user_message)
            add_to_history(user_id, "assistant", reply)
            await update.message.reply_text(reply)

    except Exception as e:
        print(f"❌ Lỗi trong handle_message: {e}")
        await update.message.reply_text("⚠️ Có lỗi xảy ra, vui lòng thử lại sau giây lát!")

# ==================== MAIN ====================

def main():
    print("=" * 50)
    print("🤖 Đang khởi động bot Telegram...")
    print(f"📌 Bot Token: {'✅' if BOT_TOKEN else '❌'}")
    print(f"🔑 OpenRouter API Key: {'✅' if OPENROUTER_API_KEY else '❌'}")
    print(f"🧠 Model: {MODEL}")
    print("=" * 50)

    # Chạy health check server trên thread riêng
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    print("💓 Health check server đang chạy")

    # Tạo và chạy bot
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Bot đang chạy polling...")
    print("=" * 50)
    # Chạy polling (blocking)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()