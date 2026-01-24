import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定與資料庫初始化 ==========
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
    """強制同步雲端資料並過濾無效行"""
    global local_cache
    try:
        raw_data = sheet.get_all_records()
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 同步成功: 目前有 {len(local_cache)} 筆遊戲商資料")
    except Exception as e: print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    """精確比對邏輯"""
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋核心邏輯 (支援群組模糊比對) ==========

async def perform_search(update: Update, keyword: str):
    """執行搜尋並處理結果回傳"""
    kw = keyword.strip().lower()
    # 模糊比對：搜尋名稱是否包含關鍵字
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    if not res:
        # 找不到時顯示現有名單，幫助除錯
        names = [str(r.get("supplier", "")) for r in local_cache]
        await update.message.reply_text(
            f"❌ 找不到「{keyword}」\n💡 目前名單：{', '.join(names) if names else '資料庫目前是空的'}"
        )
        return

    if len(res) > 1:
        # 多筆結果顯示按鈕
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        # 單筆結果直接發圖
        r = res[0]
        try:
            await update.message.reply_photo(
                photo=r["image_url"], 
                caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}"
            )
        except Exception as e:
            await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(🖼️ 圖片載入失敗: {e})")

# ========== 3. 指令與回傳處理 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主選單"""
    kbd = [
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
        [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]
    ]
    await update.message.reply_text("🎮 **遊戲商管理系統**\n\n🔹 **私訊**：直接打名字搜尋\n🔹 **群組**：直接打名字(需關閉隱私模式)或 @機器人名字 搜尋", reply_markup=InlineKeyboardMarkup(kbd))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理按鈕點擊事件"""
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_add': 
        user_state[uid] = {"mode": "add"}
        await query.message.reply_text("📸 請上傳圖片檔案 (或輸入 /cancel)")
    elif data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 已更新快取！目前共 {len(local_cache)} 筆。")
    elif data.startswith('v_'):
        # 點擊按鈕後顯示該筆資料
        await perform_search(update, data[2:])

# ========== 4. 訊息整合處理器 (支援群組文字過濾) ==========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理所有文字與照片訊息"""
    uid = update.effective_chat.id
    msg = update.message
    if not msg: return

    # A. 處理照片上傳 (新增流程)
    if msg.photo:
        if uid not in user_state: return
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        user_state[uid]["path"] = path
        await msg.reply_text("✍️ 圖片已收悉，請輸入「遊戲商名稱」：")
        return

    # B. 處理純文字
    if msg.text:
        raw_text = msg.text.strip()
        if raw_text.startswith('/'): return # 忽略斜線指令

        # 處理群組標註，過濾掉 @機器人 名稱
        bot_info = await context.bot.get_me()
        search_text = raw_text.replace(f"@{bot_info.username}", "").strip()

        # 1. 搜尋模式 (當不在新增流程時)
        if uid not in user_state:
            if search_text: await perform_search(update, search_text)
            return

        # 2. 新增流程模式
        st = user_state[uid]
        if st.get("mode") == "add":
            if "name" not in st:
                if find_in_cache(search_text)[0]: return await msg.reply_text("⚠️ 此名稱已存在，請重新輸入。")
                st["name"] = search_text
                await msg.reply_text(f"📝 好的，請輸入【{search_text}】的備註內容：")
            else:
                await msg.reply_text("⏳ 正在同步至 Cloudinary 與 Google Sheet...")
                try:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res.get("secure_url"), search_text])
                    refresh_cache()
                    user_state.pop(uid)
                    await msg.reply_text(f"✅ 【{st['name']}】新增成功！")
                except Exception as e: await msg.reply_text(f"❌ 發生錯誤: {e}")

# ========== 5. 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # 使用單一處理器解決過濾器優先權衝突問題
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🚀 終極旗艦版已啟動 (支援群組搜尋)")
    app.run_polling()
