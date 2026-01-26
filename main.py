import os, json, gspread, cloudinary, cloudinary.uploader
import cloudinary.api  
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

# ========== 2. 鍵盤配置 ==========

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 新增", callback_data='m_add'), 
         InlineKeyboardButton("🛠️ 進階管理", callback_data='m_admin_menu')],
        [InlineKeyboardButton("🚫 終止目前流程", callback_data='m_cancel'), 
         InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 更換名稱", callback_data='m_en_hint'), 
         InlineKeyboardButton("🖼️ 更換圖片", callback_data='m_ep_hint')],
        [InlineKeyboardButton("✍️ 更換備註", callback_data='m_ei_hint'), 
         InlineKeyboardButton("🗑️ 刪除遊戲商", callback_data='m_del_hint')],
        [InlineKeyboardButton("⬅️ 返回主選單", callback_data='m_main_menu')]
    ])

# ========== 3. 指令處理函式 (全指令修復) ==========

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "你可以點擊選單按鈕操作，或是輸入指令操作。\n\n"
        "📌 **通用指令**\n"
        "/start - 開啟主選單\n"
        "/help - 顯示此說明\n"
        "/cancel - 終止目前流程\n"
        "/refresh - 同步雲端資料\n\n"
        "🛠️ **快速操作指令**\n"
        "/add [名稱] - 啟動新增遊戲商流程\n"
        "/supplier [關鍵字] - 快速搜尋遊戲商(有支援模糊搜尋)\n\n"
        "⚙️ **進階管理**\n"
        "/delete [名稱] - 刪除該筆資料與圖檔\n"
        "/editname [名稱] - 修改替換名稱\n"
        "/editinfo [名稱] - 修改替換備註\n"
        "/editphoto [名稱] - 啟動換圖流程"
    )
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(help_text, reply_markup=get_main_keyboard(), parse_mode='Markdown')

# 指令跳轉引導邏輯
async def supplier_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw = " ".join(context.args).strip()
    if not kw: return await update.message.reply_text("用法: /supplier [關鍵字]")
    # 這裡會直接調用 handle_all 內的搜尋邏輯 (稍後在 handle_all 處理)
    await handle_search(update, kw)

async def editname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    uid = update.effective_chat.id
    if name:
        idx, _ = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "en_step2", "old_name": name}
            await update.message.reply_text(f"🔍 找到【{name}】\n請輸入「新名稱」：")
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "en_step1"}
        await update.message.reply_text("📝 **修改名稱**\n請輸入「舊名稱」：")

async def editinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    uid = update.effective_chat.id
    if name:
        idx, row = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "ei_step2", "name": name, "idx": idx}
            await update.message.reply_text(f"🔎 **【{name}】目前的備註：**\n`{row.get('info', '無')}`\n\n👆 **請直接輸入新備註內容送出即可：**", parse_mode='Markdown')
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "ei_step1"}
        await update.message.reply_text("✍️ **修改備註**\n請輸入想要修改的「遊戲商名稱」：")

async def editphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    uid = update.effective_chat.id
    if name:
        idx, _ = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "edit_photo_process", "name": name}
            await update.message.reply_text(f"📸 找到【{name}】，請直接傳送「新圖片」：")
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "ep_process"}
        await update.message.reply_text("🖼️ **更換圖片**\n請輸入遊戲商名稱：")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    if name:
        idx, _ = find_in_cache(name)
        if idx:
            sheet.delete_rows(idx)
            try: cloudinary.uploader.destroy(f"supplier_bot/{name}")
            except: pass
            refresh_cache()
            await update.message.reply_text(f"🗑️ 已刪除 {name}")
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[update.effective_chat.id] = {"mode": "del_process"}
        await update.message.reply_text("🗑️ **刪除流程**\n請輸入要刪除的名稱：")

# ========== 4. 搜尋與訊息處理 ==========

async def handle_search(update, kw):
    res = [r for r in local_cache if kw.lower() in str(r.get("supplier", "")).strip().lower()]
    if not res: return await update.message.reply_text(f"❌ 找不到「{kw}」")
    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        try: await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}")
        except: await update.message.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}")

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, msg = update.effective_chat.id, update.message
    if not msg: return
    
    if msg.photo and uid in user_state:
        st = user_state[uid]
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        if st["mode"] == "add":
            user_state[uid]["path"] = path
            await msg.reply_text("✍️ 請輸入新遊戲商名稱：")
        elif st["mode"] == "edit_photo_process":
            cloudinary.uploader.upload(path, folder="supplier_bot", public_id=st["name"], overwrite=True)
            user_state.pop(uid); await msg.reply_text(f"✅ 【{st['name']}】圖片更新完成！")
        return

    if msg.text:
        txt = msg.text.strip()
        if txt.startswith('/'): return
        
        if uid in user_state:
            st = user_state[uid]
            # [新增流程]
            if st["mode"] == "add":
                if "name" not in st:
                    if find_in_cache(txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                    user_state[uid]["name"] = txt
                    await msg.reply_text(f"📝 請輸入【{txt}】的備註：")
                else:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res["secure_url"], txt])
                    refresh_cache(); user_state.pop(uid); await msg.reply_text("✅ 新增成功！")
            
            # [修改名稱流程]
            elif st["mode"] == "en_step1":
                idx, _ = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "en_step2", "old_name": txt}
                    await msg.reply_text(f"🔍 找到【{txt}】\n請輸入「新名稱」：")
                else: await msg.reply_text("❌ 找不到名稱，請重新輸入：")
            elif st["mode"] == "en_step2":
                old_name = st["old_name"]
                idx, _ = find_in_cache(old_name)
                sheet.update_cell(idx, 1, txt)
                try:
                    cloudinary.uploader.rename(f"supplier_bot/{old_name}", f"supplier_bot/{txt}", overwrite=True)
                    cloudinary.api.update(f"supplier_bot/{txt}", display_name=txt)
                    new_url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload/supplier_bot/{txt}"
                    sheet.update_cell(idx, 2, new_url)
                except: pass
                refresh_cache(); user_state.pop(uid); await msg.reply_text(f"✅ 修改成功！")

            # [修改備註流程]
            elif st["mode"] == "ei_step1":
                idx, row = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "ei_step2", "name": txt, "idx": idx}
                    await msg.reply_text(f"🔎 **【{txt}】目前的備註：**\n`{row.get('info', '無')}`\n\n👆 **請直接輸入新備註送出：**", parse_mode='Markdown')
                else: await msg.reply_text("❌ 找不到名稱，請重新輸入：")
            elif st["mode"] == "ei_step2":
                sheet.update_cell(st["idx"], 3, txt)
                refresh_cache(); user_state.pop(uid); await msg.reply_text(f"✅ 【{st['name']}】備註已更新！")

            # [刪除與換圖引導]
            elif st["mode"] == "del_process":
                idx, _ = find_in_cache(txt)
                if idx:
                    sheet.delete_rows(idx); cloudinary.uploader.destroy(f"supplier_bot/{txt}")
                    refresh_cache(); user_state.pop(uid); await msg.reply_text(f"🗑️ 已刪除 {txt}")
                else: await msg.reply_text("❌ 找不到名稱")
            elif st["mode"] == "ep_process":
                idx, _ = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "edit_photo_process", "name": txt}
                    await msg.reply_text(f"📸 找到【{txt}】，請傳送圖片：")
                else: await msg.reply_text("❌ 找不到名稱")
        else:
            await handle_search(update, txt)

# ========== 5. 按鈕回調處理 (修復進階管理按鈕) ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_cancel':
        user_state.pop(uid, None); await query.message.reply_text("🚫 已終止目前流程。")
    elif data == 'm_admin_menu':
        await query.edit_message_text("🛠️ **進階管理模式**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
    elif data == 'm_main_menu':
        await query.message.delete(); await help_cmd(update, context)
    elif data == 'm_add':
        user_state[uid] = {"mode": "add"}; await query.message.reply_text("📸 請傳送遊戲商圖片：")
    elif data == 'm_en_hint':
        user_state[uid] = {"mode": "en_step1"}; await query.message.reply_text("📝 **修改名稱**\n請輸入「舊名稱」：")
    elif data == 'm_ei_hint':
        user_state[uid] = {"mode": "ei_step1"}; await query.message.reply_text("✍️ **修改備註**\n請輸入「遊戲商名稱」：")
    elif data == 'm_ep_hint':
        user_state[uid] = {"mode": "ep_process"}; await query.message.reply_text("🖼️ **更換圖片**\n請輸入名稱：")
    elif data == 'm_del_hint':
        user_state[uid] = {"mode": "del_process"}; await query.message.reply_text("🗑️ **刪除流程**\n請輸入名稱：")
    elif data == 'm_ref':
        refresh_cache(); await query.message.reply_text("✅ 已刷新！")
    elif data.startswith('v_'):
        _, row = find_in_cache(data[2:])
        if row: await query.message.reply_photo(photo=row["image_url"], caption=f"🎮 {row['supplier']}\n📝 {row['info']}")

# ========== 6. 啟動區塊 (嚴謹順序) ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    # 1. 指令處理器 (優先)
    app.add_handler(CommandHandler("start", help_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", lambda u, c: (user_state.pop(u.effective_chat.id, None), u.message.reply_text("🚫 已終止"))))
    app.add_handler(CommandHandler("refresh", lambda u, c: (refresh_cache(), u.message.reply_text("✅ 已刷新"))))
    app.add_handler(CommandHandler("add", lambda u, c: (user_state.update({u.effective_chat.id: {"mode": "add"}}), u.message.reply_text("📸 請傳送圖片"))))
    app.add_handler(CommandHandler("supplier", supplier_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("editname", editname_cmd))
    app.add_handler(CommandHandler("editinfo", editinfo_cmd))
    app.add_handler(CommandHandler("editphoto", editphoto_cmd))
    
    # 2. 回調處理器
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # 3. 通用訊息處理器 (最後)
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_all))
    
    print("🚀 整合版啟動成功，所有指令與按鈕已恢復運作。")
    app.run_polling()
