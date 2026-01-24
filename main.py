import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定區塊 ==========
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
        if str(row.get("supplier", "")).strip() == name.strip(): return i, row
    return None, None

refresh_cache()

# ========== 2. 指令功能 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔍 搜尋", callback_data='m_src')],
        [InlineKeyboardButton("✏️ 改名", callback_data='m_en'), InlineKeyboardButton("📝 改備註", callback_data='m_ei')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]
    ]
    msg = "🎮 **遊戲商管理系統**\n輸入 /cancel 可取消流程。"
    if update.callback_query: await update.callback_query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')
    else: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消操作。")

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("🔎 請輸入關鍵字：/supplier ABC")
    kw = " ".join(context.args).lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).lower()]
    if not res: return await update.message.reply_text("❌ 找不到資料")
    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text("請選擇：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_photo(photo=res[0]["image_url"], caption=f"🎮 {res[0]['supplier']}\n📝 {res[0]['info']}")

async def delete_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("🗑️ 請輸入名稱：/delete ABC")
    name = context.args[0]
    idx, _ = find_in_cache(name)
    if idx:
        cloudinary.uploader.destroy(f"supplier_bot/{name}", invalidate=True)
        sheet.delete_rows(idx)
        refresh_cache()
        await update.message.reply_text(f"✅ 【{name}】已徹底刪除。")
    else: await update.message.reply_text("❌ 找不到遊戲商")

# ========== 3. 回傳處理 ==========

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
    await update.message.reply_text("✍️ 請輸入名稱")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if uid not in user_state: return
    st = user_state[uid]
    txt = update.message.text.strip()
    
    if "name" not in st:
        if find_in_cache(txt)[0]: return await update.message.reply_text("⚠️ 名稱已存在")
        st["name"] = txt
        await update.message.reply_text("📝 請輸入備註")
    else:
        await update.message.reply_text("⏳ 處理中...")
        res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
        sheet.append_row([st["name"], res.get("secure_url"), txt])
        refresh_cache()
        if os.path.exists(st["path"]): os.remove(st["path"])
        user_state.pop(uid)
        await update.message.reply_text(f"✅ 【{st['name']}】新增成功！")

# ========== 4. 啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(CommandHandler("delete", delete_supplier))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
