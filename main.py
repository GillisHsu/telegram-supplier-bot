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
        # 抓取所有行並過濾空白行
        raw_data = sheet.get_all_records()
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 緩存同步成功: {len(local_cache)} 筆")
    except Exception as e: print(f"❌ 緩存失敗: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋邏輯 (強化容錯) ==========

async def perform_search(update: Update, keyword: str):
    kw = keyword.strip().lower()
    # 模糊比對
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    if not res:
        names = [str(r.get("supplier", "")) for r in local_cache]
        await update.message.reply_text(
            f"❌ 找不到「{keyword}」\n💡 目前資料庫名單：\n{', '.join(names) if names else '無資料'}"
        )
        return

    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到多個結果，請選擇：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        try:
            # 確保圖片 URL 存在才發圖，否則發文字
            if r.get("image_url"):
                await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 {r['supplier']}\n📝 {r['info'] or '無備註'}")
            else:
                await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info'] or '無備註'}\n(⚠️ 此項目無圖片)")
        except Exception as e:
            await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info'] or '無備註'}\n(🖼️ 圖片載入失敗: {e})")

# ========== 3. 指令與處理邏輯 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [[InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
           [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]]
    await update.message.reply_text("🎮 **管理系統已就緒**\n\n🔹 直接輸入名稱進行搜尋\n🔹 點擊按鈕執行管理功能", reply_markup=InlineKeyboardMarkup(kbd))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_add': 
        user_state[uid] = {"mode": "add"}
        await query.message.reply_text("📸 請上傳圖片")
    elif data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 資料已同步！目前共有 {len(local_cache)} 筆資料。")
    elif data.startswith('v_'):
        _, r = find_in_cache(data[2:])
        if r: await perform_search(update, data[2:])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理所有文字與照片的統一入口"""
    uid = update.effective_chat.id
    
    # 處理照片
    if update.message.photo:
        if uid not in user_state: return
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(update.message.photo[-1].file_id)).download_to_drive(path)
        user_state[uid]["path"] = path
        await update.message.reply_text("✍️ 請輸入名稱：")
        return

    # 處理文字
    if update.message.text:
        txt = update.message.text.strip()
        if txt.startswith('/'): return # 忽略指令

        if uid not in user_state:
            await perform_search(update, txt)
            return

        st = user_state[uid]
        if st.get("mode") == "add":
            if "name" not in st:
                if find_in_cache(txt)[0]: return await update.message.reply_text("⚠️ 名稱已存在")
                st["name"] = txt
                await update.message.reply_text(f"📝 請輸入【{txt}】的備註：")
            else:
                await update.message.reply_text("⏳ 存檔中...")
                res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                sheet.append_row([st["name"], res.get("secure_url"), txt])
                refresh_cache()
                user_state.pop(uid)
                await update.message.reply_text(f"✅ 【{st['name']}】新增成功！")

# ========== 4. 啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    # 統一使用一個 MessageHandler 處理文字與照片，避免過濾器衝突
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    app.run_polling()
