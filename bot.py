import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import time
import secrets
import json
import os
from datetime import datetime

# Configuration - Load from environment or use defaults
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8260566072:AAGqxekCKCnOS7irDoADYC7ZlPjM2FqzNIo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1876238439"))

# Persistent storage file
STORAGE_FILE = "payload_data.json"

# Storage
payload_data = {}
admin_sessions = {}
user_access = {}

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def load_data():
    """Load payload data from file"""
    global payload_data
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, 'r') as f:
                payload_data = json.load(f)
            logger.info(f"Loaded {len(payload_data)} payloads from storage")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            payload_data = {}
    else:
        payload_data = {}

def save_data():
    """Save payload data to file"""
    try:
        with open(STORAGE_FILE, 'w') as f:
            json.dump(payload_data, f, indent=2)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def is_expired_for_user(payload, user_id):
    """Check if payload has expired for a specific user"""
    if payload not in payload_data:
        return True
    
    if user_id not in user_access.get(payload, {}):
        return False
    
    user_start_time = user_access[payload][user_id]
    return time.time() > user_start_time + 3600

def mark_user_access(payload, user_id):
    """Mark when a user first accesses a payload"""
    if payload not in user_access:
        user_access[payload] = {}
    
    if user_id not in user_access[payload]:
        user_access[payload][user_id] = time.time()
        logger.info(f"User {user_id} accessed payload {payload[:8]}...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.args:
        payload = context.args[0]
        
        if payload not in payload_data:
            await update.message.reply_text("❌ Invalid link!")
            return
        
        if is_expired_for_user(payload, user_id):
            await update.message.reply_text("❌ This link has expired for you!")
            return
        
        mark_user_access(payload, user_id)
        
        user_start = user_access[payload][user_id]
        remaining_seconds = int((user_start + 3600) - time.time())
        remaining_minutes = remaining_seconds // 60
        
        await update.message.reply_text(
            f"⚠️ **IMPORTANT**: You have **{remaining_minutes} minutes** to download these files!\n"
            f"Your personal timer started now.\n\n"
            f"Sending {len(payload_data[payload]['files'])} files...",
            parse_mode='Markdown'
        )
        
        for file_id in payload_data[payload]["files"]:
            try:
                await context.bot.copy_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=ADMIN_ID,
                    message_id=file_id
                )
            except Exception as e:
                logger.error(f"Error forwarding file: {e}")
        
        await update.message.reply_text("✅ All files sent!")
    else:
        if user_id == ADMIN_ID:
            await update.message.reply_text(
                "👋 Welcome Admin!\n\n"
                "**Admin Commands:**\n"
                "/startpayload <name> - Start collecting files\n"
                "/stoppayload - Finish and get share link\n"
                "/status - Check active payloads\n"
                "/listpayloads - List all payloads\n"
                "/deletepayload <code> - Delete a payload\n\n"
                "Forward files after /startpayload to add them.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("👋 Welcome! Send me a valid link to access files.")

async def start_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is for admins only!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /startpayload <name>\nExample: /startpayload movies")
        return
    
    payload_name = ' '.join(context.args)
    admin_sessions[user_id] = {"payload": payload_name, "files": []}
    
    await update.message.reply_text(
        f"📁 Started collection: **{payload_name}**\n\n"
        f"Forward files to me now. When done, send /stoppayload",
        parse_mode='Markdown'
    )

async def stop_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is for admins only!")
        return
    
    if user_id not in admin_sessions:
        await update.message.reply_text("❌ No active collection! Use /startpayload first.")
        return
    
    session = admin_sessions[user_id]
    
    if not session["files"]:
        await update.message.reply_text("❌ No files were added! Forward files before stopping.")
        del admin_sessions[user_id]
        return
    
    unique_payload = secrets.token_urlsafe(16)
    current_time = time.time()
    
    payload_data[unique_payload] = {
        "name": session['payload'],
        "files": session["files"],
        "created_at": current_time,
        "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    save_data()
    
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    share_link = f"https://t.me/{bot_username}?start={unique_payload}"
    
    del admin_sessions[user_id]
    
    await update.message.reply_text(
        f"✅ **Collection Created!**\n\n"
        f"📦 Name: {session['payload']}\n"
        f"📄 Files: {len(session['files'])}\n"
        f"⏰ Each user gets: 1 hour from first access\n"
        f"🔑 Code: `{unique_payload}`\n\n"
        f"🔗 **Share Link:**\n`{share_link}`\n\n"
        f"Copy and share this link!",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is for admins only!")
        return
    
    if not payload_data:
        await update.message.reply_text("📊 No active payloads.")
        return
    
    status_text = f"📊 **Active Payloads:** {len(payload_data)}\n\n"
    
    for payload, data in list(payload_data.items())[:10]:
        access_count = len(user_access.get(payload, {}))
        status_text += f"• **{data.get('name', 'Unnamed')}**\n"
        status_text += f"  Files: {len(data['files'])} | Accessed by: {access_count} users\n"
        status_text += f"  Code: `{payload[:12]}...`\n\n"
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def list_payloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is for admins only!")
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
        await update.message.reply_text("❌ This command is for admins only!")
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
    
    save_data()
    
    await update.message.reply_text(f"✅ Deleted payload: **{name}**", parse_mode='Markdown')

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in admin_sessions:
        return
    
    message_id = update.message.message_id
    admin_sessions[user_id]["files"].append(message_id)
    file_count = len(admin_sessions[user_id]["files"])
    
    await update.message.reply_text(f"✅ File #{file_count} added!")

def main():
    """Main function to run the bot"""
    load_data()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startpayload", start_payload))
    app.add_handler(CommandHandler("stoppayload", stop_payload))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("listpayloads", list_payloads))
    app.add_handler(CommandHandler("deletepayload", delete_payload))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_files))
    
    print("✅ Bot is running!")
    print("=" * 50)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()