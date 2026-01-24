import os
import json
import gspread
import cloudinary
import cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# 初始化 Google Sheet
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_KEY_JSON), scope)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

# 全域變數
user_state = {}
local_cache = [] # 本地資料緩存

# ========== 2. 工具函數 ==========

def refresh_cache():
    """重新抓取 Sheet 資料到本地記憶體"""
    global local_cache
    local_cache = sheet.get_all_records()
    print("✨ 本地緩存已更新")

def find_in_cache(name):
    """在緩存中精確尋找"""
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip() == name.strip():
            return i, row
    return None, None

# 啟動時先刷一次緩存
refresh_cache()

# ========== 3. 指令選單 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ 新增", callback_data='menu_add'), InlineKeyboardButton("🔍 搜尋", callback_data='menu_search')],
        [InlineKeyboardButton("✏️ 改名", callback_data='menu_edit_name'), InlineKeyboardButton("📝 改備註", callback_data='menu_edit_info')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='menu_edit_photo'), InlineKeyboardButton("🗑️ 刪除", callback_data='menu_delete')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🎮 **專業遊戲商管理系統**\n輸入 /cancel 可隨時終止操作。"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消目前所有操作"""
    chat_id = update.effective_chat.id
    if chat_id in user_state:
        del user_state[chat_id]
        await update.message.reply_text("🚫 已取消目前操作。")
    else:
        await update.message.reply_text("目前沒有正在進行的操作。")

# ========== 4. 核心邏輯 (新增/搜尋/刪除) ==========

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """分頁顯示搜尋結果"""
    if not context.args:
        await update.message.reply_text("🔎 請輸入關鍵字： `/supplier ABC`", parse_mode='Markdown')
        return
    
    keyword = " ".join(context.args).lower()
    results = [r for r in local_cache if keyword in str(r.get("supplier", "")).lower()]
    
    if not results:
        await update.message.reply_text("❌ 找不到符合條件的遊戲商")
        return

    if len(results) > 1:
        # 如果有多筆結果，顯示按鈕清單 (分頁邏輯)
        buttons = [[InlineKeyboardButton(r["supplier"], callback_data=f"view_{r['supplier']}")] for r in results]
        await update.message.reply_text(f"找到 {len(results)} 筆結果，請選擇：", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        # 只有一筆直接顯示
        r = results[0]
        await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

async def delete_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """刪除功能"""
    if not context.args:
        await update.message.reply_text("🗑️ 請輸入名稱： `/delete 名稱`", parse_mode='Markdown')
        return
    
    name = context.args[0]
    row_idx, _ = find_in_cache(name)
    
    if row_idx:
        await update.message.reply_text(f"⏳ 正在徹底刪除【{name}】...")
        try:
            # 1. 刪除 Cloudinary 圖片
            cloudinary.uploader.destroy(f"supplier_bot/{name}", invalidate=True)
            # 2. 刪除 Sheet 紀錄
            sheet.delete_rows(row_idx)
            refresh_cache()
            await update.message.reply_text(f"✅ 已成功刪除【{name}】及其雲端檔案。")
        except Exception as e:
            await update.message.reply_text(f"⚠️ 刪除過程出錯：{str(e)}")
    else:
        await update.message.reply_text(f"❌ 找不到該遊戲商。")

# ========== 5. 訊息與按鈕回傳處理 ==========

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # ... 其他按鈕提示與之前相同

    elif data.startswith("view_"):
        # 分頁點擊後顯示圖片
        target_name = data.replace("view_", "")
        _, r = find_in_cache(target_name)
        await query.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_state: return
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    path = f"/tmp/{chat_id}.jpg"
    await file.download_to_drive(path)
    
    state = user_state[chat_id]
    if state["mode"] == "add":
        state["image_path"] = path
        await update.message.reply_text("✍️ 請輸入遊戲商名稱")
    elif state["mode"] == "edit_photo":
        # (略，邏輯同前，但最後記得 call refresh_cache)
        pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_state or user_state[chat_id]["mode"] != "add": return
    state = user_state[chat_id]
    
    if "supplier" not in state:
        name = update.message.text.strip()
        # 重複名稱檢查
        idx, _ = find_in_cache(name)
        if idx:
            await update.message.reply_text(f"⚠️ 名稱【{name}】已存在，請重新輸入新名稱，或輸入 /cancel 取消。")
            return
        state["supplier"] = name
        await update.message.reply_text("📝 請輸入遊戲商備註資訊")
        return

    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 正在存檔...")
        try:
            res = cloudinary.uploader.upload(state["image_path"], folder="supplier_bot", public_id=state["supplier"])
            sheet.append_row([state["supplier"], res.get("secure_url"), state["info"]])
            refresh_cache() # 更新緩存
            await update.message.reply_text(f"✅ 【{state['supplier']}】新增成功！")
        except Exception as e:
            await update.message.reply_text(f"❌ 失敗：{str(e)}")
        if os.path.exists(state["image_path"]): os.remove(state["image_path"])
        del user_state[chat_id]

# ========== 6. 主啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(CommandHandler("delete", delete_supplier))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
