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
    """強制從雲端重新抓取資料"""
    global local_cache
    try:
        local_cache = sheet.get_all_records()
        print(f"✨ 緩存同步成功: {len(local_cache)} 筆")
    except Exception as e: print(f"❌ 緩存失敗: {e}")

def find_in_cache(name):
    """精確比對：用於新增檢查或單筆查詢"""
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋邏輯 (強化版) ==========

async def perform_search(update: Update, keyword: str):
    """支援模糊比對與去空格的搜尋邏輯"""
    kw = keyword.strip().lower()
    
    # 比對時同時將雙方都 strip() 與 lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    if not res:
        # 搜尋不到時，列出目前快取內的名字供除錯
        names = [str(r.get("supplier", "")) for r in local_cache]
        await update.message.reply_text(
            f"❌ 找不到包含「{keyword}」的資料。\n"
            f"💡 目前資料庫內有：\n{', '.join(names) if names else '空無一物'}\n\n"
            "若剛才在試算表手動改過，請點擊「刷新資料」。"
        )
        return

    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        await update.message.reply_photo(
            photo=r["image_url"], 
            caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info']}"
        )

# ========== 3. 指令與按鈕處理 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]
    ]
    msg = "🎮 **管理系統已就緒**\n\n🔹 直接輸入名稱進行搜尋\n🔹 點擊按鈕執行管理功能\n\n輸入 /help 查看更多指令。"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消目前操作。")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_add': 
        user_state[uid] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送遊戲商圖片 (或 /cancel)")
    elif data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 資料已同步！目前共有 {len(local_cache)} 筆資料。")
    elif data.startswith('v_'):
        _, r = find_in_cache(data[2:])
        if r: await query.message.reply_photo(photo=r["image_url"], caption=f"🎮 {r['supplier']}\n📝 {r['info']}")

# ========== 4. 訊息接收邏輯 ==========

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if uid not in user_state: return
    path = f"/tmp/{uid}.jpg"
    await (await context.bot.get_file(update.message.photo[-1].file_id)).download_to_drive(path)
    user_state[uid]["path"] = path
    await update.message.reply_text("✍️ 圖片已收悉，請輸入「名稱」：")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    txt = update.message.text.strip()
    
    # 閒置模式 -> 自動搜尋
    if uid not in user_state:
        await perform_search(update, txt)
        return

    # 流程模式 -> 新增資料
    st = user_state[uid]
    if st.get("mode") == "add":
        if "name" not in st:
            if find_in_cache(txt)[0]: return await update.message.reply_text("⚠️ 名稱已存在，請換一個。")
            st["name"] = txt
            await update.message.reply_text(f"📝 好的，請輸入【{txt}】的備註內容：")
        else:
            await update.message.reply_text("⏳ 正在處理中...")
            try:
                res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                sheet.append_row([st["name"], res.get("secure_url"), txt])
                refresh_cache()
                if os.path.exists(st["path"]): os.remove(st["path"])
                user_state.pop(uid)
                await update.message.reply_text(f"✅ 【{st['name']}】新增成功！")
            except Exception as e: await update.message.reply_text(f"❌ 錯誤: {e}")

# ========== 5. 啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # 文字處理放在最後，捕捉所有非指令文字
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🚀 旗艦穩定版啟動成功...")
    app.run_polling()
