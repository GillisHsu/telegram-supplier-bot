import os
import json
import gspread
import cloudinary
import cloudinary.uploader
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== 1. 設定區塊 ==========
# Telegram & Google 設定
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

# Cloudinary 設定
cloudinary.config(
    cloud_name = os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key = os.environ["CLOUDINARY_API_KEY"],
    api_secret = os.environ["CLOUDINARY_API_SECRET"],
    secure = True
)

# Google Sheet 初始化
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_KEY_JSON), scope)
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

user_state = {}

# ========== 2. 機器人邏輯 ==========

async def addsupplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商圖片")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state: return
    
    # 下載圖片到 Railway 暫存區
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)
    
    user_state[chat]["image_path"] = path
    await update.message.reply_text("✍️ 請輸入遊戲商名稱")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state: return
    state = user_state[chat]

    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商資訊")
        return

    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 正在上傳圖片並寫入表格...")

        try:
            # A. 上傳圖片到 Cloudinary
            # folder 參數可以讓圖片在後台自動分類到該資料夾
            upload_result = cloudinary.uploader.upload(
                state["image_path"], 
                folder = "supplier_bot",
                public_id = state["supplier"] # 使用名稱作為檔名
            )
            
            # 取得圖片網址
            image_url = upload_result.get("secure_url")

            # B. 寫入 Google Sheet
            sheet.append_row([state["supplier"], image_url, state["info"]])
            
            await update.message.reply_text(f"✅ 【{state['supplier']}】已成功新增！\n圖片網址：{image_url}")

        except Exception as e:
            await update.message.reply_text(f"❌ 存檔失敗：{str(e)}")
            print(f"Error: {e}")

        # 清除暫存檔案
        if os.path.exists(state.get("image_path", "")):
            os.remove(state["image_path"])
        del user_state[chat]

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("請輸入名稱，例如： /supplier ABC")
        return
    
    name = " ".join(context.args)
    rows = sheet.get_all_records()
    for r in rows:
        if name.lower() in str(r.get("supplier", "")).lower():
            await update.message.reply_photo(
                photo=r["image_url"], 
                caption=f"🎮 {r['supplier']}\n\n{r['info']}"
            )
            return
    await update.message.reply_text("❌ 找不到該遊戲商")

# ========== 3. 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("addsupplier", addsupplier))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
