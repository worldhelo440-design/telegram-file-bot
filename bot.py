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
BACKUP_IDS_FILE = "telegram_backup_ids.json"

payload_data = {}
user_access = {}
admin_sessions = {}
caption_data = {"start_caption": "", "end_caption": ""}
scheduled_deletions = {}
telegram_backup_ids = {}

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Bot application and event loop
bot_app = None
bot_loop = None

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
    """Upload JSON data to Telegram as backup"""
    try:
        json_str = json.dumps(data, indent=2)
        json_bytes = json_str.encode('utf-8')
        file_obj = io.BytesIO(json_bytes)
        file_obj.name = filename
        
        sent_message = await bot.send_document(
            chat_id=ADMIN_ID,
            document=file_obj,
            caption=f"☁️ Backup: {file_type.upper()}\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n📦 Records: {len(data) if isinstance(data, dict) else 'N/A'}",
            parse_mode=None
        )
        
        telegram_backup_ids[file_type] = sent_message.message_id
        save_backup_ids()
        
        logger.info(f"☁️ Backed up {file_type} to Telegram (msg_id: {sent_message.message_id})")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to backup {file_type} to Telegram: {e}")
        return False

async def restore_from_telegram(bot, file_type):
    """Download and restore JSON data from Telegram"""
    try:
        if file_type not in telegram_backup_ids:
            logger.warning(f"⚠️ No backup ID found for {file_type}")
            return False, None
        
        message_id = telegram_backup_ids[file_type]
        logger.info(f"📥 Restoring {file_type} from Telegram (msg_id: {message_id})")
        
        file = await bot.get_file(file_id=f"get_from_message_{message_id}")
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
                text="🔥 Files Auto-Deleted!\n\nYour 1-hour timer expired.\n🔄 Click the link again to get fresh copies!",
                parse_mode=None
            )
        except Exception as e:
            logger.error(f"Could not send deletion notice: {e}")
        
        del scheduled_deletions[deletion_id]
    
    save_deletions()
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
            await update.message.reply_text(start_msg, parse_mode=None)
        
        await update.message.reply_text(
            f"⏰ IMPORTANT: 1 HOUR AUTO-DELETE!\n\n"
            f"📦 Sending {len(payload_data[payload]['files'])} files...\n"
            f"⚠️ Files will be DELETED after 1 hour!\n"
            f"💾 Forward them to Saved Messages NOW!",
            parse_mode=None
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
            await update.message.reply_text(end_msg, parse_mode=None)
        else:
            await update.message.reply_text(
                f"✅ {success_count} files sent!\n\n"
                f"⚠️ URGENT: Forward to Saved Messages NOW!\n"
                f"🔥 Auto-delete in 60 minutes!\n"
                f"🔄 Click link again after deletion for fresh copies.",
                parse_mode=None
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
                "👋 Welcome Admin!\n\n"
                "Commands:\n"
                "• /startp - Start collecting\n"
                "• /stopp - Finish and get link\n"
                "• /setcaption - Set messages\n"
                "• /status - View payloads\n"
                "• /listpayloads - List all\n"
                "• /deletepayload - Delete one\n"
                "• /pending - View scheduled deletions\n"
                "• /checkdeletions - Process overdue\n\n"
                "Cloud Backup:\n"
                "• /backupnow - Backup all to Telegram\n"
                "• /restorefromcloud - Restore from Telegram\n"
                "• /downloadjson - Get current JSON\n"
                "• /uploadjson - Upload JSON",
                parse_mode=None
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
        f"📁 Started: {payload_name}\n\n"
        f"Forward files now. Send /stopp when done.",
        parse_mode=None
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
    await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json')
    
    bot_info = await context.bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={unique_payload}"
    
    logger.info(f"✅ Payload created: {unique_payload} with {len(session['files'])} files")
    
    del admin_sessions[user_id]
    
    await update.message.reply_text(
        f"✅ Collection Created!\n\n"
        f"📦 Name: {session['payload']}\n"
        f"📄 Files: {len(session['files'])}\n"
        f"🔥 Auto-delete: 1 hour after sending\n"
        f"🔄 Reusable: Users can click again\n"
        f"🔑 Code: {unique_payload}\n"
        f"☁️ Backed up to Telegram ✅\n\n"
        f"🔗 Share Link:\n{share_link}",
        parse_mode=None
    )

async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_and_delete_due_messages(context.bot)
    
    logger.info(f"🎯 /setcaption command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text(
        "📝 Set Captions\n\n"
        "Reply with:\n"
        "START: your message\n"
        "END: your message\n\n"
        "Example:\n"
        "START: Welcome!\n"
        "END: Forward immediately!\n\n"
        "Send 'CLEAR' to remove.",
        parse_mode=None
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
    
    status_text = f"📊 Payloads: {len(payload_data)}\n\n"
    
    for payload, data in list(payload_data.items())[:10]:
        access_count = len(user_access.get(payload, {}))
        status_text += f"• {data.get('name', 'Unnamed')}\n"
        status_text += f"  Files: {len(data['files'])} | Users: {access_count}\n"
        status_text += f"  Code: {payload[:12]}...\n\n"
    
    await update.message.reply_text(status_text, parse_mode=None)

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
    
    list_text = "📋 All Payloads:\n\n"
    
    for i, (payload, data) in enumerate(payload_data.items(), 1):
        access_count = len(user_access.get(payload, {}))
        created = data.get('created_date', 'Unknown')
        list_text += f"{i}. {data.get('name', 'Unnamed')}\n"
        list_text += f"   Created: {created}\n"
        list_text += f"   Files: {len(data['files'])} | Users: {access_count}\n"
        list_text += f"   Code: {payload}\n\n"
    
    await update.message.reply_text(list_text, parse_mode=None)

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
    await backup_to_telegram(context.bot, 'payload', payload_data, 'payload_data.json')
    
    await update.message.reply_text(f"✅ Deleted: {name}\n☁️ Backup updated!", parse_mode=None)

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
    pending_text = f"⏰ Scheduled Deletions: {len(scheduled_deletions)}\n\n"
    
    for deletion_id, data in list(scheduled_deletions.items())[:20]:
        delete_at = data['delete_at']
        time_left = int((delete_at - current_time) / 60)
        payload = data.get('payload', 'unknown')[:8]
        chat_id = data['chat_id']
        num_files = len(data['message_ids'])
        
        status = "⏳ Pending" if time_left > 0 else "⚡ OVERDUE"
        
        pending_text += f"• Chat {chat_id} | Payload: {payload}\n"
        pending_text += f"  Files: {num_files} | {status}\n"
        pending_text += f"  Time: {time_left} min\n\n"
    
    await update.message.reply_text(pending_text, parse_mode=None)

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
            f"✅ Processed {processed} overdue deletion(s)!\n\nRemaining: {after_count}",
            parse_mode=None
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
        f"✅ Backup Complete!\n\n"
        f"📤 Uploaded {success_count}/4 files to Telegram\n"
        f"☁️ Your data is now safe in the cloud!",
        parse_mode=None
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
            f"✅ Restore Complete!\n\n"
            f"📥 Data restored from Telegram cloud\n"
            f"📦 Payloads: {len(payload_data)}\n"
            f"⏰ Deletions: {len(scheduled_deletions)}",
            parse_mode=None
        )
    else:
        await update.message.reply_text(
            f"⚠️ No cloud backups found\n\n"
            f"Using local files instead.\n"
            f"Use /backupnow to create cloud backups.",
            parse_mode=None
        )

async def download_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send current JSON files to admin"""
    logger.info(f"🎯 /downloadjson command received")
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    await update.message.reply_text("📥 Generating JSON files...")
    
    json_str = json.dumps(payload_data, indent=2)
    file_obj = io.BytesIO(json_str.encode('utf-8'))
    file_obj.name = 'payload_data.json'
    
    await context.bot.send_document(
        chat_id=ADMIN_ID,
        document=file_obj,
        caption=f"📦 Payload Data\n📊 Records: {len(payload_data)}",
        parse_mode=None
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
        "• caption_data.json",
        parse_mode=None
    )

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await
