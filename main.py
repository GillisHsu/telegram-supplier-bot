import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 環境設定與資料庫初始化 ==========
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
    """強制同步 Google Sheet 資料至本地緩存"""
    global local_cache
    try:
        raw_data = sheet.get_all_records()
        # 過濾掉 supplier 欄位為空的行
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 同步成功: 目前有 {len(local_cache)} 筆資料")
    except Exception as e: 
        print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    """精確比對邏輯，用於檢查重複或單筆精準提取"""
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

# 啟動時預先加載
refresh_cache()

# ========== 2. 搜尋核心邏輯 (支援模糊比對) ==========

async def perform_search(update: Update, keyword: str):
    """執行搜尋並處理回傳結果"""
    kw = keyword.strip().lower()
    # 模糊搜尋：找出名稱包含關鍵字的所有結果
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    if not res:
        names = [str(r.get("supplier", "")) for r in local_cache]
        await update.message.reply_text(
            f"❌ 找不到包含「{keyword}」的資料。\n"
            f"💡 目前名單：{', '.join(names) if names else '資料庫暫無內容'}"
        )
        return

    if len(res) > 1:
        # 多筆結果提供按鈕選擇
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        # 單筆結果直接發圖與資訊
        r = res[0]
        try:
            await update.message.reply_photo(
                photo=r["image_url"], 
                caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}"
            )
        except Exception as e:
            await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(🖼️ 圖片載入失敗: {e})")

# ========== 3. 指令處理器 (包含 /help) ==========

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """顯示詳細指令說明書"""
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "您可以點擊下方按鈕，或直接輸入指令與文字：\n\n"
        "🔎 **資料查詢**\n"
        "• 直接輸入「遊戲商名字」即可自動搜尋\n"
        "• 在群組中可標註機器人搜尋，例如：`@機器人 Alize`\n\n"
        "🛠️ **管理指令**\n"
        "• `/start` 或 `/help` - 開啟主功能選單\n"
        "• `/cancel` - 終止目前的動作\n\n"
        "💡 **小撇步**\n"
        "若手動修改了試算表，請按「🔄 刷新資料」確保同步。"
    )
    kbd = [
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]
    ]
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(kbd), parse_mode='Markdown')

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消當前進行中的流程"""
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已取消目前操作。")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理 Inline 按鈕點擊"""
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_add': 
        user_state[uid] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送遊戲商圖片 (或輸入 /cancel)")
    elif data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 快取已同步！目前共有 {len(local_cache)} 筆資料。")
    elif data.startswith('v_'):
        # 點擊多筆結果列表中的特定項目
        await perform_search(update, data[2:])

# ========== 4. 訊息整合處理 (支援群組環境) ==========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """統合處理所有文字與照片訊息"""
    uid = update.effective_chat.id
    msg = update.message
    if not msg: return

    # A. 處理照片上傳 (僅在新增模式下有效)
    if msg.photo:
        if uid not in user_state: return
        path = f"/tmp/{uid}.jpg"
        file = await context.bot.get_file(msg.photo[-1].file_id)
        await file.download_to_drive(path)
        user_state[uid]["path"] = path
        await msg.reply_text("✍️ 圖片收悉，請輸入「遊戲商名稱」：")
        return

    # B. 處理純文字
    if msg.text:
        raw_text = msg.text.strip()
        if raw_text.startswith('/'): return # 忽略斜線指令

        # 群組相容性處理：過濾掉提及機器人的字串 (@bot_username)
        bot_info = await context.bot.get_me()
        search_text = raw_text.replace(f"@{bot_info.username}", "").strip()

        # 1. 自動搜尋模式 (使用者當前不處於新增/編輯流程時)
        if uid not in user_state:
            if search_text: await perform_search(update, search_text)
            return

        # 2. 資料新增/編輯流程
        st = user_state[uid]
        if st.get("mode") == "add":
            if "name" not in st:
                # 第一步：紀錄名稱
                if find_in_cache(search_text)[0]: 
                    return await msg.reply_text("⚠️ 此名稱已存在，請重新輸入或輸入 /cancel。")
                st["name"] = search_text
                await msg.reply_text(f"📝 請輸入【{search_text}】的備註內容：")
            else:
                # 第二步：紀錄備註並上傳存檔
                await msg.reply_text("⏳ 同步至雲端中，請稍候...")
                try:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res.get("secure_url"), search_text])
                    refresh_cache() # 完工後立即刷新快取
                    if os.path.exists(st["path"]): os.remove(st["path"]) # 清理暫存檔
                    user_state.pop(uid)
                    await msg.reply_text(f"✅ 【{st['name']}】已成功新增！")
                except Exception as e: 
                    await msg.reply_text(f"❌ 存檔出錯: {e}")

# ========== 5. 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    # 指令類
    app.add_handler(CommandHandler("start", show_help))
    app.add_handler(CommandHandler("help", show_help))
    app.add_handler(CommandHandler("cancel", cancel_action))
    
    # 按鈕與訊息處理
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🚀 最終旗艦整合版啟動成功 (含 /help 指令與群組搜尋支援)")
    app.run_polling()
