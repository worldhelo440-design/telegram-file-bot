import logging
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import time
import secrets
import json
import os
from datetime import datetime, timezone
from flask import Flask, request
import asyncio
from threading import Thread
import requests

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8080))

# Storage files
PAYLOAD_FILE = "payload_data.json"
ACCESS_FILE = "user_access.json"
CAPTION_FILE = "caption_data.json"
DELETION_FILE = "scheduled_deletions.json"  # NEW: Track scheduled deletions

payload_data = {}
user_access = {}
admin_sessions = {}
caption_data = {"start_caption": "", "end_caption": ""}
scheduled_deletions = {}  # NEW: {deletion_id: {chat_id, message_ids, delete_at, payload}}

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Bot application
bot_app = None

def load_data():
    """Load all data from files"""
    global payload_data, user_access, caption_data, scheduled_deletions
    
    if os.path.exists(PAYLOAD_FILE):
        try:
            with open(PAYLOAD_FILE, 'r') as f:
                payload_data = json.load(f)
            logger.info(f"✅ Loaded {len(payload_data)} payloads")
        except Exception as e:
            logger.error(f"❌ Error loading payloads: {e}")
            payload_data = {}
    else:
        payload_data = {}
    
    if os.path.exists(ACCESS_FILE):
        try:
            with open(ACCESS_FILE, 'r') as f:
                user_access = json.load(f)
            logger.info(f"✅ Loaded user access data")
        except Exception as e:
            logger.error(f"❌ Error loading access data: {e}")
            user_access = {}
    else:
        user_access = {}
    
    if os.path.exists(CAPTION_FILE):
        try:
            with open(CAPTION_FILE, 'r') as f:
                caption_data = json.load(f)
            logger.info(f"✅ Loaded captions")
        except Exception as e:
            logger.error(f"❌ Error loading captions: {e}")
            caption_data = {"start_caption": "", "end_caption": ""}
    else:
        caption_data = {"start_caption": "", "end_caption": ""}
    
    # NEW: Load scheduled deletions
    if os.path.exists(DELETION_FILE):
        try:
            with open(DELETION_FILE, 'r') as f:
                scheduled_deletions = json.load(f)
            logger.info(f"✅ Loaded {len(scheduled_deletions)} scheduled deletions")
        except Exception as e:
            logger.error(f"❌ Error loading deletions: {e}")
            scheduled_deletions = {}
    else:
        scheduled_deletions = {}

def save_payloads():
    """Save payload data"""
    try:
        with open(PAYLOAD_FILE, 'w') as f:
            json.dump(payload_data, f, indent=2)
        logger.info("💾 Payloads saved")
    except Exception as e:
        logger.error(f"❌ Error saving payloads: {e}")

def save_access():
    """Save user access data"""
    try:
        with open(ACCESS_FILE, 'w') as f:
            json.dump(user_access, f, indent=2)
        logger.info("💾 Access data saved")
    except Exception as e:
        logger.error(f"❌ Error saving access: {e}")

def save_captions():
    """Save caption data"""
    try:
        with open(CAPTION_FILE, 'w') as f:
            json.dump(caption_data, f, indent=2)
        logger.info("💾 Captions saved")
    except Exception as e:
        logger.error(f"❌ Error saving captions: {e}")

def save_deletions():
    """NEW: Save scheduled deletions"""
    try:
        with open(DELETION_FILE, 'w') as f:
            json.dump(scheduled_deletions, f, indent=2)
        logger.info("💾 Deletions saved")
    except Exception as e:
        logger.error(f"❌ Error saving deletions: {e}")

async def check_and_delete_due_messages(bot):
    """NEW: Check and process any overdue deletions"""
    if not scheduled_deletions:
        return
    
    current_time = datetime.now(timezone.utc).timestamp()
    to_delete = []
    
    # Find overdue deletions
    for deletion_id, data in scheduled_deletions.items():
        if current_time >= data['delete_at']:
            to_delete.append(deletion_id)
    
    if not to_delete:
        return
    
    logger.info(f"⚡ Found {len(to_delete)} overdue deletions to process")
    
    # Process each overdue deletion
    for deletion_id in to_delete:
        data = scheduled_deletions[deletion_id]
        chat_id = data['chat_id']
        message_ids = data['message_ids']
        payload = data.get('payload', 'unknown')
        
        deleted = 0
        for msg_id in message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted += 1
            except Exception as e:
                logger.error(f"Could not delete message {msg_id}: {e}")
        
        logger.info(f"🔥 Deleted {deleted}/{len(message_ids)} messages from chat {chat_id} (payload: {payload[:8]})")
        
        # Send notification
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="🔥 **Files Auto-Deleted!**\n\nYour 1-hour timer expired.\n🔄 Click the link again to get fresh copies!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Could not send deletion notice: {e}")
        
        # Remove from scheduled deletions
        del scheduled_deletions[deletion_id]
    
    # Save updated deletions
    save_deletions()
    logger.info(f"✅ Processed {len(to_delete)} overdue deletions")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check for overdue deletions first
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /start command received from user {update.effective_user.id}")
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if context.args:
        payload = context.args[0]
        logger.info(f"📦 Payload requested: {payload}")
        
        if payload not in payload_data:
            await update.message.reply_text("❌ Invalid link!")
            return
        
        # Send start caption if exists
        start_msg = caption_data.get("start_caption", "")
        if start_msg:
            await update.message.reply_text(start_msg, parse_mode='Markdown')
        
        # Send warning
        await update.message.reply_text(
            f"⏰ **IMPORTANT: 1 HOUR AUTO-DELETE!**\n\n"
            f"📦 Sending {len(payload_data[payload]['files'])} files...\n"
            f"⚠️ **Files will be DELETED after 1 hour!**\n"
            f"💾 Forward them to Saved Messages NOW!",
            parse_mode='Markdown'
        )
        
        # Send all files
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
        
        # Send end caption or default message
        end_msg = caption_data.get("end_caption", "")
        if end_msg:
            await update.message.reply_text(end_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(
                f"✅ **{success_count} files sent!**\n\n"
                f"⚠️ **URGENT:** Forward to Saved Messages NOW!\n"
                f"🔥 Auto-delete in 60 minutes!\n"
                f"🔄 Click link again after deletion for fresh copies.",
                parse_mode='Markdown'
            )
        
        # NEW: Schedule deletion using UTC timestamp
        deletion_id = f"{chat_id}_{int(time.time())}_{secrets.token_hex(4)}"
        delete_at = datetime.now(timezone.utc).timestamp() + 3600  # 1 hour from now in UTC
        
        scheduled_deletions[deletion_id] = {
            'chat_id': chat_id,
            'message_ids': sent_message_ids,
            'delete_at': delete_at,
            'payload': payload,
            'scheduled_date': datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        }
        save_deletions()
        
        logger.info(f"⏰ Scheduled deletion {deletion_id} for {datetime.fromtimestamp(delete_at, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Track access
        if payload not in user_access:
            user_access[payload] = {}
        user_access[payload][str(user_id)] = time.time()
        save_access()
        
        logger.info(f"✅ User {user_id} accessed payload {payload[:8]}")
        
    else:
        if user_id == ADMIN_ID:
            logger.info("👑 Admin accessed /start")
            await update.message.reply_text(
                "👋 **Welcome Admin!**\n\n"
                "**Commands:**\n"
                "• `/startp <name>` - Start collecting\n"
                "• `/stopp` - Finish & get link\n"
                "• `/setcaption` - Set messages\n"
                "• `/status` - View payloads\n"
                "• `/listpayloads` - List all\n"
                "• `/deletepayload <code>` - Delete one\n"
                "• `/pending` - View scheduled deletions\n"
                "• `/checkdeletions` - Process overdue deletions",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("👋 Welcome! Send a valid link to access files.")

async def start_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /startp command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /startp <name>\nExample: /startp movies")
        return
    
    payload_name = ' '.join(context.args)
    admin_sessions[user_id] = {"payload": payload_name, "files": []}
    
    logger.info(f"✅ Started payload collection: {payload_name}")
    
    await update.message.reply_text(
        f"📁 **Started:** {payload_name}\n\n"
        f"Forward files now. Send /stopp when done.",
        parse_mode='Markdown'
    )

async def stop_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /stopp command received")
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
    
    logger.info(f"✅ Payload created: {unique_payload} with {len(session['files'])} files")
    
    del admin_sessions[user_id]
    
    await update.message.reply_text(
        f"✅ **Collection Created!**\n\n"
        f"📦 Name: {session['payload']}\n"
        f"📄 Files: {len(session['files'])}\n"
        f"🔥 Auto-delete: 1 hour after sending\n"
        f"🔄 Reusable: Users can click again\n"
        f"🔑 Code: `{unique_payload}`\n\n"
        f"🔗 **Share Link:**\n`{share_link}`",
        parse_mode='Markdown'
    )

async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /setcaption command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text(
        "📝 **Set Captions**\n\n"
        "Reply with:\n"
        "`START: your message`\n"
        "`END: your message`\n\n"
        "Example:\n"
        "`START: Welcome!`\n"
        "`END: Forward immediately!`\n\n"
        "Send 'CLEAR' to remove.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /status command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not payload_data:
        await update.message.reply_text("📊 No payloads.")
        return
    
    status_text = f"📊 **Payloads:** {len(payload_data)}\n\n"
    
    for payload, data in list(payload_data.items())[:10]:
        access_count = len(user_access.get(payload, {}))
        status_text += f"• **{data.get('name', 'Unnamed')}**\n"
        status_text += f"  Files: {len(data['files'])} | Users: {access_count}\n"
        status_text += f"  Code: `{payload[:12]}...`\n\n"
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def list_payloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /listpayloads command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not payload_data:
        await update.message.reply_text("📊 No payloads.")
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
    # NEW: Check deletions
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /deletepayload command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /deletepayload <code>")
        return
    
    payload = context.args[0]
    
    if payload not in payload_data:
        await update.message.reply_text("❌ Not found!")
        return
    
    name = payload_data[payload].get('name', 'Unnamed')
    del payload_data[payload]
    
    if payload in user_access:
        del user_access[payload]
    
    save_payloads()
    save_access()
    
    await update.message.reply_text(f"✅ Deleted: **{name}**", parse_mode='Markdown')

async def pending_deletions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW: View all scheduled deletions"""
    # Check deletions first
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /pending command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not scheduled_deletions:
        await update.message.reply_text("📊 No pending deletions.")
        return
    
    current_time = datetime.now(timezone.utc).timestamp()
    pending_text = f"⏰ **Scheduled Deletions:** {len(scheduled_deletions)}\n\n"
    
    for deletion_id, data in list(scheduled_deletions.items())[:20]:
        delete_at = data['delete_at']
        time_left = int((delete_at - current_time) / 60)  # Minutes left
        payload = data.get('payload', 'unknown')[:8]
        chat_id = data['chat_id']
        num_files = len(data['message_ids'])
        
        status = "⏳ Pending" if time_left > 0 else "⚡ OVERDUE"
        
        pending_text += f"• **Chat {chat_id}** | Payload: `{payload}`\n"
        pending_text += f"  Files: {num_files} | {status}\n"
        pending_text += f"  Time: {time_left} min\n\n"
    
    await update.message.reply_text(pending_text, parse_mode='Markdown')

async def check_deletions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW: Manually trigger deletion check"""
    logger.info(f"🎯 /checkdeletions command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text("⚡ Checking for overdue deletions...")
    
    before_count = len(scheduled_deletions)
    await check_and_delete_due_messages(context.bot)
    after_count = len(scheduled_deletions)
    
    processed = before_count - after_count
    
    if processed > 0:
        await update.message.reply_text(
            f"✅ Processed {processed} overdue deletion(s)!\n\n"
            f"Remaining: {after_count}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"✅ All clear! No overdue deletions.\n\nPending: {after_count}")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NEW: Check deletions on every message
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"📨 Message received from user {update.effective_user.id}")
    user_id = update.effective_user.id
    
    # Caption setting
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
                parts = text.split('START:')
                if len(parts) > 1:
                    start = parts[1].split('END:')[0].strip()
                    caption_data["start_caption"] = start
            
            if 'END:' in text:
                parts = text.split('END:')
                if len(parts) > 1:
                    caption_data["end_caption"] = parts[1].strip()
            
            save_captions()
            await update.message.reply_text("✅ Captions updated!")
            return
    
    # File collection
    if user_id == ADMIN_ID and user_id in admin_sessions:
        message_id = update.message.message_id
        admin_sessions[user_id]["files"].append(message_id)
        count = len(admin_sessions[user_id]["files"])
        logger.info(f"✅ File #{count} added to collection")
        await update.message.reply_text(f"✅ File #{count}")

def keep_alive_sync():
    """Keep the service alive by pinging itself every 10 minutes"""
    while True:
        time.sleep(600)  # 10 minutes
        try:
            if WEBHOOK_URL:
                requests.get(f"{WEBHOOK_URL}/health", timeout=5)
                logger.info("💓 Keep-alive ping sent")
        except Exception as e:
            logger.error(f"Keep-alive ping failed: {e}")

# Flask routes
@app.route('/')
def index():
    return "Bot running! 🚀", 200

@app.route('/health')
def health():
    return "OK", 200

@app.route('/<token>', methods=['POST'])
def webhook(token):
    """Handle incoming webhook updates"""
    
    # Verify token matches
    if token != BOT_TOKEN:
        logger.error(f"❌ Invalid token in webhook: {token}")
        return "Unauthorized", 401
    
    logger.info("🔔 Webhook received!")
    
    if not bot_app:
        logger.error("❌ Bot app not initialized!")
        return "Bot not ready", 503
    
    try:
        update_data = request.get_json(force=True)
        logger.info(f"📦 Update received")
        
        # Use bot_app.bot which is already initialized
        update = Update.de_json(update_data, bot_app.bot)
        
        # Process update synchronously
        import nest_asyncio
        nest_asyncio.apply()
        asyncio.run(bot_app.process_update(update))
        
        logger.info("✅ Update processed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return "Error", 500
    
    return "OK", 200

def run_flask():
    """Run Flask"""
    logger.info(f"🌐 Flask starting on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

async def notify_admin_restart():
    """Notify admin that bot restarted and check deletions"""
    try:
        await asyncio.sleep(2)  # Wait for bot to be ready
        
        # NEW: Check for overdue deletions on restart
        await check_and_delete_due_messages(bot_app.bot)
        
        await bot_app.bot.send_message(
            chat_id=ADMIN_ID,
            text="🔄 **Bot Restarted!**\n\nAll systems online and ready.\n\n**Commands:**\n• /startp <name>\n• /stopp\n• /setcaption\n• /status\n• /listpayloads\n• /deletepayload <code>\n• /pending - View deletions\n• /checkdeletions - Process overdue",
            parse_mode='Markdown'
        )
        logger.info("✅ Admin notified of restart")
    except Exception as e:
        logger.error(f"❌ Could not notify admin: {e}")

def main():
    """Main function"""
    global bot_app
    
    logger.info("=" * 60)
    logger.info("🚀 TELEGRAM BOT STARTING - ENHANCED VERSION")
    logger.info("=" * 60)
    logger.info(f"📝 BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'MISSING ❌'}")
    logger.info(f"👤 ADMIN_ID: {ADMIN_ID}")
    logger.info(f"🌐 WEBHOOK_URL: {WEBHOOK_URL if WEBHOOK_URL else 'MISSING ❌'}")
    logger.info(f"🔌 PORT: {PORT}")
    logger.info("=" * 60)
    
    if not WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not set - webhook will not work!")
    
    # Load data
    load_data()
    
    # Create application WITHOUT updater (webhook mode only)
    logger.info("🤖 Creating bot application...")
    bot_app = Application.builder().token(BOT_TOKEN).updater(None).build()
    
    # Add handlers
    logger.info("📌 Adding handlers...")
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("startp", start_payload))
    bot_app.add_handler(CommandHandler("stopp", stop_payload))
    bot_app.add_handler(CommandHandler("setcaption", set_caption))
    bot_app.add_handler(CommandHandler("status", status))
    bot_app.add_handler(CommandHandler("listpayloads", list_payloads))
    bot_app.add_handler(CommandHandler("deletepayload", delete_payload))
    bot_app.add_handler(CommandHandler("pending", pending_deletions))  # NEW
    bot_app.add_handler(CommandHandler("checkdeletions", check_deletions_command))  # NEW
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    # Initialize
    logger.info("⚙️ Initializing bot...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_app.initialize())
    
    # Set webhook
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        logger.info(f"🔗 Setting webhook: {webhook_url}")
        
        # DELETE old webhook first
        logger.info("🗑️ Deleting old webhook...")
        loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
        
        # Wait a moment
        time.sleep(2)
        
        # Set new webhook
        loop.run_until_complete(bot_app.bot.set_webhook(url=webhook_url))
        logger.info("✅ Webhook configured!")
        
        # Verify webhook
        webhook_info = loop.run_until_complete(bot_app.bot.get_webhook_info())
        logger.info(f"📡 Webhook URL: {webhook_info.url}")
        logger.info(f"📡 Pending updates: {webhook_info.pending_update_count}")
        
        # Notify admin (this will also check for overdue deletions)
        loop.run_until_complete(notify_admin_restart())
    
    # Start keep-alive thread
    if WEBHOOK_URL:
        keep_alive_thread = Thread(target=keep_alive_sync, daemon=True)
        keep_alive_thread.start()
        logger.info("💓 Keep-alive thread started")
    
    logger.info("=" * 60)
    logger.info("✅ BOT IS READY - ENHANCED VERSION!")
    logger.info("=" * 60)
    
    # Start Flask (blocks forever)
    run_flask()

if __name__ == "__main__":
    # Install nest_asyncio if not available
    try:
        import nest_asyncio
    except ImportError:
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nest_asyncio"])
        import nest_asyncio
    
    main()
