import os, json, gspread, cloudinary, cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 初始化環境 ==========
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
        print(f"✨ 緩存同步成功：{len(local_cache)} 筆")
    except Exception as e: print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n: return i, row
    return None, None

refresh_cache()

# ========== 2. 鍵盤配置 (維持兩階層) ==========

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), 
         InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')],
        [InlineKeyboardButton("🗑️ 刪除", callback_data='m_del_hint'), 
         InlineKeyboardButton("🛠️ 進階管理", callback_data='m_admin_menu')]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 更換名稱", callback_data='m_en_hint'), 
         InlineKeyboardButton("🖼️ 更換圖片", callback_data='m_ep_hint')],
        [InlineKeyboardButton("✍️ 更換備註", callback_data='m_ei_hint'), 
         InlineKeyboardButton("🚫 刪除遊戲商", callback_data='m_del_hint')],
        [InlineKeyboardButton("⬅️ 返回主選單", callback_data='m_main_menu')]
    ])

# ========== 3. 指令處理器 (通用與管理功能) ==========

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "你可以點擊選單按鈕操作，或是直接輸入以下指令：\n\n"
        "📌 **通用指令**\n"
        "/start - 開啟主選單按鈕\n"
        "/help - 顯示此說明清單\n"
        "/cancel - 終止目前的動作\n\n"
        "🛠️ **快速操作指令**\n"
        "/add - 啟動新增遊戲商流程\n"
        "/refresh - 手動強制同步試算表\n\n"
        "🔎 **資料查詢**\n"
        "/supplier [關鍵字] - 快速搜尋遊戲商(有支援模糊搜尋)\n\n"
        "⚙️ **進階管理**\n"
        "/delete [名稱] - 刪除該筆資料與圖檔\n"
        "/editname - 修改替換名稱 (需換行)\n"
        "/editinfo - 修改替換備註 (需換行)\n"
        "/editphoto [名稱] - 啟動換圖流程"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(help_text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    else:
        await update.message.reply_text(help_text, reply_markup=get_main_keyboard(), parse_mode='Markdown')

# 💡 [整合] 更換名稱 (換行分隔 + Cloudinary 同步)
async def editname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.replace('/editname', '', 1).strip()
    parts = raw_text.split('\n')
    
    if len(parts) < 2:
        return await update.message.reply_text("⚠️ 格式：/editname 舊名 (換行) 新名")

    old_name, new_name = parts[0].strip(), parts[1].strip()
    idx, _ = find_in_cache(old_name)

    if idx:
        # 1. 更新試算表名稱 (第一欄)
        sheet.update_cell(idx, 1, new_name)
        
        # 2. 同步更新 Cloudinary
        cloud_status = "並同步更新圖檔標籤"
        try:
            # 💡 關鍵修正：確保 Public ID 包含資料夾路徑，且不帶副檔名
            old_public_id = f"supplier_bot/{old_name}"
            new_public_id = f"supplier_bot/{new_name}"
            
            # 執行重命名 (使用 overwrite=True 確保強制覆蓋)
            cloudinary.uploader.rename(old_public_id, new_public_id, overwrite=True)
            
            # 💡 重新產生的網址必須符合 Cloudinary 規則
            new_url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload/{new_public_id}"
            sheet.update_cell(idx, 2, new_url)
            
        except Exception as e:
            cloud_status = f"但圖片同步失敗 (原因: {e})"
            print(f"❌ Cloudinary Rename Error: {e}")
        
        # 3. 務必重新載入本機快取，否則搜尋時還是會抓到舊資料
        refresh_cache()
        await update.message.reply_text(f"✅ 名稱已從【{old_name}】修改為【{new_name}】\n{cloud_status}")
    else:
        await update.message.reply_text(f"❌ 找不到名稱為「{old_name}」的對象")
        

# 💡 [整合] 更換備註 (換行分隔 + 預先查詢)
async def editinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.replace('/editinfo', '', 1).strip()
    parts = raw_text.split('\n')
    name = parts[0].strip()

    if not name:
        return await update.message.reply_text("用法: /editinfo [名稱]")

    idx, row = find_in_cache(name)
    if not idx:
        return await update.message.reply_text(f"❌ 找不到「{name}」")

    current_info = row.get("info", "目前無備註")

    # 如果只有給名字，則秀出目前的備註方便複製
    if len(parts) < 2:
        return await update.message.reply_text(
            f"🔎 **【{name}】目前的備註如下：**\n\n"
            f"`{current_info}`\n\n"
            f"👆 **點擊上方文字可自動複製**，修改後再使用換行格式傳送：\n"
            f"`/editinfo {name}`\n"
            f"`新的備註內容`",
            parse_mode='Markdown'
        )

    new_info = parts[1].strip()
    sheet.update_cell(idx, 3, new_info)
    refresh_cache()
    await update.message.reply_text(f"✅ 【{name}】備註已更新！")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args)
    if not name: return await update.message.reply_text("用法: /delete [名稱]")
    idx, _ = find_in_cache(name)
    if idx:
        sheet.delete_rows(idx)
        try: cloudinary.uploader.destroy(f"supplier_bot/{name}")
        except: pass
        refresh_cache()
        await update.message.reply_text(f"🗑️ 已刪除 {name} 及其雲端圖檔")
    else: await update.message.reply_text("❌ 找不到該對象")

# ========== 4. 搜尋與訊息處理 ==========

async def perform_search(update: Update, keyword: str):
    kw = keyword.strip().lower()
    res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
    msg = update.callback_query.message if update.callback_query else update.message
    if not res: return await msg.reply_text(f"❌ 找不到「{keyword}」")

    if len(res) > 1 and not update.callback_query:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await msg.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        try: await msg.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}")
        except: await msg.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(圖片載入失敗)")

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, msg = update.effective_chat.id, update.message
    if not msg: return
    if msg.photo and uid in user_state:
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        user_state[uid]["path"] = path
        if user_state[uid]["mode"] == "add": await msg.reply_text("✍️ 請輸入新廠商名稱：")
        elif user_state[uid]["mode"] == "edit_photo":
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
        if uid in user_state:
            st = user_state[uid]
            if st["mode"] == "add":
                if "name" not in st:
                    if find_in_cache(txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                    st["name"] = txt
                    await msg.reply_text(f"📝 請輸入【{txt}】的備註：")
                else:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res["secure_url"], txt])
                    refresh_cache()
                    user_state.pop(uid)
                    await msg.reply_text("✅ 新增成功！")
        else:
            await perform_search(update, txt)

# ========== 5. 按鈕回調 ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'm_admin_menu':
        await query.edit_message_text("🛠️ **進階管理模式**\n請選擇操作項目：", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
    elif data == 'm_main_menu':
        await query.edit_message_text("🎮 **遊戲商管理系統**\n請選擇操作項目：", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif data == 'm_ref':
        refresh_cache()
        await query.message.reply_text("✅ 已同步快取！")
    elif data == 'm_add':
        user_state[query.message.chat_id] = {"mode": "add"}
        await query.message.reply_text("📸 請傳送圖片")
    elif data == 'm_en_hint':
        await query.message.reply_text("📝 **修改名稱**\n請輸入格式：\n`/editname 舊名` (按換行)\n`新名`")
    elif data == 'm_ep_hint':
        await query.message.reply_text("🖼️ **更換圖片**\n請輸入：`/editphoto [名稱]`")
    elif data == 'm_ei_hint':
        await query.message.reply_text("✍️ **修改備註**\n請輸入：`/editinfo [名稱]` 以查詢並修改")
    elif data == 'm_del_hint':
        await query.message.reply_text("🗑️ **刪除遊戲商**\n請輸入：`/delete [名稱]`")
    elif data.startswith('v_'):
        await perform_search(update, data[2:])

# ========== 6. 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", help_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", lambda u, c: (user_state.pop(u.effective_chat.id, None), u.message.reply_text("🚫 已取消"))))
    app.add_handler(CommandHandler("add", lambda u, c: (user_state.update({u.effective_chat.id: {"mode": "add"}}), u.message.reply_text("📸 請傳送圖片"))))
    app.add_handler(CommandHandler("refresh", lambda u, c: (refresh_cache(), u.message.reply_text("✅ 同步完成"))))
    app.add_handler(CommandHandler("supplier", lambda u, c: perform_search(u, " ".join(c.args)) if c.args else u.message.reply_text("請輸入關鍵字")))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("editname", editname_cmd))
    app.add_handler(CommandHandler("editinfo", editinfo_cmd))
    app.add_handler(CommandHandler("editphoto", lambda u, c: (user_state.update({u.effective_chat.id: {"mode": "edit_photo", "name": " ".join(c.args), "idx": find_in_cache(" ".join(c.args))[0]}}), u.message.reply_text("📸 請傳圖")) if c.args else u.message.reply_text("用法: /editphoto [名稱]")))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_all))
    
    print("🚀 最終整合版啟動成功...")
    app.run_polling()


