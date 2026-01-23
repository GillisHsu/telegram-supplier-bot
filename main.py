import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 讀取環境變數
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]
FOLDER_ID = "1LZvoWvtHRmQdJTHRfJObSwZ90gZqsaHh"

# Google API 設定
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

google_key = json.loads(GOOGLE_KEY_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_key, scope)

# 初始化 Google 客戶端
client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1
drive = build("drive", "v3", credentials=creds)

user_state = {}

# ========== 指令與處理邏輯 ==========

async def addsupplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商群組圖片")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    # 存放在 Railway 容器的臨時目錄
    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)

    user_state[chat]["image"] = path
    await update.message.reply_text("✍️ 請輸入遊戲商名稱")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    state = user_state[chat]

    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商資訊")
        return

    if "info" not in state:
        state["info"] = update.message.text

        # 上傳圖片到 Google Drive
        file_metadata = {
            "name": f"{state['supplier']}.jpg",
            "parents": [FOLDER_ID]
        }

        media = MediaFileUpload(state["image"], mimetype="image/jpeg")
        file_drive = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        # 設定權限為公開讀取
        drive.permissions().create(
            fileId=file_drive["id"],
            body={"type": "anyone", "role": "reader"}
        ).execute()

        image_url = f"https://drive.google.com/uc?id={file_drive['id']}"

        # 寫入 Google Sheet (欄位順序請對應你的表格)
        sheet.append_row([state["supplier"], image_url, state["info"]])

        await update.message.reply_text("✅ 遊戲商已成功新增")
        del user_state[chat]

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("請輸入遊戲商名稱，例如： /supplier ABC")
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

    await update.message.reply_text("❌ 找不到這個遊戲商")

# ========== 啟動主程式 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("addsupplier", addsupplier))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is running...")
    app.run_polling()