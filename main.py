import os, json, asyncio, gspread, cloudinary, cloudinary.uploader
import cloudinary.api
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler
)

# ========== 1. 初始化 ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
    secure=True
)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(GOOGLE_KEY_JSON), scope
)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

user_state = {}
local_cache = []

# ========== 2. 快取同步 ==========
def refresh_cache():
    global local_cache
    try:
        raw = sheet.get_all_records()
        local_cache = [r for r in raw if str(r.get("supplier", "")).strip()]
        print(f"[CACHE] synced {len(local_cache)} rows")
    except Exception as e:
        print(f"[CACHE] sync failed: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

# ========== 3. 鍵盤 ==========
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'),
         InlineKeyboardButton("🛠️ 進階管理", callback_data='m_admin_menu')],
        [InlineKeyboardButton("🚫 終止流程", callback_data='m_cancel'),
         InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 更換名稱", callback_data='m_en_hint'),
         InlineKeyboardButton("🖼️ 更換圖片", callback_data='m_ep_hint')],
        [InlineKeyboardButton("✍️ 更換備註", callback_data='m_ei_hint'),
         InlineKeyboardButton("🗑️ 刪除", callback_data='m_del_hint')],
        [InlineKeyboardButton("🚫 取消", callback_data='m_cancel'),
         InlineKeyboardButton("⬅️ 返回", callback_data='m_main_menu')]
    ])

# ========== 4. 指令 ==========
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await help_cmd(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>📖 使用說明</b>\n\n"
        "/add 新增\n"
        "/supplier 查詢\n"
        "/editname 修改名稱\n"
        "/editinfo 修改備註\n"
        "/editphoto 修改圖片\n"
        "/delete 刪除\n"
    )
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(text, reply_markup=get_main_keyboard(), parse_mode='HTML')

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消")

async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refresh_cache()
    await update.message.reply_text("✅ 已同步")

# 其餘 handler 邏輯保持你原本的（搜尋、上傳、修改、刪除）
# ⚠️ 這裡不重貼，因為你原本的 4~5 區塊可直接沿用

# ========== 5. 啟動 ==========
if __name__ == "__main__":
    refresh_cache()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))

    # 保留你原本所有 handler
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_all))

    # 每小時自動同步，避免 Render 重啟後資料不同步
    app.job_queue.run_repeating(lambda _: refresh_cache(), interval=3600, first=60)

    print("🚀 Render Worker Bot Started")
    app.run_polling()
