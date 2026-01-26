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
        [InlineKeyboardButton("🗑️ 刪除", callback_data='m_del_hint'), 
         InlineKeyboardButton("🔄 刷新資料", callback_data='m_ref')]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 更換名稱", callback_data='m_en_hint'), 
         InlineKeyboardButton("🖼️ 更換圖片", callback_data='m_ep_hint')],
        [InlineKeyboardButton("✍️ 更換備註", callback_data='m_ei_hint'), 
         InlineKeyboardButton("🚫 刪除遊戲商", callback_data='m_del_hint')],
        [InlineKeyboardButton("⬅️ 返回主選單", callback_data='m_main_menu')]
    ])

# ========== 3. 核心功能函式 (處理實際邏輯) ==========

async def perform_editname(update, context, old_name, new_name):
    idx, _ = find_in_cache(old_name)
    if idx:
        sheet.update_cell(idx, 1, new_name)
        cloud_status = "並同步更新圖檔標籤與顯示名稱"
        try:
            old_public_id = f"supplier_bot/{old_name}"
            new_public_id = f"supplier_bot/{new_name}"
            cloudinary.uploader.rename(old_public_id, new_public_id, overwrite=True)
            cloudinary.api.update(new_public_id, display_name=new_name)
            new_url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload/{new_public_id}"
            sheet.update_cell(idx, 2, new_url)
        except Exception as e:
            cloud_status = f"但圖片同步失敗 ({e})"
        refresh_cache()
        await update.message.reply_text(f"✅ 名稱已從【{old_name}】修改為【{new_name}】\n{cloud_status}")
    else:
        await update.message.reply_text(f"❌ 找不到「{old_name}」，流程已終止。")

# ========== 4. 訊息處理 (handle_all) ==========

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, msg = update.effective_chat.id, update.message
    if not msg: return
    
    # 照片處理
    if msg.photo and uid in user_state:
        st = user_state[uid]
        path = f"/tmp/{uid}.jpg"
        await (await context.bot.get_file(msg.photo[-1].file_id)).download_to_drive(path)
        if st["mode"] == "add":
            user_state[uid]["path"] = path
            await msg.reply_text("✍️ 請輸入新廠商名稱：")
        elif st["mode"] == "edit_photo_process":
            name = st["name"]
            cloudinary.uploader.upload(path, folder="supplier_bot", public_id=name, overwrite=True)
            user_state.pop(uid)
            await msg.reply_text(f"✅ 【{name}】圖片更新完成！")
        return

    # 文字處理
    if msg.text:
        txt = msg.text.strip()
        if txt.startswith('/'): return
        
        if uid in user_state:
            st = user_state[uid]
            mode = st["mode"]

            # 1. 新增流程
            if mode == "add":
                if "name" not in st:
                    if find_in_cache(txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                    user_state[uid]["name"] = txt
                    await msg.reply_text(f"📝 請輸入【{txt}】的備註：")
                else:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res["secure_url"], txt])
                    refresh_cache(); user_state.pop(uid)
                    await msg.reply_text("✅ 新增成功！")

            # 2. 修改名稱流程 (分段引導)
            elif mode == "en_step1":
                idx, _ = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "en_step2", "old_name": txt}
                    await msg.reply_text(f"🔍 找到【{txt}】\n請輸入「新名稱」：")
                else:
                    await msg.reply_text(f"❌ 找不到「{txt}」，請重新輸入或輸入 /cancel 取消：")

            elif mode == "en_step2":
                old_name = st["old_name"]
                await perform_editname(update, context, old_name, txt)
                user_state.pop(uid)

            # 3. 修改備註流程 (先查再改)
            elif mode == "ei_step1":
                idx, row = find_in_cache(txt)
                if idx:
                    current_info = row.get("info", "無")
                    user_state[uid] = {"mode": "ei_step2", "name": txt, "idx": idx}
                    await msg.reply_text(
                        f"🔎 **【{txt}】目前的備註：**\n`{current_info}`\n\n"
                        f"👆 點擊上方文字可複製，**請直接輸入新的備註內容送出：**",
                        parse_mode='Markdown'
                    )
                else:
                    await msg.reply_text(f"❌ 找不到「{txt}」，請重新輸入或輸入 /cancel 取消：")

            elif mode == "ei_step2":
                name, idx = st["name"], st["idx"]
                sheet.update_cell(idx, 3, txt)
                refresh_cache(); user_state.pop(uid)
                await msg.reply_text(f"✅ 【{name}】備註已更新完成！")

            # 4. 刪除與換圖流程
            elif mode == "del_process":
                context.args = [txt]
                from __main__ import delete_cmd # 確保呼叫到下方的 delete_cmd
                await delete_cmd(update, context, custom_name=txt)
                user_state.pop(uid)
            
            elif mode == "ep_process":
                idx, _ = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "edit_photo_process", "name": txt, "idx": idx}
                    await msg.reply_text(f"📸 找到【{txt}】，請傳送新的圖片：")
                else:
                    await msg.reply_text(f"❌ 找不到「{txt}」，請重新輸入名稱：")
        else:
            # 一般搜尋模式
            kw = txt.lower()
            res = [r for r in local_cache if kw in str(r.get("supplier", "")).strip().lower()]
            if not res: return await msg.reply_text(f"❌ 找不到「{txt}」")
            if len(res) > 1:
                btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
                await msg.reply_text(f"🔍 找到 {len(res)} 筆相似結果：", reply_markup=InlineKeyboardMarkup(btns))
            else:
                r = res[0]
                try: await msg.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 備註：{r['info'] or '無'}")
                except: await msg.reply_text(f"🎮 {r['supplier']}\n📝 {r['info']}\n(圖片載入失敗)")

# ========== 5. 指令與按鈕回調 ==========

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **機器人使用說明書**\n\n"
        "你可以點擊選單按鈕操作，或是直接輸入名稱進行搜尋。\n\n"
        "📌 **通用指令**\n"
        "/start - 開啟主選單\n"
        "/help - 顯示此說明\n"
        "/cancel - 終止目前流程\n"
        "/refresh - 同步雲端資料"
    )
    msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(help_text, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    else:
        await msg.reply_text(help_text, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def delete_cmd(update, context, custom_name=None):
    name = custom_name if custom_name else " ".join(context.args)
    idx, _ = find_in_cache(name)
    if idx:
        sheet.delete_rows(idx)
        try: cloudinary.uploader.destroy(f"supplier_bot/{name}")
        except: pass
        refresh_cache()
        await update.message.reply_text(f"🗑️ 已刪除 {name} 及其雲端圖檔")
    else: await update.message.reply_text(f"❌ 找不到「{name}」")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_admin_menu':
        await query.edit_message_text("🛠️ **進階管理模式**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
    elif data == 'm_main_menu':
        await help_cmd(update, context)
    elif data == 'm_ref':
        refresh_cache(); await query.message.reply_text("✅ 已同步快取！")
    elif data == 'm_add':
        user_state[uid] = {"mode": "add"}; await query.message.reply_text("📸 請傳送圖片")
    elif data == 'm_en_hint':
        user_state[uid] = {"mode": "en_step1"}
        await query.message.reply_text("📝 **修改名稱**\n請輸入想要修改的「舊名稱」：")
    elif data == 'm_ei_hint':
        user_state[uid] = {"mode": "ei_step1"}
        await query.message.reply_text("✍️ **修改備註**\n請輸入想要查詢並修改備註的「遊戲商名稱」：")
    elif data == 'm_ep_hint':
        user_state[uid] = {"mode": "ep_process"}
        await query.message.reply_text("🖼️ **更換圖片**\n請輸入遊戲商名稱：")
    elif data == 'm_del_hint':
        user_state[uid] = {"mode": "del_process"}
        await query.message.reply_text("🗑️ **刪除流程**\n請輸入想要刪除的「遊戲商名稱」：")
    elif data.startswith('v_'):
        # 處理搜尋列表的點擊
        kw = data[2:]
        _, row = find_in_cache(kw)
        if row: await query.message.reply_photo(photo=row["image_url"], caption=f"🎮 遊戲商：{row['supplier']}\n📝 備註：{row['info'] or '無'}")

# ========== 6. 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", help_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", lambda u, c: (user_state.pop(u.effective_chat.id, None), u.message.reply_text("🚫 已取消目前流程"))))
    app.add_handler(CommandHandler("refresh", lambda u, c: (refresh_cache(), u.message.reply_text("✅ 同步完成"))))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_all))
    print("🚀 流程分段引導版啟動成功...")
    app.run_polling()
