import os
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from openai import OpenAI

health_app = Flask(__name__)

@health_app.route("/")
def health():
    return "Bot đang chạy! 🐔", 200

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    health_app.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("❌ Chưa tìm thấy BOT_TOKEN!")
if not OPENROUTER_API_KEY:
    raise ValueError("❌ Chưa tìm thấy OPENROUTER_API_KEY!")

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

MODEL = "deepseek/deepseek-chat:online"

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


def get_history(user_id: int) -> list[dict]:
    if user_id not in chat_histories:
        chat_histories[user_id] = []
    return chat_histories[user_id]


def add_to_history(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY * 2:
        chat_histories[user_id] = history[-(MAX_HISTORY * 2):]


def call_ai(user_id: int, user_message: str) -> str:
    history = get_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": user_message}
    ]

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=1024,
    )
    return response.choices[0].message.content


async def start(update: Update, context):
    user_id = update.effective_user.id
    chat_histories.pop(user_id, None)
    await update.message.reply_text(
        "🤖 Chào bạn! Mình là bot AI thông minh!\n\n"
        "✨ Mình có thể:\n"
        "• Trả lời mọi câu hỏi\n"
        "• Tìm kiếm thông tin thời gian thực 🌐\n"
        "• Nhớ ngữ cảnh cuộc trò chuyện 🧠\n"
        "• Hỗ trợ học tập, công việc\n\n"
        "Cứ nhắn gì cũng được! 😊\n"
        "Dùng /clear để xóa lịch sử hội thoại."
    )


async def help_command(update: Update, context):
    await update.message.reply_text(
        "📖 Hướng dẫn sử dụng:\n\n"
        "• Nhắn tin bình thường → bot trả lời\n"
        "• Hỏi tin tức, thời tiết, giá cả → bot tự tìm kiếm web 🌐\n"
        "• Bot nhớ ngữ cảnh hội thoại 🧠\n"
        "• /clear — xóa lịch sử hội thoại\n"
        "• /start — bắt đầu lại từ đầu\n"
        "• /help — xem hướng dẫn này"
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

    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username
        is_tagged = f"@{bot_username}" in user_message
        is_reply_to_bot = (
            update.message.reply_to_message is not None
            and update.message.reply_to_message.from_user is not None
            and update.message.reply_to_message.from_user.username == bot_username
        )
        if not is_tagged and not is_reply_to_bot:
            return
        user_message = user_message.replace(f"@{bot_username}", "").strip()
        if not user_message:
            return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = await asyncio.to_thread(call_ai, user_id, user_message)
        if not reply:
            reply = "😅 Mình chưa hiểu lắm, bạn có thể hỏi lại không?"

        add_to_history(user_id, "user", user_message)
        add_to_history(user_id, "assistant", reply)
        await update.message.reply_text(reply)

    except Exception as e:
        print(f"❌ Lỗi: {e}")
        await update.message.reply_text("⚠️ Có lỗi xảy ra, vui lòng thử lại sau giây lát!")


def main():
    print("🤖 Đang khởi động bot...")
    print("📌 Bot token: ✅")
    print("🔑 OpenRouter API key: ✅")
    print(f"🧠 Model: {MODEL}")

    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    print("💓 Health check server: ✅")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Bot đang chạy...")
    app.run_polling()


if __name__ == "__main__":
    main()
