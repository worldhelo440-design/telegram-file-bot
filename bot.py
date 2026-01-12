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
import io

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8080))

# Storage files
PAYLOAD_FILE = "payload_data.json"
ACCESS_FILE = "user_access.json"
CAPTION_FILE = "caption_data.json"
DELETION_FILE = "scheduled_deletions.json"
BACKUP_IDS_FILE = "telegram_backup_ids.json"  # NEW: Store Telegram message IDs

payload_data = {}
user_access = {}
admin_sessions = {}
caption_data = {"start_caption": "", "end_caption": ""}
scheduled_deletions = {}
telegram_backup_ids = {}  # NEW: {file_type: message_id}

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

def load_backup_ids():
    """Load Telegram backup message IDs"""
    global telegram_backup_ids
    if os.path.exists(BACKUP_IDS_FILE):
        try:
            with open(BACKUP_IDS_FILE, 'r') as f:
                telegram_backup_ids = json.load(f)
            logger.info(f"✅ Loaded backup IDs: {telegram_backup_ids}")
        except Exception as e:
            logger.error(f"❌ Error loading backup IDs: {e}")
            telegram_backup_ids = {}
    else:
        telegram_backup_ids = {}

def save_backup_ids():
    """Save Telegram backup message IDs"""
    try:
        with open(BACKUP_IDS_FILE, 'w') as f:
            json.dump(telegram_backup_ids, f, indent=2)
        logger.info("💾 Backup IDs saved")
    except Exception as e:
        logger.error(f"❌ Error saving backup IDs: {e}")

async def backup_to_telegram(bot, file_type, data, filename):
    """
    Upload JSON data to Telegram as backup
    file_type: 'payload', 'access', 'caption', 'deletion'
    """
    try:
        # Convert data to JSON string
        json_str = json.dumps(data, indent=2)
        json_bytes = json_str.encode('utf-8')
        
        # Create file-like object
        file_obj = io.BytesIO(json_bytes)
        file_obj.name = filename
        
        # Send to admin
        sent_message = await bot.send_document(
            chat_id=ADMIN_ID,
            document=file_obj,
            caption=f"☁️ **Backup: {file_type.upper()}**\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n📦 Records: {len(data) if isinstance(data, dict) else 'N/A'}",
            parse_mode='Markdown'
        )
        
        # Store message ID
        telegram_backup_ids[file_type] = sent_message.message_id
        save_backup_ids()
        
        logger.info(f"☁️ Backed up {file_type} to Telegram (msg_id: {sent_message.message_id})")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to backup {file_type} to Telegram: {e}")
        return False

async def restore_from_telegram(bot, file_type):
    """
    Download and restore JSON data from Telegram
    Returns: (success, data)
    """
    try:
        if file_type not in telegram_backup_ids:
            logger.warning(f"⚠️ No backup ID found for {file_type}")
            return False, None
        
        message_id = telegram_backup_ids[file_type]
        logger.info(f"📥 Restoring {file_type} from Telegram (msg_id: {message_id})")
        
        # Get the message
        # Note: We need to get file from admin's chat
        file = await bot.get_file(file_id=f"get_from_message_{message_id}")
        
        # Download file
        file_bytes = await file.download_as_bytearray()
        json_str = file_bytes.decode('utf-8')
        data = json.loads(json_str)
        
        logger.info(f"✅ Restored {file_type} from Telegram")
        return True, data
    except Exception as e:
        logger.error(f"❌ Failed to restore {file_type} from Telegram: {e}")
        return False, None

def load_data():
    """Load all data from files"""
    global payload_data, user_access, caption_data, scheduled_deletions
    
    if os.path.exists(PAYLOAD_FILE):
        try:
            with open(PAYLOAD_FILE, 'r') as f:
                payload_data = json.load(f)
            logger.info(f"✅ Loaded {len(payload_data)} payloads from local file")
        except Exception as e:
            logger.error(f"❌ Error loading payloads: {e}")
            payload_data = {}
    else:
        payload_data = {}
    
    if os.path.exists(ACCESS_FILE):
        try:
            with open(ACCESS_FILE, 'r') as f:
                user_access = json.load(f)
            logger.info(f"✅ Loaded user access data from local file")
        except Exception as e:
            logger.error(f"❌ Error loading access data: {e}")
            user_access = {}
    else:
        user_access = {}
    
    if os.path.exists(CAPTION_FILE):
        try:
            with open(CAPTION_FILE, 'r') as f:
                caption_data = json.load(f)
            logger.info(f"✅ Loaded captions from local file")
        except Exception as e:
            logger.error(f"❌ Error loading captions: {e}")
            caption_data = {"start_caption": "", "end_caption": ""}
    else:
        caption_data = {"start_caption": "", "end_caption": ""}
    
    if os.path.exists(DELETION_FILE):
        try:
            with open(DELETION_FILE, 'r') as f:
                scheduled_deletions = json.load(f)
            logger.info(f"✅ Loaded {len(scheduled_deletions)} scheduled deletions from local file")
        except Exception as e:
            logger.error(f"❌ Error loading deletions: {e}")
            scheduled_deletions = {}
    else:
        scheduled_deletions = {}

async def load_data_from_telegram(bot):
    """Try to load data from Telegram backups first"""
    global payload_data, user_access, caption_data, scheduled_deletions
    
    logger.info("☁️ Attempting to restore from Telegram backups...")
    
    restored_count = 0
    
    # Try to restore each file type
    success, data = await restore_from_telegram(bot, 'payload')
    if success and data:
        payload_data = data
        save_payloads()
        restored_count += 1
        logger.info(f"✅ Restored {len(payload_data)} payloads from Telegram")
    
    success, data = await restore_from_telegram(bot, 'access')
    if success and data:
        user_access = data
        save_access()
        restored_count += 1
        logger.info(f"✅ Restored access data from Telegram")
    
    success, data = await restore_from_telegram(bot, 'caption')
    if success and data:
        caption_data = data
        save_captions()
        restored_count += 1
        logger.info(f"✅ Restored captions from Telegram")
    
    success, data = await restore_from_telegram(bot, 'deletion')
    if success and data:
        scheduled_deletions = data
        save_deletions()
        restored_count += 1
        logger.info(f"✅ Restored {len(scheduled_deletions)} deletions from Telegram")
    
    if restored_count > 0:
        logger.info(f"🎉 Successfully restored {restored_count} file(s) from Telegram!")
        return True
    else:
        logger.warning("⚠️ No data restored from Telegram, using local files")
        return False

def save_payloads():
    """Save payload data"""
    try:
        with open(PAYLOAD_FILE, 'w') as f:
            json.dump(payload_data, f, indent=2)
        logger.info("💾 Payloads saved locally")
    except Exception as e:
        logger.error(f"❌ Error saving payloads: {e}")

def save_access():
    """Save user access data"""
    try:
        with open(ACCESS_FILE, 'w') as f:
            json.dump(user_access, f, indent=2)
        logger.info("💾 Access data saved locally")
    except Exception as e:
        logger.error(f"❌ Error saving access: {e}")

def save_captions():
    """Save caption data"""
    try:
        with open(CAPTION_FILE, 'w') as f:
            json.dump(caption_data, f, indent=2)
        logger.info("💾 Captions saved locally")
    except Exception as e:
        logger.error(f"❌ Error saving captions: {e}")

def save_deletions():
    """Save scheduled deletions"""
    try:
        with open(DELETION_FILE, 'w') as f:
            json.dump(scheduled_deletions, f, indent=2)
        logger.info("💾 Deletions saved locally")
    except Exception as e:
        logger.error(f"❌ Error saving deletions: {e}")

async def check_and_delete_due_messages(bot):
    """Check and process any overdue deletions"""
    if not scheduled_deletions:
        return
    
    current_time = datetime.now(timezone.utc).timestamp()
    to_delete = []
    
    for deletion_id, data in scheduled_deletions.items():
        if current_time >= data['delete_at']:
            to_delete.append(deletion_id)
    
    if not to_delete:
        return
    
    logger.info(f"⚡ Found {len(to_delete)} overdue deletions to process")
    
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
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="🔥 **Files Auto-Deleted!**\n\nYour 1-hour timer expired.\n🔄 Click the link again to get fresh copies!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Could not send deletion notice: {e}")
        
        del scheduled_deletions[deletion_id]
    
    save_deletions()
    # Backup deletions to Telegram
    # await backup_to_telegram(bot_app.bot, 'deletion', scheduled_deletions, 'scheduled_deletions.json')
    logger.info(f"✅ Processed {len(to_delete)} overdue deletions")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        start_msg = caption_data.get("start_caption", "")
        if start_msg:
            await update.message.reply_text(start_msg, parse_mode='Markdown')
        
        await update.message.reply_text(
            f"⏰ **IMPORTANT: 1 HOUR AUTO-DELETE!**\n\n"
            f"📦 Sending {len(payload_data[payload]['files'])} files...\n"
            f"⚠️ **Files will be DELETED after 1 hour!**\n"
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
                f"⚠️ **URGENT:** Forward to Saved Messages NOW!\n"
                f"🔥 Auto-delete in 60 minutes!\n"
                f"🔄 Click link again after deletion for fresh copies.",
                parse_mode='Markdown'
            )
        
        deletion_id = f"{chat_id}_{int(time.time())}_{secrets.token_hex(4)}"
        delete_at = datetime.now(timezone.utc).timestamp() + 3600
        
        scheduled_deletions[deletion_id] = {
            'chat_id': chat_id,
            'message_ids': sent_message_ids,
            'delete_at': delete_at,
            'payload': payload,
            'scheduled_date': datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        }
        save_deletions()
        
        logger.info(f"⏰ Scheduled deletion {deletion_id}")
        
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
                "• `/checkdeletions` - Process overdue\n\n"
                "**☁️ Cloud Backup:**\n"
                "• `/backupnow` - Backup all to Telegram\n"
                "• `/restorefromcloud` - Restore from Telegram\n"
                "• `/downloadjson` - Get current JSON\n"
                "• `/uploadjson` - Upload JSON (reply to file)",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("👋 Welcome! Send a valid link to access files.")

async def start_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # AUTO-BACKUP TO TELEGRAM
    await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json')
    
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
        f"🔑 Code: `{unique_payload}`\n"
        f"☁️ Backed up to Telegram ✅\n\n"
        f"🔗 **Share Link:**\n`{share_link}`",
        parse_mode='Markdown'
    )

async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # Auto-backup after deletion
    await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json')
    
    await update.message.reply_text(f"✅ Deleted: **{name}**\n☁️ Backup updated!", parse_mode='Markdown')

async def pending_deletions(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        time_left = int((delete_at - current_time) / 60)
        payload = data.get('payload', 'unknown')[:8]
        chat_id = data['chat_id']
        num_files = len(data['message_ids'])
        
        status = "⏳ Pending" if time_left > 0 else "⚡ OVERDUE"
        
        pending_text += f"• **Chat {chat_id}** | Payload: `{payload}`\n"
        pending_text += f"  Files: {num_files} | {status}\n"
        pending_text += f"  Time: {time_left} min\n\n"
    
    await update.message.reply_text(pending_text, parse_mode='Markdown')

async def check_deletions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually backup all data to Telegram"""
    logger.info(f"🎯 /backupnow command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text("☁️ Starting backup to Telegram...")
    
    success_count = 0
    
    if await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json'):
        success_count += 1
    
    if await backup_to_telegram(context.bot, 'access', user_access, 'user_access.json'):
        success_count += 1
    
    if await backup_to_telegram(context.bot, 'caption', caption_data, 'caption_data.json'):
        success_count += 1
    
    if await backup_to_telegram(context.bot, 'deletion', scheduled_deletions, 'scheduled_deletions.json'):
        success_count += 1
    
    await update.message.reply_text(
        f"✅ **Backup Complete!**\n\n"
        f"📤 Uploaded {success_count}/4 files to Telegram\n"
        f"☁️ Your data is now safe in the cloud!",
        parse_mode='Markdown'
    )

async def restore_from_cloud(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually restore all data from Telegram"""
    logger.info(f"🎯 /restorefromcloud command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text("☁️ Restoring from Telegram backups...")
    
    restored = await load_data_from_telegram(context.bot)
    
    if restored:
        await update.message.reply_text(
            f"✅ **Restore Complete!**\n\n"
            f"📥 Data restored from Telegram cloud\n"
            f"📦 Payloads: {len(payload_data)}\n"
            f"⏰ Deletions: {len(scheduled_deletions)}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"⚠️ **No cloud backups found**\n\n"
            f"Using local files instead.\n"
            f"Use /backupnow to create cloud backups.",
            parse_mode='Markdown'
        )

async def download_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send current JSON files to admin"""
    logger.info(f"🎯 /downloadjson command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text("📥 Generating JSON files...")
    
    # Send payload data
    json_str = json.dumps(payload_data, indent=2)
    file_obj = io.BytesIO(json_str.encode('utf-8'))
    file_obj.name = 'payload_data.json'
    
    await context.bot.send_document(
        chat_id=ADMIN_ID,
        document=file_obj,
        caption=f"📦 **Payload Data**\n📊 Records: {len(payload_data)}",
        parse_mode='Markdown'
    )
    
    await update.message.reply_text("✅ JSON file sent!")




async def upload_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload and set payload data from JSON file"""
    logger.info(f"🎯 /uploadjson command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text(
        "📤 Upload JSON File\n\n"
        "Send your payload_data.json file now.\n"
        "I'll process it automatically.\n\n"
        "Supported files:\n"
        "• payload_data.json\n"
        "• caption_data.json\n"
        "• user_access.json\n"
        "• scheduled_deletions.json",
        parse_mode=None
    )

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"📨 Message received from user {update.effective_user.id}")
    user_id = update.effective_user.id
    
    # Handle JSON file upload - AUTOMATIC DETECTION
    if update.message.document and user_id == ADMIN_ID:
        doc = update.message.document
        
        # Check if it's a JSON file
        if doc.file_name and doc.file_name.endswith('.json'):
            logger.info(f"📄 JSON file received: {doc.file_name}")
            
            # Show processing message
            processing_msg = await update.message.reply_text("⏳ Processing JSON file...")
            
            try:
                # Download and parse JSON
                file = await context.bot.get_file(doc.file_id)
                file_bytes = await file.download_as_bytearray()
                json_str = file_bytes.decode('utf-8')
                new_data = json.loads(json_str)
                
                # Determine file type and update accordingly
                if 'name' in str(new_data) and 'files' in str(new_data):
                    # It's payload data
                    global payload_data
                    old_count = len(payload_data)
                    payload_data = new_data
                    save_payloads()
                    
                    # Backup to Telegram
                    await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json')
                    
                    logger.info(f"✅ Loaded {len(payload_data)} payloads from uploaded file")
                    
                    # Delete processing message
                    await processing_msg.delete()
                    
                    # Send success message
                    await update.message.reply_text(
                        f"✅ Payload Data Uploaded!\n\n"
                        f"📦 Previous: {old_count} payloads\n"
                        f"📦 New: {len(payload_data)} payloads\n"
                        f"☁️ Backed up to Telegram cloud\n\n"
                        f"🚀 Bot is ready to use!",
                        parse_mode=None
                    )
                    return
                
                elif 'start_caption' in new_data or 'end_caption' in new_data:
                    # It's caption data
                    global caption_data
                    caption_data = new_data
                    save_captions()
                    await backup_to_telegram(context.bot, 'caption', caption_data, 'caption_data.json')
                    
                    await processing_msg.delete()
                    await update.message.reply_text(
                        "✅ Caption Data Uploaded!\n\n"
                        "☁️ Backed up to cloud",
                        parse_mode=None
                    )
                    return
                
                else:
                    # Unknown JSON format
                    await processing_msg.delete()
                    await update.message.reply_text(
                        "⚠️ Unknown JSON format!\n\n"
                        "Expected: payload_data.json or caption_data.json"
                    )
                    return
                    
            except json.JSONDecodeError as e:
                logger.error(f"❌ Invalid JSON: {e}")
                await processing_msg.delete()
                await update.message.reply_text(
                    f"❌ Invalid JSON file!\n\n"
                    f"Error: {str(e)}"
                )
                return
            except Exception as e:
                logger.error(f"❌ Upload error: {e}")
                await processing_msg.delete()
                await update.message.reply_text(f"❌ Error: {str(e)}")
                return
    
    # Caption setting
    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text
        if reply_text and "Set Captions" in reply_text and user_id == ADMIN_ID:
            text = update.message.text
            
            if text.upper() == 'CLEAR':
                caption_data["start_caption"] = ""
                caption_data["end_caption"] = ""
                save_captions()
                await backup_to_telegram(context.bot, 'caption', caption_data, 'caption_data.json')
                await update.message.reply_text("✅ Captions cleared and backed up!")
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
            await backup_to_telegram(context.bot, 'caption', caption_data, 'caption_data.json')
            await update.message.reply_text("✅ Captions updated and backed up!")
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
        
        update = Update.de_json(update_data, bot_app.bot)
        
        # Use nest_asyncio to allow nested event loops
        import nest_asyncio
        nest_asyncio.apply()
        
        # Create a new event loop for this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot_app.process_update(update))
        finally:
            loop.close()
        
        logger.info("✅ Update processed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return "Error", 500
    
    return "OK", 200

def run_flask():
    """Run Flask"""
    logger.info(f"🌐 Flask starting on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


        if has_data:
            # Bot has existing data
            message = (
                "🔄 Bot Restarted!\n\n"
                f"📦 Current payloads: {len(payload_data)}\n"
                f"⏰ Pending deletions: {len(scheduled_deletions)}\n\n"
                "All systems online and ready.\n\n"
                "Commands:\n"
                "• /startp - Start collection\n"
                "• /stopp - Finish collection\n"
                "• /status - View payloads\n"
                "• /listpayloads - List all\n"
                "• /uploadjson - Upload new data\n"
                "• /backupnow - Backup to cloud\n"
                "• /downloadjson - Download current data"
            )
        else:
            # No data found - ask for upload
            message = (
                "🔄 Bot Restarted!\n\n"
                "⚠️ No payload data found!\n\n"
                "📤 UPLOAD YOUR JSON FILE NOW\n\n"
                "Send your payload_data.json file\n"
                "within the next 60 seconds.\n\n"
                "I'll process it automatically."
            )
        
        await bot_app.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode=None
        )
        logger.info("✅ Admin notified of restart")
        
        # If no data, send a follow-up reminder after 30 seconds
        if not has_data:
            await asyncio.sleep(30)
            
            # Check again if data was uploaded
            if len(payload_data) == 0:
                await bot_app.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⏰ 30 seconds left!\n\n"
                        "📤 Send payload_data.json file now\n"
                        "or use /downloadjson from old bot\n\n"
                        "Bot is waiting..."
                    ),
                    parse_mode=None
                )
                logger.info("⏰ Sent upload reminder")
                
    except Exception as e:
        logger.error(f"❌ Could not notify admin: {e}")



def main():
    """Main function"""
    global bot_app
    
    logger.info("=" * 60)
    logger.info("🚀 TELEGRAM BOT STARTING - CLOUD BACKUP VERSION")
    logger.info("=" * 60)
    logger.info(f"📝 BOT_TOKEN: {'SET ✅' if BOT_TOKEN else 'MISSING ❌'}")
    logger.info(f"👤 ADMIN_ID: {ADMIN_ID}")
    logger.info(f"🌐 WEBHOOK_URL: {WEBHOOK_URL if WEBHOOK_URL else 'MISSING ❌'}")
    logger.info(f"🔌 PORT: {PORT}")
    logger.info("=" * 60)
    
    if not WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not set - webhook will not work!")
    
    # Load backup IDs first
    load_backup_ids()
    
    # Load local data
    load_data()
    
    # Create application
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
    bot_app.add_handler(CommandHandler("pending", pending_deletions))
    bot_app.add_handler(CommandHandler("checkdeletions", check_deletions_command))
    bot_app.add_handler(CommandHandler("backupnow", backup_now))
    bot_app.add_handler(CommandHandler("restorefromcloud", restore_from_cloud))
    bot_app.add_handler(CommandHandler("downloadjson", download_json))
    bot_app.add_handler(CommandHandler("uploadjson", upload_json))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    # Initialize
    logger.info("⚙️ Initializing bot...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_app.initialize())
    
    # Try to restore from Telegram cloud
    logger.info("☁️ Checking for cloud backups...")
    loop.run_until_complete(load_data_from_telegram(bot_app.bot))
    
    # Set webhook
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        logger.info(f"🔗 Setting webhook: {webhook_url}")
        
        logger.info("🗑️ Deleting old webhook...")
        loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
        
        time.sleep(2)
        
        loop.run_until_complete(bot_app.bot.set_webhook(url=webhook_url))
        logger.info("✅ Webhook configured!")
        
        webhook_info = loop.run_until_complete(bot_app.bot.get_webhook_info())
        logger.info(f"📡 Webhook URL: {webhook_info.url}")
        logger.info(f"📡 Pending updates: {webhook_info.pending_update_count}")
        
        loop.run_until_complete(notify_admin_restart())
    
    # Start keep-alive
    if WEBHOOK_URL:
        keep_alive_thread = Thread(target=keep_alive_sync, daemon=True)
        keep_alive_thread.start()
        logger.info("💓 Keep-alive thread started")
    
    logger.info("=" * 60)
    logger.info("✅ BOT IS READY - CLOUD BACKUP ENABLED!")
    logger.info("=" * 60)
    
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










