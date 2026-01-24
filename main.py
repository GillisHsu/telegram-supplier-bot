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
    """重新抓取資料並同步到本地記憶體"""
    global local_cache
    try:
        local_cache = sheet.get_all_records()
        print(f"✨ 緩存同步成功，共 {len(local_cache)} 筆資料")
    except Exception as e:
        print(f"❌ 緩存更新失敗: {e}")

def find_in_cache(name):
    """在緩存中精確尋找"""
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
    """分頁搜尋功能"""
    if not context.args:
        await update.message.reply_text("🔎 請輸入關鍵字，例如： `/supplier 遊戲`", parse_mode='Markdown')
        return
    
    keyword = " ".join(context.args).lower()
    results = [r for r in local_cache if keyword in str(r.get("supplier", "")).lower()]
    
    if not results:
        await update.message.reply_text("❌ 找不到符合條件的遊戲商。")
        return

    if len(results) > 1:
        buttons = [[InlineKeyboardButton(f"▶️ {r['supplier']}", callback_data=f"view_{r['supplier']}")] for r in results]
        await update.message.reply_text(f"找到 {len(results)} 筆結果，請選擇查看：", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        r = results[0]
        await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

# 修復此處的括號錯誤
async def delete_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """徹底刪除遊戲商紀錄與圖片"""
    if not context.args:
        await update.message.reply_text("🗑️ 請輸入完整名稱，例如： `/delete TestName`", parse_mode='Markdown')
        return
    
    name = " ".join(context.args).strip()
    row_idx, _ = find_in_cache(name)
    
    if not row_idx:
        await update.message.reply_text(f"❌ 找不到遊戲商：{name}")
        return

    await update.message.reply_text(f"⏳ 正在徹底刪除【{name}】所有資料...")
    try:
        cloudinary.uploader.destroy(f"supplier_bot/{name}", invalidate=True)
        sheet.delete_rows(row_idx)
        refresh_cache()
        await update.message.reply_text(f"✅ 【{name}】及其雲端圖檔已完全移除。")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 刪除過程出錯：{e}")

# ========== 5. 事件回傳處理 ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = query.message.chat_id
    await query.answer()

    if data.startswith("menu_"):
        action = data.replace("menu_", "")
        if action == "add":
            user_state[chat_id] = {"mode": "add"}
            await query.message.reply_text("📸 請上傳圖片 (或輸入 /cancel 取消)")
        elif action == "search":
            await query.message.reply_text("🔎 請輸入 `/supplier 關鍵字`", parse_mode='Markdown')
        elif action == "delete":
            await query.message.reply_text("🗑️ 請輸入 `/delete 名稱`", parse_mode='Markdown')
        else:
            await query.message.reply_text(f"💡 請直接輸入對應指令進行操作。")

    elif data.startswith("view_"):
        target_name = data.replace("view_", "")
        _, r = find_in_cache(target_name)
        if r:
            await query.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

# ========== 6. 照片與文字處理邏輯 ==========

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_
