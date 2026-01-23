import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== 設定區塊 ==========
TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_KEY_JSON = os.environ["GOOGLE_KEY"]

# Google Sheet 設定
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
google_key = json.loads(GOOGLE_KEY_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(google_key, scope)

client = gspread.authorize(creds)
# 請確保您的 Google Sheet 名稱正確
sheet = client.open("telegram-supplier-bot").sheet1

# 暫存使用者狀態
user_state = {}

# ========== 機器人功能 ==========

async def addsupplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """開始新增遊戲商流程"""
    user_state[update.effective_chat.id] = {}
    await update.message.reply_text("📸 請上傳遊戲商圖片")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收圖片並取得 File ID"""
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    # 取得 Telegram 伺服器上的圖片 ID (不需要下載到雲端硬碟)
    file_id = update.message.photo[-1].file_id
    user_state[chat]["file_id"] = file_id
    await update.message.reply_text("✍️ 請輸入遊戲商名稱")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理名稱與資訊輸入，並寫入 Google Sheet"""
    chat = update.effective_chat.id
    if chat not in user_state:
        return

    state = user_state[chat]

    # 1. 處理名稱輸入
    if "supplier" not in state:
        state["supplier"] = update.message.text
        await update.message.reply_text("📝 請輸入遊戲商資訊")
        return

    # 2. 處理資訊輸入並存檔
    if "info" not in state:
        state["info"] = update.message.text
        await update.message.reply_text("⏳ 正在寫入資料庫...")

        try:
            # 寫入 Google Sheet：[名稱, 圖片ID, 資訊]
            sheet.append_row([state["supplier"], state["file_id"], state["info"]])
            await update.message.reply_text(f"✅ 【{state['supplier']}】已成功新增！")
        except Exception as e:
            await update.message.reply_text(f"❌ 寫入表格失敗：{str(e)}")

        # 清除暫存
        del user_state[chat]

async def supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查詢遊戲商"""
    if not context.args:
        await update.message.reply_text("請輸入遊戲商名稱，例如： /supplier ABC")
        return

    name = " ".join(context.args)
    rows = sheet.get_all_records()

    for r in rows:
        # 比對表格中的 supplier 欄位
        if name.lower() in str(r.get("supplier", "")).lower():
            # 使用儲存的 file_id 發送圖片 (這是 Telegram 內部的 ID)
            await update.message.reply_photo(
                photo=r["image_url"], 
                caption=f"🎮 {r['supplier']}\n\n{r['info']}"
            )
            return

    await update.message.reply_text("❌ 找不到這個遊戲商")

# ========== 啟動區塊 ==========

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("addsupplier", addsupplier))
    app.add_handler(CommandHandler("supplier", supplier))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("機器人運行中...")
    app.run_polling()
