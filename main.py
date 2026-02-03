import asyncio
import nest_asyncio
nest_asyncio.apply()

import os, json, gspread, cloudinary, cloudinary.uploader
import cloudinary.api  
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# 🔧 新增（防 Render / Railway 休眠）
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from apscheduler.schedulers.background import BackgroundScheduler

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

# ========== 2. 快取同步 ==========
def refresh_cache():
    global local_cache
    try:
        raw_data = sheet.get_all_records()
        local_cache = [r for r in raw_data if str(r.get("supplier", "")).strip()]
        print(f"✨ 緩存同步成功：{len(local_cache)} 筆")
    except Exception as e:
        print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        if str(row.get("supplier", "")).strip().lower() == n:
            return i, row
    return None, None

refresh_cache()

# ========== 3. Render 健康檢查 ==========
def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # 已修正：這裡必須縮排在 Handler 裡面
            if self.path in ("/", "/health"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# ========== 4. 每日同步 ==========
def start_daily_refresh():
    scheduler = BackgroundScheduler(daemon=True, timezone="Asia/Taipei")
    scheduler.add_job(refresh_cache, "interval", hours=6)
    scheduler.start()
    print("⏰ 已啟動每日自動同步")

# ========== 5. 鍵盤 ==========
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
        [InlineKeyboardButton("🚫 終止目前流程", callback_data='m_cancel'),
         InlineKeyboardButton("⬅️ 返回主選單", callback_data='m_main_menu')]
    ])

# ========== 6. 指令定義區==========

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>📖 機器人使用說明書</b>\n\n"
        "你可以點擊選單按鈕操作，或是輸入指令操作。\n\n"
        "📌 <b>通用指令</b>\n"
        "/start - 開啟主選單\n"
        "/help - 顯示此說明\n"
        "/cancel - 終止目前流程\n"
        "/refresh - 同步雲端資料\n\n"
        "🛠️ <b>快速操作指令</b>\n"
        "/add [名稱] - 啟動新增遊戲商流程\n"
        "/supplier [關鍵字] - 快速搜尋遊戲商\n\n"
        "⚙️ <b>進階管理</b>\n"
        "/delete [名稱] - 刪除該筆資料與圖檔\n"
        "/editname [名稱] - 修改替換名稱\n"
        "/editinfo [名稱] - 修改替換備註\n"
        "/editphoto [名稱] - 啟動換圖流程"
    )
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(text, reply_markup=get_main_keyboard(), parse_mode='HTML')

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await help_cmd(update, context)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已終止目前所有流程。")

async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refresh_cache()
    await update.message.reply_text("✅ 已成功同步雲端快取資料！")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = {"mode": "add"}
    await update.message.reply_text("📸 請傳送「遊戲商圖片」來開始新增流程：")

async def supplier_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw = " ".join(context.args).strip()
    if not kw: return await update.message.reply_text("用法: /supplier [關鍵字]")
    await perform_search(update, kw)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name, uid = " ".join(context.args).strip(), update.effective_chat.id
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
        user_state[uid] = {"mode": "del_process"}
        await update.message.reply_text("🗑️ <b>刪除流程</b>\n請輸入要刪除的名稱：", parse_mode='HTML')

async def editname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name, uid = " ".join(context.args).strip(), update.effective_chat.id
    if name:
        idx, _ = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "en_step2", "old_name": name}
            await update.message.reply_text(f"🔍 找到【{name}】\n請輸入「新名稱」：")
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "en_step1"}
        await update.message.reply_text("📝 <b>修改名稱</b>\n請輸入「舊名稱」：", parse_mode='HTML')

async def editinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name, uid = " ".join(context.args).strip(), update.effective_chat.id
    if name:
        idx, row = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "ei_step2", "name": name, "idx": idx}
            info = row.get('info', '無')
            await update.message.reply_text(
                f"🔎 <b>找到遊戲商：【{name}】</b>\n"
                f"📝 目前備註：<code>{info}</code>\n\n"
                f"👆 <b>請直接輸入「新備註」內容並送出：</b>", 
                parse_mode='HTML'
            )
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "ei_step1"}
        await update.message.reply_text("✍️ <b>修改備註</b>\n請輸入想要修改的「遊戲商名稱」：", parse_mode='HTML')

async def editphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name, uid = " ".join(context.args).strip(), update.effective_chat.id
    if name:
        idx, _ = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "edit_photo_process", "name": name}
            await update.message.reply_text(f"📸 找到【{name}】，請直接傳送「新圖片」：")
        else: await update.message.reply_text(f"❌ 找不到「{name}」")
    else:
        user_state[uid] = {"mode": "ep_process"}
        await update.message.reply_text("🖼️ <b>更換圖片</b>\n請輸入遊戲商名稱：", parse_mode='HTML')

# ========== 7. 搜尋與訊息處理核心 ==========

async def perform_search(update, kw):
    res = [r for r in local_cache if kw.lower() in str(r.get("supplier", "")).strip().lower()]
    if not res: return await update.message.reply_text(f"❌ 找不到與「{kw}」相關的遊戲商")
    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text(f"🔍 找到 {len(res)} 筆相似結果，請選擇：", reply_markup=InlineKeyboardMarkup(btns))
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
            # 修正：更新圖片時同步設定 display_name
            cloudinary.uploader.upload(path, folder="supplier_bot", public_id=st["name"], display_name=st["name"], overwrite=True)
            user_state.pop(uid); await msg.reply_text(f"✅ 【{st['name']}】圖片更新完成！")
        return

    if msg.text:
        txt = msg.text.strip()
        if txt.startswith('/'): return
        
        if uid in user_state:
            st = user_state[uid]
            if st["mode"] == "add":
                if "name" not in st:
                    if find_in_cache(txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                    user_state[uid]["name"] = txt
                    await msg.reply_text(f"📝 請輸入【{txt}】的備註：")
                else:
                    # 修正：上傳時加入 display_name
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"], display_name=st["name"])
                    sheet.append_row([st["name"], res["secure_url"], txt])
                    refresh_cache(); user_state.pop(uid); await msg.reply_text("✅ 新增成功！")
            
            elif st["mode"] == "en_step1":
                idx, _ = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "en_step2", "old_name": txt}
                    await msg.reply_text(f"🔍 找到【{txt}】，請輸入「新名稱」：")
                else: await msg.reply_text("❌ 找不到該名稱，請重新輸入：")
            elif st["mode"] == "en_step2":
                old_name = st["old_name"]
                idx = find_in_cache(old_name)[0]
                sheet.update_cell(idx, 1, txt)
                try:
                    # 修正：改名後使用 API 更新 display_name
                    cloudinary.uploader.rename(f"supplier_bot/{old_name}", f"supplier_bot/{txt}", overwrite=True)
                    cloudinary.api.update(f"supplier_bot/{txt}", display_name=txt)
                    
                    new_url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload/supplier_bot/{txt}"
                    info = cloudinary.api.resource(f"supplier_bot/{txt}")
                    sheet.update_cell(idx, 2, info["secure_url"])
                    sheet.update_cell(idx, 2, new_url)
                except: pass
                refresh_cache(); user_state.pop(uid); await msg.reply_text(f"✅ 已將名稱改為【{txt}】")
            
            elif st["mode"] == "ei_step1":
                idx, row = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "ei_step2", "name": txt, "idx": idx}
                    await msg.reply_text(f"🔎 <b>找到【{txt}】</b>\n目前備註：<code>{row.get('info', '無')}</code>\n\n👆 請輸入新備註：", parse_mode='HTML')
                else: await msg.reply_text("❌ 找不到名稱，請重新輸入：")
            elif st["mode"] == "ei_step2":
                sheet.update_cell(st["idx"], 3, txt)
                refresh_cache(); user_state.pop(uid); await msg.reply_text(f"✅ 備註更新成功！\n【{st['name']}】的新備註為：\n<code>{txt}</code>", parse_mode='HTML')
            
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
            await perform_search(update, txt)

# ========== 8. 按鈕回調處理 ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_cancel':
        user_state.pop(uid, None); await query.message.reply_text("🚫 已終止目前流程。")
    elif data == 'm_admin_menu':
        await query.edit_message_text("🛠️ <b>進階管理模式</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML')
    elif data == 'm_main_menu':
        help_text = (
            "<b>📖 機器人使用說明書</b>\n\n"
            "你可以點擊選單按鈕操作，或是輸入指令操作。\n\n"
            "📌 <b>通用指令</b>\n"
            "/start - 開啟主選單\n"
            "/help - 顯示此說明\n"
            "/cancel - 終止目前流程\n"
            "/refresh - 同步雲端資料\n\n"
            "🛠️ <b>快速操作指令</b>\n"
            "/add [名稱] - 啟動新增遊戲商流程\n"
            "/supplier [關鍵字] - 快速搜尋遊戲商\n\n"
            "⚙️ <b>進階管理</b>\n"
            "/delete [名稱] - 刪除該筆資料與圖檔\n"
            "/editname [名稱] - 修改替換名稱\n"
            "/editinfo [名稱] - 修改替換備註\n"
            "/editphoto [名稱] - 啟動換圖流程"
        )
        await query.edit_message_text(help_text, reply_markup=get_main_keyboard(), parse_mode='HTML')
    elif data == 'm_add':
        user_state[uid] = {"mode": "add"}; await query.message.reply_text("📸 請傳送遊戲商圖片：")
    elif data == 'm_en_hint':
        user_state[uid] = {"mode": "en_step1"}; await query.message.reply_text("📝 <b>修改名稱</b>\n請輸入「舊名稱」：", parse_mode='HTML')
    elif data == 'm_ei_hint':
        user_state[uid] = {"mode": "ei_step1"}; await query.message.reply_text("✍️ <b>修改備註</b>\n請輸入「遊戲商名稱」：", parse_mode='HTML')
    elif data == 'm_ep_hint':
        user_state[uid] = {"mode": "ep_process"}; await query.message.reply_text("🖼️ <b>更換圖片</b>\n請輸入名稱：", parse_mode='HTML')
    elif data == 'm_del_hint':
        user_state[uid] = {"mode": "del_process"}; await query.message.reply_text("🗑️ <b>刪除流程</b>\n請輸入名稱：", parse_mode='HTML')
    elif data == 'm_ref':
        refresh_cache(); await query.message.reply_text("✅ 已成功同步雲端快取資料！")
    elif data.startswith('v_'):
        _, row = find_in_cache(data[2:])
        if row: await query.message.reply_photo(photo=row["image_url"], caption=f"🎮 {row['supplier']}\n📝 {row['info']}")


# ========== 9. 啟動 ==========
if __name__ == "__main__":
    
    # 啟動 Render 所需的 Web Server 執行 (防休眠)
    threading.Thread(target=start_health_server, daemon=True).start()

    # 啟動每日自動同步排程
    start_daily_refresh()

    # 初始化 Telegram Application
    app = ApplicationBuilder().token(TOKEN).build()

    # 註冊所有處理器 (Handler)
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("supplier", supplier_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("editname", editname_cmd))
    app.add_handler(CommandHandler("editinfo", editinfo_cmd))
    app.add_handler(CommandHandler("editphoto", editphoto_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO, handle_all))

    # --- 採用雲端穩定啟動方案 ---避免在 Render 產生 Event Loop 衝突
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        loop = asyncio.get_event_loop()
        
        # 1. 初始化
        loop.run_until_complete(app.initialize())
        # 2. 啟動Bot
        loop.run_until_complete(app.start())
        # 3. 手動啟用run_polling：啟動「接收訊息」的 Polling
        loop.run_until_complete(app.updater.start_polling())
        
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass




