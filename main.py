import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 初始化與環境變數 ==========
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
        raw_data = sheet.get_all_records()
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 同步成功: 共 {len(local_cache)} 筆")
    except Exception as e: print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n: return i, row
    return None, None

refresh_cache()

# ========== 2. 搜尋核心 (含修復後的點擊處理) ==========

async def perform_search(update: Update, keyword: str):
    kw = keyword.strip().lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    
    # 判斷是來自按鈕回調還是文字訊息
    msg = update.callback_query.message if update.callback_query else update.message

    if not res:
        names = [str(r.get("supplier", "")) for r in local_cache]
        return await msg.reply_text(f"❌ 找不到「{keyword}」\n💡 目前名單：{', '.join(names)}")

    if len(res) > 1 and not update.callback_query:
        # 多筆結果顯示按鈕
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await msg.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        # 單筆或按鈕點擊結果
        r = res[0]
        try:
            await msg.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}")
        except:
            await msg.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(🖼️ 圖片載入失敗)")

# ========== 3. 指令處理器 (完整重現說明書功能) ==========

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "你可以點擊選單按鈕操作，或是直接輸入以下指令：\n\n"
        "📌 **通用指令**\n"
        "/start - 開啟主選單按鈕\n"
        "/help - 顯示此說明清單\n"
        "/cancel - 終止目前的動作\n\n"
        "🔎 **資料查詢**\n"
        "/supplier [關鍵字] - 快速搜尋遊戲商\n\n"
        "🛠️ **進階管理**\n"
        "/delete [名稱] - 刪除該筆資料與圖檔\n"
        "/editname [舊名] [新名] - 修改名稱\n"
        "/editinfo [名稱] [新備註] - 修改備註\n"
        "/editphoto [名稱] - 啟動換圖流程"
    )
    kbd = [[InlineKeyboardButton("➕ 新增", callback_data='m_add'), InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
           [InlineKeyboardButton("🖼️ 換圖", callback_data='m_ep'), InlineKeyboardButton("🗑️ 刪除", callback_data='m_del')]]
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(kbd))

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("用法: /delete [名稱]")
    name = " ".join(context.args)
    idx, _ = find_in_cache(name)
    if idx:
        sheet.delete_rows(idx)
        try: cloudinary.uploader.destroy(f"supplier_bot/{name}")
        except: pass
        refresh_cache()
        await update.message.reply_text(f"✅ 已刪除 {name}")
    else: await update.message.reply_text("❌ 找不到該對象")

async def editname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: return await update.message.reply_text("用法: /editname [舊名] [新名]")
    old, new = context.args[0], context.args[1]
    idx, _ = find_in_cache(old)
    if idx:
        sheet.update_cell(idx, 1, new)
        refresh_cache()
        await update.message.reply_text(f"✅ 名稱已從 {old} 改為 {new}")
    else: await update.message.reply_text("❌ 找不到該對象")

async def editinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: return await update.message.reply_text("用法: /editinfo [名稱] [新備註]")
    name, info = context.args[0], " ".join(context.args[1:])
    idx, _ = find_in_cache(name)
    if idx:
        sheet.update_cell(idx, 3, info)
        refresh_cache()
        await update.message.reply_text(f"✅ {name} 的備註已更新")
    else: await update.message.reply_text("❌ 找不到該對象")

async def editphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("用法: /editphoto [名稱]")
    name = " ".join(context.args)
    idx, _ = find_in_cache(name)
    if idx:
        user_state[update.effective_chat.id] = {"mode": "edit_photo", "name": name, "idx": idx}
        await update.message.reply_text(f"📸 請傳送【{name}】的新圖片")
    else: await update.message.reply_text("❌ 找不到該對象")

# ========== 4. 訊息整合處理 (文字/照片) ==========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, msg = update.effective_chat.id, update.message
    if not msg: return

    if msg.photo:
        if uid not in user_state: return
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        user_state[uid]["path"] = path
        
        if user_state[uid]["mode"] == "add":
            await msg.reply_text("✍️ 請輸入新廠商名稱：")
        elif user_state[uid]["mode"] == "edit_photo":
            await msg.reply_text("⏳ 正在更新圖片...")
            name = user_state[uid]["name"]
            res = cloudinary.uploader.upload(path, folder="supplier_bot", public_id=name, overwrite=True)
            sheet.update_cell(user_state[uid]["idx"], 2, res["secure_url"])
            refresh_cache()
            user_state.pop(uid)
            await msg.reply_text(f"✅ 【{name}】圖片更新完成！")
        return

    if msg.text:
        txt = msg.text.strip()
        if txt.startswith('/'): return
        
        bot_info = await context.bot.get_me()
        search_txt = txt.replace(f"@{bot_info.username}", "").strip()

        if uid not in user_state:
            if search_txt: await perform_search(update, search_txt)
            return

        st = user_state[uid]
        if st["mode"] == "add":
            if "name" not in st:
                if find_in_cache(search_txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                st["name"] = search_txt
                await msg.reply_text(f"📝 請輸入【{search_txt}】的備註：")
            else:
                await msg.reply_text("⏳ 存檔中...")
                res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                sheet.append_row([st["name"], res["secure_url"], txt])
                refresh_cache()
                user_state.pop(uid)
                await msg.reply_text(f"✅ 新增成功！")

# ========== 5. 按鈕回調 (修復點擊無反應) ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'm_ref':
        refresh_cache()
        await query.message.reply_text(f"✅ 已同步！共 {len(local_cache)} 筆")
    elif data == 'm_add':
        user_state[query.message.chat_id] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送圖片")
    elif data.startswith('v_'):
        # 關鍵修正：點擊搜尋結果按鈕時，將按鈕文字作為關鍵字重新搜尋
        await perform_search(update, data[2:])

# ========== 6. 啟動 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", help_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("supplier", lambda u, c: perform_search(u, " ".join(c.args)) if c.args else u.message.reply_text("請輸入關鍵字")))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("editname", editname_cmd))
    app.add_handler(CommandHandler("editinfo", editinfo_cmd))
    app.add_handler(CommandHandler("editphoto", editphoto_cmd))
    app.add_handler(CommandHandler("cancel", lambda u, c: (user_state.pop(u.effective_chat.id, None), u.message.reply_text("🚫 已取消"))))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🚀 最終完全體啟動成功...")
    app.run_polling()
