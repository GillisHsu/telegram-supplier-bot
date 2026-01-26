import os, json, gspread, cloudinary, cloudinary.uploader
import cloudinary.api  
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
        local_cache = []
        for r in raw_data:
            name = str(r.get("supplier", "")).strip()
            if name:
                r["supplier"] = name 
                local_cache.append(r)
        print(f"✨ 緩存同步成功：{len(local_cache)} 筆")
    except Exception as e: print(f"❌ 同步失敗: {e}")

def find_in_cache(name):
    if not name: return None, None
    n = str(name).strip().lower()
    for i, row in enumerate(local_cache, start=2):
        db_name = str(row.get("supplier", "")).strip().lower()
        if db_name == n: 
            return i, row
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
        [InlineKeyboardButton("🚫 終止目前流程", callback_data='m_cancel'),
         InlineKeyboardButton("⬅️ 返回主選單", callback_data='m_main_menu')]
    ])

# ========== 3. 指令定義區 ==========

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 **主選單**", reply_markup=get_main_keyboard())

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_chat.id, None)
    await update.message.reply_text("🚫 已終止目前所有流程。")

async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refresh_cache()
    await update.message.reply_text("✅ 快取已成功同步！")

async def editinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    user_state.pop(uid, None) # 強制清除舊狀態
    
    name = " ".join(context.args).strip()
    if name:
        idx, row = find_in_cache(name)
        if idx:
            user_state[uid] = {"mode": "ei_step2", "name": name, "idx": idx}
            await update.message.reply_text(f"🔎 **【{name}】目前的備註：**\n`{row.get('info', '無')}`\n\n👆 請直接輸入「新備註」內容並送出：", parse_mode='Markdown')
        else:
            user_state[uid] = {"mode": "ei_step1"}
            await update.message.reply_text(f"❌ 找不到「{name}」，請輸入正確名稱：")
    else:
        user_state[uid] = {"mode": "ei_step1"}
        await update.message.reply_text("✍️ **修改備註**\n請輸入想要修改的「遊戲商名稱」：")

async def add_cmd(update, context):
    user_state[update.effective_chat.id] = {"mode": "add"}
    await update.message.reply_text("📸 請傳送圖片：")

# ========== 4. 搜尋與訊息處理核心 ==========

async def perform_search(update, kw):
    res = [r for r in local_cache if kw.lower() in str(r.get("supplier", "")).lower()]
    if not res: return await update.message.reply_text(f"❌ 找不到與「{kw}」相關的資料")
    if len(res) > 1:
        btns = [[InlineKeyboardButton(r['supplier'], callback_data=f"v_{r['supplier']}")] for r in res]
        await update.message.reply_text("🔍 找到相似結果：", reply_markup=InlineKeyboardMarkup(btns))
    else:
        r = res[0]
        try: await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 {r['supplier']}\n📝 {r['info'] or '無'}")
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
        return

    if msg.text:
        txt = msg.text.strip()
        if txt.startswith('/'): return
        
        if uid in user_state:
            st = user_state[uid]
            
            # 修改備註流程
            if st["mode"] == "ei_step1":
                idx, row = find_in_cache(txt)
                if idx:
                    user_state[uid] = {"mode": "ei_step2", "name": txt, "idx": idx}
                    await msg.reply_text(f"🔎 **【{txt}】目前的備註：**\n`{row.get('info', '無')}`\n\n👆 請輸入新備註送出：", parse_mode='Markdown')
                else:
                    await msg.reply_text(f"❌ 找不到「{txt}」，請重新輸入：")
                return 

            elif st["mode"] == "ei_step2":
                sheet.update_cell(st["idx"], 3, txt)
                refresh_cache(); user_state.pop(uid)
                await msg.reply_text(f"✅ 備註已更新！")
                return 

            # 新增流程
            elif st["mode"] == "add":
                if "name" not in st:
                    if find_in_cache(txt)[0]: return await msg.reply_text("⚠️ 名稱已存在")
                    user_state[uid]["name"] = txt
                    await msg.reply_text(f"📝 請輸入【{txt}】的備註：")
                else:
                    res = cloudinary.uploader.upload(st["path"], folder="supplier_bot", public_id=st["name"])
                    sheet.append_row([st["name"], res["secure_url"], txt])
                    refresh_cache(); user_state.pop(uid); await msg.reply_text("✅ 新增成功！")
                return
        
        await perform_search(update, txt)

# ========== 5. 按鈕回調處理 ==========

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid, data = query.message.chat_id, query.data
    
    if data == 'm_cancel':
        user_state.pop(uid, None); await query.message.reply_text("🚫 已終止流程。")
    elif data == 'm_admin_menu':
        await query.edit_message_text("🛠️ **進階管理模式**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
    elif data == 'm_main_menu':
        await query.edit_message_text("📖 **主選單**", reply_markup=get_main_keyboard())
    elif data == 'm_ei_hint':
        user_state[uid] = {"mode": "ei_step1"}
        await query.message.reply_text("✍️ 請輸入「遊戲商名稱」：")
    elif data == 'm_ref':
        refresh_cache(); await query.message.reply_text("✅ 快取已成功同步！")
    elif data.startswith('v_'):
        _, row = find_in_cache(data[2:])
        if row: await query.message.reply_photo(photo=row["image_url"], caption=f"🎮 {row['supplier']}\n📝 {row['info']}")

# ========== 6. 啟動與 Handler 順序校正 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    # 指令類 Handler (優先度高)
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("editinfo", editinfo_cmd))
    
    # 回調類 Handler
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # 萬用訊息處理 (優先度低，放在最後)
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_all))
    
    print("🚀 整合修正版啟動成功。請先輸入 /cancel 確保狀態清空後再開始。")
    app.run_polling()
