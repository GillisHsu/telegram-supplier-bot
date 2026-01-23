import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ========== 設定區塊 ==========
# 從 Railway Variables 讀取
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

# 您的 Google Drive 資料夾 ID
FOLDER_ID = "1LZvoWvtHRmQdJTHRfJObSwZ90gZqsaHh"

# API 權限範圍
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# 初始化憑證與客戶端
google_key = json.loads(GOOGLE_KEY_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_key, scope)

client = gspread.authorize(creds)
sheet = client.open("telegram-supplier-bot").sheet1
drive = build("drive", "v3", credentials=creds)

# 暫存對話狀態
user_state = {}

# ========== 機器人功能函數 ==========

async def addsupplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """啟動新增流程"""
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商群組圖片")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理圖片上傳"""
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    # 暫存在容器的 /tmp 目錄
    path = f"/tmp/{chat}.jpg"
    await file.download_to_drive(path)

    user_state[chat]["image"] = path
    await update.message.reply_text("✍️ 請輸入遊戲商名稱")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理文字輸入並執行最終存檔"""
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    state = user_state[chat]

    # 1. 處理名稱
    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商資訊")
        return

    # 2. 處理資訊並執行上傳邏輯
    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 權限驗證中，正在執行雲端存檔...")

        try:
            # A. 上傳圖片到 Drive
            # 使用 parents 並在後面加入 supportsAllDrives=True 來解決 Quota 空間問題
            file_metadata = {
                "name": f"{state['supplier']}.jpg",
                "parents": [FOLDER_ID]
            }

            media = MediaFileUpload(state["image"], mimetype="image/jpeg")
            
            # 關鍵修正點：加入 supportsAllDrives=True
            file_drive = drive.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True 
            ).execute()

            # B. 開啟讀取權限
            drive.permissions().create(
                fileId=file_drive["id"],
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True
            ).execute()

            image_url = f"https://drive.google.com/uc?id={file_drive['id']}"

            # C. 寫入 Google Sheet
            sheet.append_row([state["supplier"], image_url, state["info"]])

            await update.message.reply_text(f"✅ 【{state['supplier']}】新增成功！")

        except Exception as e:
            await update.message.reply_text(f"❌ 存檔失敗：{str(e)}")
            print(f"Error: {e}")

        # 清理暫存檔案
        if os.path.exists(state.get("image", "")):
            os.remove(state["image"])
        del user_state[chat]

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查詢功能"""
    if not context.args:
        await update.message.reply_text("請輸入名稱，例如： /supplier ABC")
        return

    name = " ".join(context.args)
    rows = sheet.get_all_records()

    for r in rows:
        # 比對 Sheet 裡的 supplier 欄位
        if name.lower() in str(r.get("supplier", "")).lower():
            await update.message.reply_photo(
                photo=r["image_url"],
                caption=f"🎮 {r['supplier']}\n\n{r['info']}"
            )
            return

    await update.message.reply_text("❌ 找不到該遊戲商")

# ========== 主程式啟動 ==========

if __name__ == "__main__":
    # 使用 v20+ 的 ApplicationBuilder
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("addsupplier", addsupplier))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("機器人已啟動...")
    app.run_polling()
