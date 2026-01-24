import os
import json
import gspread
import cloudinary
import cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定區塊 ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

cloudinary.config(
    cloud_name = os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key = os.environ["CLOUDINARY_API_KEY"],
    api_secret = os.environ["CLOUDINARY_API_SECRET"],
    secure = True
)

# Google Sheet 初始化
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_KEY_JSON), scope)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

# 全域暫存
user_state = {}
local_cache = []

# ========== 2. 工具函數 ==========

def refresh_cache():
    """重新抓取資料並同步到本地記憶體 (優化搜尋速度)"""
    global local_cache
    try:
        local_cache = sheet.get_all_records()
        print(f"✨ 緩存同步成功，共 {len(local_cache)} 筆資料")
    except Exception as e:
        print(f"❌ 緩存更新失敗: {e}")

def find_in_cache(name):
    """在緩存中精確尋找 (回傳行數與資料)"""
    # Google Sheet 的 records 從 0 開始，對應試算表行數需 +2 (1是標題)
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip() == name.strip():
            return i, row
    return None, None

# 啟動時預載
refresh_cache()

# ========== 3. 主選單與導覽 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """喚起主選單按鈕"""
    keyboard = [
        [InlineKeyboardButton("➕ 新增", callback_data='menu_add'), InlineKeyboardButton("🔍 搜尋", callback_data='menu_search')],
        [InlineKeyboardButton("✏️ 改名", callback_data='menu_edit_name'), InlineKeyboardButton("📝 改備註", callback_data='menu_edit_info')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='menu_edit_photo'), InlineKeyboardButton("🗑️ 刪除", callback_data='menu_delete')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🎮 **專業遊戲商管理系統**\n請選擇操作項目：\n(輸入 /cancel 可隨時終止當前流程)"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """強制取消當前所有進度"""
    chat_id = update.effective_chat.id
    if chat_id in user_state:
        del user_state[chat_id]
        await update.message.reply_text("🚫 操作已取消，狀態已重置。")
    else:
        await update.message.reply_text("目前沒有正在進行中的操作。")

# ========== 4. 核心功能函數 ==========

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """分頁搜尋：若多筆結果則顯示按鈕，若一筆則直接顯圖"""
    if not context.args:
        await update.message.reply_text("🔎 請輸入關鍵字，例如： `/supplier 遊戲`", parse_mode='Markdown')
        return
    
    keyword = " ".join(context.args).lower()
    results = [r for r in local_cache if keyword in str(r.get("supplier", "")).lower()]
    
    if not results:
        await update.message.reply_text("❌ 找不到符合條件的遊戲商。")
        return

    if len(results) > 1:
        # 多筆結果轉為分頁按鈕
        buttons = [[InlineKeyboardButton(f"▶️ {r['supplier']}", callback_data=f"view_{r['supplier']}")] for r in results]
        await update.message.reply_text(f"找到 {len(results)} 筆結果，請選擇查看：", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        r = results[0]
        await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

async def delete_supplier(update: Update
