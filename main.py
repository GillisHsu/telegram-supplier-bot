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
    chat = update.effective_chat.id
    if chat not in user_state: return
    state = user_state[chat]

    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商資訊")
        return

    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 正在繞過配額限制進行存檔，請稍候...")

        try:
            # 1. 準備檔案元數據
            file_metadata = {
                "name": f"{state['supplier']}.jpg",
                "parents": [FOLDER_ID]
            }

            media = MediaFileUpload(state["image"], mimetype="image/jpeg")
            
            # 2. 執行上傳
            # 重點：supportsAllDrives=True 是必須的
            file_drive = drive.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True
            ).execute()

            # 3. 取得檔案 ID
            file_id = file_drive.get("id")

            # 4. 強制轉移所有權邏輯 (避免扣除機器人配額)
            # 在資料夾已經共享的情況下，檔案會繼承父目錄空間
            drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True
            ).execute()

            image_url = f"https://drive.google.com/uc?id={file_id}"

            # 5. 寫入 Google Sheet
            sheet.append_row([state["supplier"], image_url, state["info"]])
            
            await update.message.reply_text(f"✅ 【{state['supplier']}】已成功新增！")
            
        except Exception as e:
            # 如果依然噴 Quota 錯誤，表示 Google 強制要求使用「OAuth2 委派」或「共享雲端硬碟」
            error_msg = str(e)
            if "storageQuotaExceeded" in error_msg:
                await update.message.reply_text("❌ 空間報錯依舊。請確認您的資料夾不是在『我的雲端硬碟』下，而是建議建立一個專門的『共享雲端硬碟』(Shared Drive) 給機器人使用。")
            else:
                await update.message.reply_text(f"❌ 存檔失敗：{error_msg}")
            print(f"Detailed Error: {e}")

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

