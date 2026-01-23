import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN = os.environ["BOT_TOKEN"]

# Google API
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

import json
from oauth2client.service_account import ServiceAccountCredentials

google_key = json.loads(os.environ["GOOGLE_KEY"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_key, scope)

client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1

drive = build("drive", "v3", credentials=creds)

FOLDER_ID = "1LZvoWvtHRmQdJTHRfJObSwZ90gZqsaHh"

user_state = {}

# ========== 新增遊戲商 ==========

async def addsupplier(update, context):
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商群組圖片")

async def handle_photo(update, context):
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()

    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)

    user_state[chat]["image"] = path
    await update.message.reply_text("✍️ 請輸入遊戲商名稱")

async def handle_text(update, context):
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
            "name": state["supplier"] + ".jpg",
            "parents": [FOLDER_ID]
        }

        media = MediaFileUpload(state["image"], mimetype="image/jpeg")
        file = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        drive.permissions().create(
            fileId=file["id"],
            body={"type": "anyone", "role": "reader"}
        ).execute()

        image_url = f"https://drive.google.com/uc?id={file['id']}"

        # 寫入 Google Sheet
        sheet.append_row([state["supplier"], image_url, state["info"]])

        await update.message.reply_text("✅ 遊戲商已成功新增")

        del user_state[chat]

# ========== 查詢遊戲商 ==========

async def supplier(update, context):
    if not context.args:
        await update.message.reply_text("請輸入遊戲商名稱，例如： /supplier ABC")
        return

    name = " ".join(context.args)
    rows = sheet.get_all_records()

    for r in rows:
        if name.lower() in r["supplier"].lower():
            await update.message.reply_photo(
                photo=r["image_url"],
                caption=f"🎮 {r['supplier']}\n\n{r['info']}"
            )
            return

    await update.message.reply_text("❌ 找不到這個遊戲商")

# ========== 啟動 Bot ==========

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("addsupplier", addsupplier))
app.add_handler(CommandHandler("supplier", supplier))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

app.run_polling()
