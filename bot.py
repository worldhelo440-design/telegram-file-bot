import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import time
import secrets
import json
import os
from datetime import datetime
from flask import Flask, request
import asyncio
from threading import Thread

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8260566072:AAGqxekCKCnOS7irDoADYC7ZlPjM2FqzNIo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1876238439"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8080))

# Storage files
PAYLOAD_FILE = "payload_data.json"
ACCESS_FILE = "user_access.json"
CAPTION_FILE = "caption_data.json"

payload_data = {}
user_access = {}
admin_sessions = {}
caption_data = {"start_caption": "", "end_caption": ""}

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

def load_data():
    """Load all data from files"""
    global payload_data, user_access, caption_data
    
    if os.path.exists(PAYLOAD_FILE):
        try:
            with open(PAYLOAD_FILE, 'r') as f:
                payload_data = json.load(f)
            logger.info(f"Loaded {len(payload_data)} payloads")
        except Exception as e:
            logger.error(f"Error loading payloads: {e}")
            payload_data = {}
    
    if os.path.exists(ACCESS_FILE):
        try:
            with open(ACCESS_FILE, 'r') as f:
                user_access = json.load(f)
            logger.info(f"Loaded user access data")
        except Exception as e:
            logger.error(f"Error loading access data: {e}")
            user_access = {}
    
    if os.path.exists(CAPTION_FILE):
        try:
            with open(CAPTION_FILE, 'r') as f:
                caption_data = json.load(f)
            logger.info(f"Loaded captions")
        except Exception as e:
            logger.error(f"Error loading captions: {e}")
            caption_data = {"start_caption": "", "end_caption": ""}

def save_payloads():
    try:
        with open(PAYLOAD_FILE, 'w') as f:
            json.dump(payload_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving payloads: {e}")

def save_access():
    try:
        with open(ACCESS_FILE, 'w') as f:
            json.dump(user_access, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving access: {e}")

def save_captions():
    try:
        with open(CAPTION_FILE, 'w') as f:
            json.dump(caption_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving captions: {e}")

async def delete_user_messages(bot, chat_id, message_ids):
    """Delete messages from user's chat after 1 hour"""
    try:
        await asyncio.sleep(3600)
        
        deleted = 0
        for msg_id in message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted += 1
            except Exception as e:
                logger.error(f"Could not delete message {msg_id}: {e}")
        
        logger.info(f"Deleted {deleted}/{len(message_ids)} messages from chat {chat_id}")
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="🔥 **Files Auto-Deleted!**\n\nYour 1-hour timer expired. Click the link again to get fresh copies!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Could not send deletion notice: {e}")
    except Exception as e:
        logger.error(f"Error in delete_user_messages: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if context.args:
        payload = context.args[0]
        
        if payload not in payload_data:
            await update.message.reply_text("❌ Invalid link!")
            return
        
        start_msg = caption_data.get("start_caption", "")
        if start_msg:
            await update.message.reply_text(start_msg, parse_mode='Markdown')
        
        await update.message.reply_text(
            f"⏰ **IMPORTANT: 1 HOUR AUTO-DELETE!**\n\n"
            f"📦 Sending {len(payload_data[payload]['files'])} files...\n"
            f"⚠️ **Files will be AUTOMATICALLY DELETED after 1 hour!**\n"
            f"💾 Forward them to Saved Messages NOW!",
            parse_mode='Markdown'
        )
        
        sent_message_ids = []
        success_count = 0
        
        for file_id in payload_data[payload]["files"]:
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=ADMIN_ID,
                    message_id=file_id
                )
                sent_message_ids.append(sent_msg.message_id)
                success_count += 1
            except Exception as e:
                logger.error(f"Error forwarding file: {e}")
        
        end_msg = caption_data.get("end_caption", "")
        if end_msg:
            await update.message.reply_text(end_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(
                f"✅ **{success_count} files sent!**\n\n"
                f"⚠️ **URGENT:** Forward these to Saved Messages NOW!\n"
                f"🔥 Auto-delete in 60 minutes!\n"
                f"🔄 Click the link again after deletion to get fresh copies.",
                parse_mode='Markdown'
            )
        
        asyncio.create_task(delete_user_messages(context.bot, chat_id, sent_message_ids))
        
        if payload not in user_access:
            user_access[payload] = {}
        user_access[payload][str(user_id)] = time.time()
        save_access()
        
        logger.info(f"User {user_id} accessed payload {payload[:8]}, deletion scheduled")
        
    else:
        if user_id == ADMIN_ID:
            await update.message.reply_text(
                "👋 **Welcome Admin!**\n\n"
                "**Admin Commands:**\n"
                "• `/startp <name>` - Start collecting files\n"
                "• `/stopp` - Finish and get share link\n"
                "• `/setcaption` - Set start/end captions\n"
                "• `/status` - Check active payloads\n"
                "• `/listpayloads` - List all payloads\n"
                "• `/deletepayload <code>` - Delete a payload\n\n"
                "Forward files after /startp to add them.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("👋 Welcome! Send me a valid link to access files.")

async def start_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /startp <name>\nExample: /startp movies")
        return
    
    payload_name = ' '.join(context.args)
    admin_sessions[user_id] = {"payload": payload_name, "files": []}
    
    await update.message.reply_text(
        f"📁 **Started:** {payload_name}\n\n"
        f"Forward files now. Send /stopp when done.",
        parse_mode='Markdown'
    )

async def stop_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if user_id not in admin_sessions:
        await update.message.reply_text("❌ No active collection! Use /startp first.")
        return
    
    session = admin_sessions[user_id]
    
    if not session["files"]:
        await update.message.reply_text("❌ No files added!")
        del admin_sessions[user_id]
        return
    
    unique_payload = secrets.token_urlsafe(16)
    
    payload_data[unique_payload] = {
        "name": session['payload'],
        "files": session["files"],
        "created_at": time.time(),
        "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    save_payloads()
    
    bot_info = await context.bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={unique_payload}"
    
    del admin_sessions[user_id]
    
    await update.message.reply_text(
        f"✅ **Collection Created!**\n\n"
        f"📦 Name: {session['payload']}\n"
        f"📄 Files: {len(session['files'])}\n"
        f"🔥 Auto-delete: 1 hour after sending\n"
        f"🔄 Users can click again for fresh copies\n"
        f"🔑 Code: `{unique_payload}`\n\n"
        f"🔗 **Share Link:**\n`{share_link}`",
        parse_mode='Markdown'
    )

async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text(
        "📝 **Set Captions**\n\n"
        "Reply to this message with:\n"
        "`START: your start message`\n"
        "`END: your end message`\n\n"
        "Example:\n"
        "`START: Welcome! Here are your files.`\n"
        "`END: ⚠️ Forward immediately! Auto-delete in 1 hour!`\n\n"
        "Send 'CLEAR' to remove captions.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not payload_data:
        await update.message.reply_text("📊 No active payloads.")
        return
    
    status_text = f"📊 **Active Payloads:** {len(payload_data)}\n\n"
    
    for payload, data in list(payload_data.items())[:10]:
        access_count = len(user_access.get(payload, {}))
        status_text += f"• **{data.get('name', 'Unnamed')}**\n"
        status_text += f"  Files: {len(data['files'])} | Users: {access_count}\n"
        status_text += f"  Code: `{payload[:12]}...`\n\n"
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def list_payloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not payload_data:
        await update.message.reply_text("📊 No payloads found.")
        return
    
    list_text = "📋 **All Payloads:**\n\n"
    
    for i, (payload, data) in enumerate(payload_data.items(), 1):
        access_count = len(user_access.get(payload, {}))
        created = data.get('created_date', 'Unknown')
        list_text += f"{i}. **{data.get('name', 'Unnamed')}**\n"
        list_text += f"   Created: {created}\n"
        list_text += f"   Files: {len(data['files'])} | Users: {access_count}\n"
        list_text += f"   Code: `{payload}`\n\n"
    
    await update.message.reply_text(list_text, parse_mode='Markdown')

async def delete_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /deletepayload <code>")
        return
    
    payload = context.args[0]
    
    if payload not in payload_data:
        await update.message.reply_text("❌ Payload not found!")
        return
    
    name = payload_data[payload].get('name', 'Unnamed')
    del payload_data[payload]
    
    if payload in user_access:
        del user_access[payload]
    
    save_payloads()
    save_access()
    
    await update.message.reply_text(f"✅ Deleted: **{name}**", parse_mode='Markdown')

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text
        if reply_text and "Set Captions" in reply_text and user_id == ADMIN_ID:
            text = update.message.text
            
            if text.upper() == 'CLEAR':
                caption_data["start_caption"] = ""
                caption_data["end_caption"] = ""
                save_captions()
                await update.message.reply_text("✅ Captions cleared!")
                return
            
            if 'START:' in text:
                start = text.split('START:')[1].split('END:')[0].strip()
                caption_data["start_caption"] = start
            
            if 'END:' in text:
                end = text.split('END:')[1].strip()
                caption_data["end_caption"] = end
            
            save_captions()
            await update.message.reply_text("✅ Captions updated!", parse_mode='Markdown')
            return
    
    if user_id == ADMIN_ID and user_id in admin_sessions:
        message_id = update.message.message_id
        admin_sessions[user_id]["files"].append(message_id)
        count = len(admin_sessions[user_id]["files"])
        await update.message.reply_text(f"✅ File #{count} added!")

@app.route('/')
def index():
    return "Bot is running! 🚀"

@app.route('/health')
def health():
    return "OK", 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    asyncio.run(bot_app.process_update(update))
    return "OK"

bot_app = Application.builder().token(BOT_TOKEN).build()

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("startp", start_payload))
bot_app.add_handler(CommandHandler("stopp", stop_payload))
bot_app.add_handler(CommandHandler("setcaption", set_caption))
bot_app.add_handler(CommandHandler("status", status))
bot_app.add_handler(CommandHandler("listpayloads", list_payloads))
bot_app.add_handler(CommandHandler("deletepayload", delete_payload))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

def setup_bot():
    load_data()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_app.initialize())
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        loop.run_until_complete(bot_app.bot.set_webhook(url=webhook_url))
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.warning("WEBHOOK_URL not set!")

if __name__ == "__main__":
    setup_bot()
    logger.info(f"Starting Flask on port {PORT}")
    app.run(host='0.0.0.0', port=PORT)
