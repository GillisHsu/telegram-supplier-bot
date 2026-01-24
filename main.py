import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定與初始化 ==========
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
    """同步雲端資料至本地"""
    global local_cache
    try:
        raw_data = sheet.get_all_records()
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 資料同步成功，共 {len(local_cache)} 筆")
    except Exception as e: print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n: return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋核心邏輯 ==========

async def perform_search(update: Update, keyword: str):
    kw = keyword.strip().lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    if not res:
        names = [str(r.get("supplier", "")) for r in local_cache]
        await update.message.reply_text(f"❌ 找不到「{keyword}」\n💡 目前名單：{', '.join(names)}")
        return

    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        try:
            await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}")
        except Exception:
            await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(⚠️ 圖片載入失敗)")

# ========== 3. 指令處理器 (包含說明書與管理指令) ==========

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start 顯示簡易選單"""
    kbd = [[InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
           [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]]
    await update.message.reply_text("🎮 **遊戲商管理系統**\n輸入 /help 查看完整使用說明書。", reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help 顯示截圖中的詳細說明書"""
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "你可以點擊選單按鈕操作，或是直接輸入以下指令：\n\n"
        "📌 **通用指令**\n"
        "/start - 開啟主選單按鈕\n"
        "/help - 顯示此說明清單\n"
        "/cancel - 終止目前的動作\n\n"
        "🔎 **資料查詢**\n"
        "/supplier [關鍵字] - 快速搜尋遊戲商\n"
        "💡 提示：直接輸入名字也可以搜尋喔！\n\n"
        "🛠️ **進階管理**\n"
        "/delete [名稱] - 刪除該筆資料與圖檔\n"
        "/editname [舊] [新] - 修改遊戲商名稱\n"
        "/editinfo [名稱] [備註] - 更新資訊內容\n"
        "/editphoto [名稱] - 啟動照片更換流程"
    )
    await update.message.reply_text(help_text)

async def supplier_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理 /supplier 指令搜尋"""
    if not context.args:
        return await update.message.reply_text("請輸入搜尋關鍵字，例如：`/supplier Alize`", parse_mode='Markdown')
    await perform_search(update, " ".join(context.args))

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消操作。")

# ========== 4. 訊息整合處理 ==========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    msg = update.message
    if not msg: return

    # 照片處理
    if msg.photo:
        if uid not in user_state: return
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        user_state[uid]["path"] = path
        await msg.reply_text("✍️ 圖片收悉，請輸入名稱：")
        return

    # 文字處理
    if msg.text:
        raw_text = msg.text.strip()
        if raw_text.startswith('/'): return

        bot_info = await context.bot.get_me()
        search_text = raw_text.replace(f"@{bot_info.username}", "").strip()

        if uid not in user_state:
            if search_text: await perform_search(update, search_text)
            return

        st = user_state[uid]
        if st.get("mode") == "add":
            if "name" not in st:
                if find_in_cache(search_text)[0]: return await msg.reply_text("⚠️ 此名稱已存在")
                st["name"] = search_text
                await msg.reply_text(f"📝 請輸入【{search_text}】的備註：")
            else:
                await msg.reply_text("⏳ 同步中...")
                res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                sheet.append_row([st["name"], res.get("secure_url"), search_text])
                refresh_cache()
                user_state.pop(uid)
                await msg.reply_text(f"✅ 【{st['name']}】新增成功！")

# ========== 5. 按鈕處理 ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 快取已同步！共 {len(local_cache)} 筆。")
    elif data == 'm_add':
        user_state[query.message.chat_id] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送圖片 (或輸入 /cancel)")
    elif data.startswith('v_'):
        await perform_search(update, data[2:])

# ========== 6. 啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("supplier", supplier_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🚀 完整整合版啟動中...")
    app.run_polling()
