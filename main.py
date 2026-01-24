import os
import json
import gspread
import cloudinary
import cloudinary.uploader
import cloudinary.api
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# ========== 1. 設定區塊 ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

cloudinary.config(
    cloud_name = os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key = os.environ["CLOUDINARY_API_KEY"],
    api_secret = os.environ["CLOUDINARY_API_SECRET"],
    secure = True
)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_KEY_JSON), scope)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

user_state = {}

# ========== 2. 工具函數 ==========

def find_row_by_name(name):
    data = sheet.get_all_records()
    for i, row in enumerate(data, start=2):
        if str(row.get("supplier", "")).strip() == name.strip():
            return i, row
    return None, None

# ========== 3. 指令選單 (Inline Keyboard) ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ 新增遊戲商", callback_data='menu_add'),
         InlineKeyboardButton("🔍 搜尋遊戲商", callback_data='menu_search')],
        [InlineKeyboardButton("✏️ 修改名稱", callback_data='menu_edit_name'),
         InlineKeyboardButton("📝 修改備註", callback_data='menu_edit_info')],
        [InlineKeyboardButton("🖼️ 更換圖片", callback_data='menu_edit_photo')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🎮 **遊戲商管理系統**\n\n請點擊下方按鈕進行操作，或直接輸入指令。"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'menu_add':
        user_state[query.message.chat_id] = {"mode": "add"}
        await query.message.reply_text("📸 請上傳遊戲商圖片")
    elif query.data == 'menu_search':
        await query.message.reply_text("🔎 請輸入 `/supplier 關鍵字`", parse_mode='Markdown')
    elif query.data == 'menu_edit_name':
        await query.message.reply_text("✏️ 請輸入：\n`/editname 舊名稱 新名稱`", parse_mode='Markdown')
    elif query.data == 'menu_edit_info':
        await query.message.reply_text("📝 請輸入：\n`/editinfo 名稱 新備註`", parse_mode='Markdown')
    elif query.data == 'menu_edit_photo':
        await query.message.reply_text("🖼️ 請輸入：\n`/editphoto 名稱`", parse_mode='Markdown')

# ========== 4. 核心功能 ==========

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("請輸入搜尋關鍵字，例如： `/supplier ABC`", parse_mode='Markdown')
        return
    keyword = " ".join(context.args).lower()
    data = sheet.get_all_records()
    results = [r for r in data if keyword in str(r.get("supplier", "")).lower()]
    if not results:
        await update.message.reply_text("❌ 找不到符合條件的遊戲商")
        return
    for r in results:
        await update.message.reply_photo(photo=r["image_url"], caption=f"🎮 遊戲商：{r['supplier']}\n📝 資訊：{r['info']}")

async def editname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ 格式錯誤！範例： `/editname 舊名稱 新名稱`", parse_mode='Markdown')
        return
    old_n, new_n = context.args[0], context.args[1]
    row_idx, _ = find_row_by_name(old_n)
    
    if not row_idx:
        await update.message.reply_text(f"❌ 找不到遊戲商：{old_n}")
        return

    await update.message.reply_text(f"⏳ 正在同步更新雲端檔案與表格...")
    try:
        # 1. 同步更名 Cloudinary 上的檔案
        cloudinary.uploader.rename(
            from_public_id=f"supplier_bot/{old_n}",
            to_public_id=f"supplier_bot/{new_n}",
            overwrite=True,
            invalidate=True
        )
        # 2. 更新 Google Sheet
        new_url = f"https://res.cloudinary.com/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload/supplier_bot/{new_n}.jpg"
        sheet.update_cell(row_idx, 1, new_n)
        sheet.update_cell(row_idx, 2, new_url)
        await update.message.reply_text(f"✅ 更新成功！\n舊稱：{old_n}\n新稱：{new_n}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 雲端更名失敗(可能無檔案)，僅更新表格名稱：{str(e)}")
        sheet.update_cell(row_idx, 1, new_n)

async def editinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ 格式錯誤！範例： `/editinfo 名稱 新備註`")
        return
    name = context.args[0]
    info = " ".join(context.args[1:])
    row_idx, _ = find_row_by_name(name)
    if row_idx:
        sheet.update_cell(row_idx, 3, info)
        await update.message.reply_text(f"✅ 【{name}】備註更新成功！")
    else:
        await update.message.reply_text(f"❌ 找不到遊戲商：{name}")

async def editphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ 格式錯誤！範例： `/editphoto 名稱`")
        return
    name = context.args[0]
    row_idx, _ = find_row_by_name(name)
    if row_idx:
        user_state[update.effective_chat.id] = {"mode": "edit_photo", "target": name}
        await update.message.reply_text(f"📸 請上傳【{name}】的新圖片")
    else:
        await update.message.reply_text(f"❌ 找不到遊戲商：{name}")

# ========== 5. 訊息處理邏輯 ==========

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state: return
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)
    state = user_state[chat]
    
    if state["mode"] == "add":
        state["image_path"] = path
        await update.message.reply_text("✍️ 請輸入遊戲商名稱")
    
    elif state["mode"] == "edit_photo":
        await update.message.reply_text("⏳ 正在清理舊圖並上傳新圖...")
        try:
            # 使用 invalidate=True 徹底清除舊緩存
            upload_result = cloudinary.uploader.upload(
                path, 
                folder="supplier_bot", 
                public_id=state["target"],
                overwrite=True,
                invalidate=True
            )
            row_idx, _ = find_row_by_name(state["target"])
            sheet.update_cell(row_idx, 2, upload_result.get("secure_url"))
            await update.message.reply_text(f"✅ 【{state['target']}】圖片已更新並清理舊緩存！")
        except Exception as e:
            await update.message.reply_text(f"❌ 更新失敗：{str(e)}")
        if os.path.exists(path): os.remove(path)
        del user_state[chat]

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state or user_state[chat]["mode"] != "add": return
    state = user_state[chat]
    
    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商備註資訊")
        return

    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 正在處理上傳...")
        try:
            upload_result = cloudinary.uploader.upload(state["image_path"], folder="supplier_bot", public_id=state["supplier"])
            sheet.append_row([state["supplier"], upload_result.get("secure_url"), state["info"]])
            await update.message.reply_text(f"✅ 【{state['supplier']}】新增成功！")
        except Exception as e:
            await update.message.reply_text(f"❌ 失敗：{str(e)}")
        if os.path.exists(state.get("image_path", "")): os.remove(state["image_path"])
        del user_state[chat]

# ========== 6. 主啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(CommandHandler("editname", editname))
    app.add_handler(CommandHandler("editinfo", editinfo))
    app.add_handler(CommandHandler("editphoto", editphoto))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("機器人已啟動...")
    app.run_polling()
