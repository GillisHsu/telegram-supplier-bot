import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定與初始化 (保持不變) ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
    secure=True
)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_KEY_JSON), scope)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

user_state, local_cache = {}, []

def refresh_cache():
    global local_cache
    try:
        local_cache = sheet.get_all_records()
        print(f"✨ 緩存更新成功: {len(local_cache)} 筆")
    except Exception as e: print(f"❌ 緩存失敗: {e}")

def find_in_cache(name):
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == name.strip().lower(): return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋核心邏輯 (抽取出來供自動搜尋調用) ==========

async def perform_search(update: Update, keyword: str):
    """執行搜尋邏輯"""
    kw = keyword.lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).lower()]
    
    if not res:
        await update.message.reply_text(f"❌ 找不到包含「{keyword}」的資料。")
        return

    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到多個相關結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_photo(photo=res[0]["image_url"], caption=f"🎮 {res[0]['supplier']}\n📝 {res[0]['info']}")

# ========== 3. 指令功能 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [[InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔍 搜尋", callback_data='m_src')],
           [InlineKeyboardButton("✏️ 改名", callback_data='m_en'), InlineKeyboardButton("📝 改備註", callback_data='m_ei')],
           [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]]
    msg = "🎮 **遊戲商管理系統**\n\n💡 **小技巧**：直接輸入名稱即可自動搜尋！"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "📖 **使用說明**\n\n直接在對話框輸入「遊戲商名稱」即可自動查詢。\n\n`/start` - 選單\n`/cancel` - 取消流程"
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消目前操作。")

# ========== 4. 訊息處理 (關鍵改動) ==========

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    txt = update.message.text.strip()
    
    # 如果使用者不在「新增/修改」模式中，則自動判定為「搜尋」
    if uid not in user_state:
        await perform_search(update, txt)
        return

    # 否則，執行原本的新增/修改流程
    st = user_state[uid]
    if st.get("mode") == "add":
        if "name" not in st:
            if find_in_cache(txt)[0]: return await update.message.reply_text("⚠️ 名稱已存在")
            st["name"] = txt
            await update.message.reply_text(f"📝 好的，請輸入【{txt}】的備註內容：")
        else:
            await update.message.reply_text("⏳ 雲端處理中...")
            res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
            sheet.append_row([st["name"], res.get("secure_url"), txt])
            refresh_cache()
            if os.path.exists(st["path"]): os.remove(st["path"])
            user_state.pop(uid)
            await update.message.reply_text(f"✅ 【{st['name']}】新增成功！")

# ========== 5. 其他回傳與啟動 (保持不變) ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    if data == 'm_add': 
        user_state[uid] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送圖片")
    elif data.startswith('v_'):
        _, r = find_in_cache(data[2:])
        await query.message.reply_photo(photo=r["image_url"], caption=f"🎮 {r['supplier']}\n📝 {r['info']}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if uid not in user_state: return
    path = f"/tmp/{uid}.jpg"
    await (await context.bot.get_file(update.message.photo[-1].file_id)).download_to_drive(path)
    user_state[uid]["path"] = path
    await update.message.reply_text("✍️ 圖片已收悉，請輸入名稱：")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", show_help))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(filters.TEXT & ~filters.COMMAND, handle_text) # 移除 CommandHandler，讓一般文字走 handle_text
    app.run_polling()
