import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ========== 設定區塊 ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

# 請確保這是您在 image_a21d0a.png 中已經共用給機器人的資料夾 ID
FOLDER_ID = "1LZvoWvtHRmQdJTHRfJObSwZ90gZqsaHh" 

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
google_key = json.loads(GOOGLE_KEY_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_key, scope)

client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1
drive = build("drive", "v3", credentials=creds)

user_state = {}

# ========== 機器人邏輯 ==========

async def addsupplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商群組圖片")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat not in user_state: return
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)
    
    user_state[chat]["image"] = path
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
        await update.message.reply_text("⏳ 正在嘗試繞過空間限制存檔...")

        try:
            # 1. 準備元數據，強制指定父資料夾
            file_metadata = {
                "name": f"{state['supplier']}.jpg",
                "parents": [FOLDER_ID]
            }
            media = MediaFileUpload(state["image"], mimetype="image/jpeg", resumable=True)
            
            # 2. 執行上傳
            file_drive = drive.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()

            file_id = file_drive.get("id")

            # 3. 關鍵步驟：開放讀取權限
            # 這樣 Telegram 才能透過網址讀取圖片
            drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"}
            ).execute()

            image_url = f"https://drive.google.com/uc?id={file_id}"

            # 4. 寫入 Google Sheet
            sheet.append_row([state["supplier"], image_url, state["info"]])
            await update.message.reply_text(f"✅ 【{state['supplier']}】已成功新增！")

        except Exception as e:
            # 如果還是 Quota 錯誤，嘗試顯示更詳細的訊息
            await update.message.reply_text(f"❌ 存檔失敗：{str(e)}")
            print(f"Error: {e}")

        if os.path.exists(state.get("image", "")): 
            os.remove(state["image"])
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

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("addsupplier", addsupplier))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
