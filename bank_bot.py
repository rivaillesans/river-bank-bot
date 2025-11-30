# bank_bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import asyncio
import json
from datetime import datetime

# Config
import os
BOT_TOKEN = os.environ.get('BOT_TOKEN')
OWNER_ID = 1768830793
SPREADSHEET_NAME = "RBank"
SERVICE_ACCOUNT_FILE = "tg-project-01-b8db80779692.json"
CURRENCY = "₱"

# Setup Google Sheets using environment variables
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Get Google Sheets credentials from environment variables
google_creds_json = os.environ.get('GOOGLE_CREDS_JSON')
if google_creds_json:
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    # Fallback to file (for local development only)
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)

client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).sheet1

# Load admins, co-owners, log channel, and connected groups
try:
    with open("admins.json", "r") as f:
        ADMINS = json.load(f)
except:
    ADMINS = []

try:
    with open("co_owners.json", "r") as f:
        CO_OWNERS = json.load(f)
except:
    CO_OWNERS = []

try:
    with open("config.json", "r") as f:
        config = json.load(f)
        LOG_CHANNEL = config.get("log_channel")
        CONNECTED_GROUPS = config.get("connected_groups", [])
except:
    LOG_CHANNEL = None
    CONNECTED_GROUPS = []

def save_admins():
    with open("admins.json", "w") as f:
        json.dump(ADMINS, f)

def save_co_owners():
    with open("co_owners.json", "w") as f:
        json.dump(CO_OWNERS, f)

def save_config():
    config = {
        "log_channel": LOG_CHANNEL,
        "connected_groups": CONNECTED_GROUPS
    }
    with open("config.json", "w") as f:
        json.dump(config, f)

def get_all_accounts():
    return sheet.get_all_records()

def find_user_row(user_id):
    """Find user row by ID, return None if not found"""
    try:
        data = sheet.col_values(1)
        for i, val in enumerate(data):
            if str(val) == str(user_id):
                return i + 1
        return None
    except:
        return None

def delete_user_account(user_id):
    """Delete user account by ID"""
    try:
        row = find_user_row(user_id)
        if row:
            sheet.delete_rows(row)
            return True
        return False
    except:
        return False

def format_datetime():
    return datetime.now().strftime("%m-%d-%Y, %I:%M %p")

def can_modify(user):
    """Check if user is owner or admin"""
    return user.id == OWNER_ID or (user.username and user.username in ADMINS) or (user.username and user.username in CO_OWNERS)

def is_owner(user):
    """Check if user is owner"""
    return user.id == OWNER_ID

def is_co_owner(user):
    """Check if user is co-owner"""
    return user.username and user.username in CO_OWNERS

def is_manager(user):
    """Check if user is manager"""
    return user.username and user.username in ADMINS

def can_manage_users(user):
    """Check if user can manage other users (owner and co-owners)"""
    return user.id == OWNER_ID or (user.username and user.username in CO_OWNERS)

async def send_log(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send log message to log channel"""
    if LOG_CHANNEL:
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL,
                text=message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"Failed to send log: {e}")

# Transaction history storage (in real app, this would be in database)
TRANSACTION_HISTORY = {}

# Store message IDs for auto-delete functionality
BAL_MESSAGES = {}
INFOBANK_MESSAGES = {}

def add_transaction(user_id, amount, executor_id, executor_name, transaction_type="added"):
    """Add transaction to history"""
    if user_id not in TRANSACTION_HISTORY:
        TRANSACTION_HISTORY[user_id] = []
    
    TRANSACTION_HISTORY[user_id].append({
        "timestamp": format_datetime(),
        "amount": amount,
        "executor_id": executor_id,
        "executor_name": executor_name,
        "type": transaction_type
    })
    
    # Keep only last 10 transactions
    if len(TRANSACTION_HISTORY[user_id]) > 10:
        TRANSACTION_HISTORY[user_id] = TRANSACTION_HISTORY[user_id][-10:]

async def schedule_auto_delete(message, message_key, message_type="bal"):
    """Schedule auto-delete for message after 1 minute"""
    await asyncio.sleep(60)  # 1 minute
    
    # Check if message is still in the dictionary (not deleted by user action)
    if message_type == "bal" and message_key in BAL_MESSAGES:
        try:
            await message.delete()
            # Remove from tracking
            if message_key in BAL_MESSAGES:
                del BAL_MESSAGES[message_key]
        except:
            # Message already deleted, remove from tracking
            if message_key in BAL_MESSAGES:
                del BAL_MESSAGES[message_key]
    elif message_type == "infobank" and message_key in INFOBANK_MESSAGES:
        try:
            await message.delete()
            # Remove from tracking
            if message_key in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key]
        except:
            # Message already deleted, remove from tracking
            if message_key in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key]

async def setlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set log channel for bank activities"""
    user = update.effective_user
    
    # Check if user is owner or co-owner
    if not can_manage_users(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if message is sent in a channel
    if not update.message.chat.type == "channel":
        error_msg = await update.message.reply_text("Please use this command in the channel you want to set as log channel.")
        await asyncio.sleep(2)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Get channel info
    channel_id = update.message.chat.id
    channel_title = update.message.chat.title
    
    # Check if bot is admin in the channel
    try:
        chat_member = await context.bot.get_chat_member(channel_id, context.bot.id)
        if not chat_member.status in ["administrator", "creator"]:
            error_msg = await update.message.reply_text("❌ Bot must be an admin in this channel to set it as log channel.")
            await asyncio.sleep(2)
            try:
                await error_msg.delete()
                await update.message.delete()
            except:
                pass
            return
    except Exception as e:
        error_msg = await update.message.reply_text("❌ Cannot access channel information. Make sure bot is added as admin.")
        await asyncio.sleep(2)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Set log channel
    global LOG_CHANNEL
    LOG_CHANNEL = channel_id
    save_config()
    
    # Send success message
    success_msg = await update.message.reply_text(
        f"✅ <b>Log Channel Set</b>\n"
        f"• {channel_title} will now receive all bank activity logs\n"
        f"• Bot must remain admin in this channel\n"
        f"• Use /setlog again in another channel to change",
        parse_mode=ParseMode.HTML
    )
    
    # Send test log message
    await send_log(context, 
        f"📝 <b>Logging Started</b>\n"
        f"• This channel is now set as the log channel\n"
        f"• All bank activities will be logged here\n"
        f"• Date: {format_datetime()}"
    )
    
    # Delete messages after delay
    await asyncio.sleep(3)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect another group to use the same bank data"""
    user = update.effective_user
    
    # Check if user is owner or co-owner
    if not can_manage_users(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if message is sent in a group
    if update.message.chat.type not in ["group", "supergroup"]:
        error_msg = await update.message.reply_text("Please use this command in the group you want to connect.")
        await asyncio.sleep(2)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    group_id = update.message.chat.id
    group_title = update.message.chat.title
    
    # Check if already connected
    if group_id in CONNECTED_GROUPS:
        error_msg = await update.message.reply_text("❌ This group is already connected to the bank.")
        await asyncio.sleep(2)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Check if bot is admin in the group
    try:
        chat_member = await context.bot.get_chat_member(group_id, context.bot.id)
        if not chat_member.status in ["administrator", "creator"]:
            error_msg = await update.message.reply_text("❌ Bot must be an admin in this group to connect it.")
            await asyncio.sleep(2)
            try:
                await error_msg.delete()
                await update.message.delete()
            except:
                pass
            return
    except Exception as e:
        error_msg = await update.message.reply_text("❌ Cannot access group information. Make sure bot is added as admin.")
        await asyncio.sleep(2)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Connect group
    CONNECTED_GROUPS.append(group_id)
    save_config()
    
    # Send success message
    success_msg = await update.message.reply_text(
        f"✅ <b>Group Connected</b>\n"
        f"• {group_title} is now connected to the bank\n"
        f"• All bank commands will work here\n"
        f"• User accounts are shared across all connected groups",
        parse_mode=ParseMode.HTML
    )
    
    # Log the connection
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    await send_log(context,
        f"🔗 <b>Group Connected</b>\n"
        f"• {executor_link} connected {group_title} to the bank\n"
        f"• Group ID: {group_id}\n"
        f"• Date: {format_datetime()}"
    )
    
    # Delete messages after delay
    await asyncio.sleep(3)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def bal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Determine target user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # Owner/manager checking another user's account
        if not can_modify(user):
            # Delete command message immediately for unauthorized users
            try:
                await update.message.delete()
            except:
                pass
            return
        target = update.message.reply_to_message.from_user
    else:
        # User checking their own account
        target = user
    
    # Check if target has an account
    row = find_user_row(target.id)
    if not row:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Get account details
    name = sheet.cell(row, 2).value
    balance = float(sheet.cell(row, 5).value or 0)
    created_date = sheet.cell(row, 6).value
    last_transaction = sheet.cell(row, 7).value or "Never"
    
    # Clean the last transaction - remove any transaction details after the date
    if "•" in last_transaction:
        last_transaction = last_transaction.split("•")[0].strip()
    
    # Create user link
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    
    # Create buttons with user ID in callback data for permission checking
    keyboard = [
        [InlineKeyboardButton("history", callback_data=f"history_{target.id}_{user.id}"),
         InlineKeyboardButton("close", callback_data=f"close_bal_{target.id}_{user.id}")]
    ]
    
    # Format balance to 2 digits
    balance_formatted = f"{balance:02.0f}"
    
    # Send account details first
    message = await update.message.reply_text(
        f"<b>account details</b> 📮\n\n"
        f"{target_link} <code>[{target.id}]</code>\n\n"
        f"current balance — {CURRENCY}{balance_formatted}\n"
        f"s. {last_transaction}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    # Schedule auto-delete after 1 minute
    message_key = f"bal_{target.id}_{user.id}_{message.message_id}"
    BAL_MESSAGES[message_key] = message
    asyncio.create_task(schedule_auto_delete(message, message_key, "bal"))
    
    # Then delete the command message after 0.5 seconds
    await asyncio.sleep(0.5)
    try:
        await update.message.delete()
    except:
        pass

async def show_transaction_history(query, target_id, original_user_id):
    """Show transaction history for a user"""
    # Get account details
    row = find_user_row(target_id)
    if not row:
        return
    
    balance = float(sheet.cell(row, 5).value or 0)
    created_date = sheet.cell(row, 6).value
    
    # Get transactions
    transactions = TRANSACTION_HISTORY.get(target_id, [])
    
    # Format balance to 5 digits and transactions count to 4 digits
    balance_formatted = f"{balance:05.0f}"
    transactions_count_formatted = f"{len(transactions):04d}"
    
    if not transactions:
        message_text = "<b>transactions history</b> 🔖\n—————————————\n\n"
    else:
        message_text = "<b>transactions history</b> 🔖\n\n"
        for transaction in reversed(transactions[-10:]):  # Show latest first
            executor_link = f'<a href="tg://user?id={transaction["executor_id"]}">{transaction["executor_name"]}</a>'
            amount_formatted = f"{transaction['amount']:02.0f}"
            message_text += f"• {transaction['timestamp']}\n   {CURRENCY}{amount_formatted} {transaction['type']} by {executor_link}\n\n"
        message_text += "—————————————\n\n"
    
    message_text += f"total transactions of {transactions_count_formatted}\n"
    message_text += f"total balance of — {CURRENCY}{balance_formatted}\n\n"
    message_text += f"<i>c. {created_date}</i>"
    
    # Create buttons with user ID for permission checking
    keyboard = [
        [InlineKeyboardButton("per admin", callback_data=f"per_admin_{target_id}_{original_user_id}")],
        [InlineKeyboardButton("go back", callback_data=f"bal_back_{target_id}_{original_user_id}"),
         InlineKeyboardButton("close", callback_data=f"close_bal_{target_id}_{original_user_id}")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    # Update auto-delete tracking for the edited message
    message_key = f"history_{target_id}_{original_user_id}_{query.message.message_id}"
    BAL_MESSAGES[message_key] = query.message
    asyncio.create_task(schedule_auto_delete(query.message, message_key, "bal"))

async def show_per_admin(query, target_id, original_user_id):
    """Show balance per admin"""
    # Get account details
    row = find_user_row(target_id)
    if not row:
        return
    
    balance = float(sheet.cell(row, 5).value or 0)
    
    # Calculate balance per admin - include both added and used transactions
    transactions = TRANSACTION_HISTORY.get(target_id, [])
    admin_balances = {}
    
    for transaction in transactions:
        admin_name = transaction["executor_name"]
        if admin_name not in admin_balances:
            admin_balances[admin_name] = 0
        
        if transaction["type"] == "added":
            admin_balances[admin_name] += transaction["amount"]
        elif transaction["type"] == "used":
            admin_balances[admin_name] -= transaction["amount"]
    
    message_text = ""
    for admin_name, amount in admin_balances.items():
        amount_formatted = f"{amount:02.0f}"
        message_text += f"• balance by {admin_name}\n   amounting to {CURRENCY}{amount_formatted}\n\n"
    
    if not admin_balances:
        message_text = "• no balances recorded\n\n"
    
    # Format total balance to 2 digits
    balance_formatted = f"{balance:02.0f}"
    message_text += f"<b>— total amount is {CURRENCY}{balance_formatted}</b>"
    
    # Create buttons with user ID for permission checking
    keyboard = [
        [InlineKeyboardButton("go back", callback_data=f"history_back_{target_id}_{original_user_id}"),
         InlineKeyboardButton("close", callback_data=f"close_bal_{target_id}_{original_user_id}")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    # Update auto-delete tracking for the edited message
    message_key = f"per_admin_{target_id}_{original_user_id}_{query.message.message_id}"
    BAL_MESSAGES[message_key] = query.message
    asyncio.create_task(schedule_auto_delete(query.message, message_key, "bal"))

async def show_admin_list(query, original_user_id):
    """Show admin list in alphabetical order"""
    # Get all admins and sort alphabetically
    all_admins = sorted(ADMINS)
    
    message_text = "<b>admins list —</b>\n\n"
    if not all_admins:
        message_text += "• no admins\n\n"
    else:
        for admin in all_admins:
            message_text += f"• {admin}\n"
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("go back", callback_data=f"go_back_{original_user_id}"),
         InlineKeyboardButton("close", callback_data=f"close_{original_user_id}")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    # Update auto-delete tracking for the edited message
    message_key = f"admin_list_{original_user_id}_{query.message.message_id}"
    INFOBANK_MESSAGES[message_key] = query.message
    asyncio.create_task(schedule_auto_delete(query.message, message_key, "infobank"))

async def infobank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is the owner or co-owner
    if not (is_owner(user) or is_co_owner(user)):
        # Immediately delete the command message
        try:
            await update.message.delete()
        except:
            pass  # If already deleted, just ignore
        return
    
    # Get owner info
    owner_link = f'<a href="tg://user?id={OWNER_ID}">riv</a>'
    
    # Get totals
    accounts = get_all_accounts()
    total_accs = len(accounts)
    total_value = sum(float(acc.get('Balance', 0)) for acc in accounts)
    
    # Format to 4 digits for accounts, 3 digits for value
    total_accs_formatted = f"{total_accs:04d}"
    total_value_formatted = f"{total_value:03.0f}"
    
    # Create buttons with user ID in callback data for permission checking
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("data list", callback_data=f"data_list_{user_id}"),
         InlineKeyboardButton("admin list", callback_data=f"admin_list_{user_id}")],
        [InlineKeyboardButton("close", callback_data=f"close_{user_id}")]
    ]
    
    # Send message without replying (to avoid Rose bot deletion issues)
    message = await update.message.reply_text(
        f"<b>the river bank</b> 🎱\n"
        f"is owned by {owner_link}\n\n"
        f"total accs  — {total_accs_formatted}\n"
        f"total value — {CURRENCY}{total_value_formatted}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    # Schedule auto-delete after 1 minute
    message_key = f"infobank_{user_id}_{message.message_id}"
    INFOBANK_MESSAGES[message_key] = message
    asyncio.create_task(schedule_auto_delete(message, message_key, "infobank"))
    
    # Immediately delete the command message too
    try:
        await update.message.delete()
    except:
        pass  # If already deleted, just ignore

async def co(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is the owner or co-owner
    if not can_manage_users(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target is a bot
    if target.is_bot:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if target is already co-owner
    if target.username in CO_OWNERS:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Remove from managers if they were one
    if target.username in ADMINS:
        ADMINS.remove(target.username)
        save_admins()
    
    # Add to co-owners
    CO_OWNERS.append(target.username)
    save_co_owners()
    
    # Send success message first
    success_msg = await update.message.reply_text("promotion success ☑️")
    
    # Log the action
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await send_log(context,
        f"🛡️ <b>Co-Owner Promotion</b>\n"
        f"• {executor_link} promoted {target_link} to Co-Owner\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete both messages after 2 seconds
    await asyncio.sleep(2)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def prom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is the owner or co-owner
    if not can_manage_users(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target is a bot
    if target.is_bot:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if target is already manager
    if target.username in ADMINS:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Remove from co-owners if they were one
    if target.username in CO_OWNERS:
        CO_OWNERS.remove(target.username)
        save_co_owners()
    
    # Add to managers
    ADMINS.append(target.username)
    save_admins()
    
    # Send success message first
    success_msg = await update.message.reply_text("promotion success ☑️")
    
    # Log the action
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await send_log(context,
        f"👨‍💼 <b>Admin Promotion</b>\n"
        f"• {executor_link} promoted {target_link} to Manager\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete both messages after 2 seconds
    await asyncio.sleep(2)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def dem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is the owner or co-owner
    if not can_manage_users(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target is a bot
    if target.is_bot:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if target is manager or co-owner
    if target.username not in ADMINS and target.username not in CO_OWNERS:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Remove from both lists
    if target.username in ADMINS:
        ADMINS.remove(target.username)
        save_admins()
    
    if target.username in CO_OWNERS:
        CO_OWNERS.remove(target.username)
        save_co_owners()
    
    # Send success message first
    success_msg = await update.message.reply_text("demotion success ☑️")
    
    # Log the action
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await send_log(context,
        f"📉 <b>Demotion</b>\n"
        f"• {executor_link} demoted {target_link}\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete both messages after 2 seconds
    await asyncio.sleep(2)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is owner, co-owner or manager
    if not can_modify(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Send error message and delete both after 0.1 second
        error_msg = await update.message.reply_text("please reply to a user's message ❌")
        await asyncio.sleep(0.1)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target is a bot
    if target.is_bot:
        error_msg = await update.message.reply_text("cannot create account for bot ❌")
        await asyncio.sleep(0.1)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Check if account already exists
    if find_user_row(target.id):
        error_msg = await update.message.reply_text("user already has an account ❌")
        await asyncio.sleep(0.1)
        try:
            await error_msg.delete()
            await update.message.delete()
        except:
            pass
        return
    
    # Create account
    name = target.full_name
    username = f"@{target.username}" if target.username else ""
    link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    
    sheet.append_row([
        target.id, 
        name, 
        username, 
        link, 
        "0",  # Starting balance
        format_datetime(),  # Created date
        ""  # Last transaction
    ])
    
    # Send success message first
    success_msg = await update.message.reply_text("creation success ☑️")
    
    # Log the action
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await send_log(context,
        f"🆕 <b>Account Created</b>\n"
        f"• {executor_link} created account for {target_link}\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete both messages after 2 seconds
    await asyncio.sleep(2)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is owner, co-owner or manager
    if not can_modify(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if amount is provided
    if not context.args or len(context.args) < 1:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    try:
        amount = float(context.args[0])
        if amount <= 0:
            # Delete command message immediately
            try:
                await update.message.delete()
            except:
                pass
            return
    except:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target has an account
    row = find_user_row(target.id)
    if not row:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Get current balance and update
    current_balance = float(sheet.cell(row, 5).value or 0)
    new_balance = current_balance + amount
    
    # Update balance and last transaction
    sheet.update_cell(row, 5, str(new_balance))
    sheet.update_cell(row, 7, format_datetime())
    
    # Add transaction to history
    add_transaction(target.id, amount, user.id, user.first_name, "added")
    
    # Create user links
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    
    # Send reply first (will NOT be deleted)
    await update.message.reply_text(
        f"<b>done!</b> by {executor_link}\n\n"
        f"added {CURRENCY}{amount:,.0f} to {target_link}\n"
        f"new balance is {CURRENCY}{new_balance:,.0f}",
        parse_mode=ParseMode.HTML
    )
    
    # Log the action
    await send_log(context,
        f"💰 <b>Funds Added</b>\n"
        f"• {executor_link} added {CURRENCY}{amount:,.0f} to {target_link}\n"
        f"• New Balance: {CURRENCY}{new_balance:,.0f}\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete the command message after 0.5 seconds
    await asyncio.sleep(0.5)
    try:
        await update.message.delete()
    except:
        pass

async def use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is owner, co-owner or manager
    if not can_modify(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if amount is provided
    if not context.args or len(context.args) < 1:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    try:
        amount = float(context.args[0])
        if amount <= 0:
            # Delete command message immediately
            try:
                await update.message.delete()
            except:
                pass
            return
    except:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target has an account
    row = find_user_row(target.id)
    if not row:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Get current balance
    current_balance = float(sheet.cell(row, 5).value or 0)
    
    # Check if user has sufficient balance
    if current_balance < amount:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Deduct amount from balance
    new_balance = current_balance - amount
    
    # Update balance and last transaction
    sheet.update_cell(row, 5, str(new_balance))
    sheet.update_cell(row, 7, format_datetime())
    
    # Add transaction to history
    add_transaction(target.id, amount, user.id, user.first_name, "used")
    
    # Create user links
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    
    # Send reply first (will NOT be deleted)
    await update.message.reply_text(
        f"<b>done!</b> by {executor_link}\n\n"
        f"used {CURRENCY}{amount:,.0f} for {target_link}\n"
        f"new balance is {CURRENCY}{new_balance:,.0f}",
        parse_mode=ParseMode.HTML
    )
    
    # Log the action
    await send_log(context,
        f"💸 <b>Funds Used</b>\n"
        f"• {executor_link} used {CURRENCY}{amount:,.0f} for {target_link}\n"
        f"• New Balance: {CURRENCY}{new_balance:,.0f}\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete the command message immediately
    try:
        await update.message.delete()
    except:
        pass

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is owner, co-owner or manager
    if not can_modify(user):
        # Delete command message immediately for unauthorized users
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    target = update.message.reply_to_message.from_user
    
    # Check if target has an account
    row = find_user_row(target.id)
    if not row:
        # Delete command message immediately
        try:
            await update.message.delete()
        except:
            pass
        return
    
    # Reset account balance to 0
    sheet.update_cell(row, 5, "0")
    sheet.update_cell(row, 7, format_datetime())
    
    # Clear transaction history for this user
    if target.id in TRANSACTION_HISTORY:
        del TRANSACTION_HISTORY[target.id]
    
    # Send success message first
    success_msg = await update.message.reply_text("reset success ☑️")
    
    # Log the action
    executor_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    target_link = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await send_log(context,
        f"🔄 <b>Account Reset</b>\n"
        f"• {executor_link} reset account of {target_link}\n"
        f"• Balance reset to {CURRENCY}0\n"
        f"• Date: {format_datetime()}"
    )
    
    # Then delete both messages after 2 seconds
    await asyncio.sleep(2)
    try:
        await success_msg.delete()
        await update.message.delete()
    except:
        pass

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically delete accounts when users leave the group"""
    try:
        left_member = update.message.left_chat_member
        
        # Check if the user who left has an account
        if find_user_row(left_member.id):
            # Delete the account
            delete_user_account(left_member.id)
            print(f"✅ Deleted account for user who left: {left_member.id} ({left_member.full_name})")
            
            # Log the action if log channel is set
            if LOG_CHANNEL:
                await send_log(context,
                    f"🗑️ <b>Account Auto-Deleted</b>\n"
                    f"• {left_member.full_name} left the group\n"
                    f"• Account automatically deleted\n"
                    f"• Date: {format_datetime()}"
                )
            
    except Exception as e:
        print(f"Error handling left member: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    
    # Extract callback data
    callback_data = query.data
    
    try:
        # Check if user is authorized to interact with this button
        if callback_data.startswith(("history_", "close_bal_", "per_admin_", "history_back_", "bal_back_")):
            # Extract user ID from callback data for permission checking
            parts = callback_data.split("_")
            if len(parts) >= 3:
                original_user_id = int(parts[-1])  # Last part is the user ID who created the message
                
                # Check if the user clicking is the same user who initiated the command
                if user.id != original_user_id:
                    # Unauthorized user - ignore the click
                    await query.answer()
                    return
        
        elif callback_data.startswith(("data_list_", "go_back_", "close_", "admin_list_")):
            # For infobank callbacks
            original_user_id = int(callback_data.split('_')[-1])
            
            # Check if the user clicking is the same user who initiated the command
            if user.id != original_user_id:
                # Unauthorized user - ignore the click
                await query.answer()
                return
        
        # Handle balance-related callbacks
        if callback_data.startswith("history_") and not callback_data.startswith("history_back_"):
            # Format: history_123456789_987654321
            parts = callback_data.split("_")
            target_id = int(parts[1])
            original_user_id = int(parts[2])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"bal_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            await show_transaction_history(query, target_id, original_user_id)
        
        elif callback_data.startswith("per_admin_"):
            # Format: per_admin_123456789_987654321
            parts = callback_data.split("_")
            target_id = int(parts[2])
            original_user_id = int(parts[3])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"history_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            await show_per_admin(query, target_id, original_user_id)
        
        elif callback_data.startswith("history_back_"):
            # Format: history_back_123456789_987654321
            parts = callback_data.split("_")
            target_id = int(parts[2])
            original_user_id = int(parts[3])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"per_admin_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            await show_transaction_history(query, target_id, original_user_id)
        
        elif callback_data.startswith("bal_back_"):
            # Format: bal_back_123456789_987654321
            parts = callback_data.split("_")
            target_id = int(parts[2])
            original_user_id = int(parts[3])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"history_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            # Get account details
            row = find_user_row(target_id)
            if not row:
                return
            
            name = sheet.cell(row, 2).value
            balance = float(sheet.cell(row, 5).value or 0)
            last_transaction = sheet.cell(row, 7).value or "Never"
            
            # Clean the last transaction - remove any transaction details after the date
            if "•" in last_transaction:
                last_transaction = last_transaction.split("•")[0].strip()
            
            # Create user link
            target_link = f'<a href="tg://user?id={target_id}">{name.split()[0] if name else "User"}</a>'
            
            # Format balance to 2 digits
            balance_formatted = f"{balance:02.0f}"
            
            # Create buttons with user ID for permission checking
            keyboard = [
                [InlineKeyboardButton("history", callback_data=f"history_{target_id}_{original_user_id}"),
                 InlineKeyboardButton("close", callback_data=f"close_bal_{target_id}_{original_user_id}")]
            ]
            
            await query.edit_message_text(
                f"<b>account details</b> 📮\n\n"
                f"{target_link} <code>[{target_id}]</code>\n\n"
                f"current balance — {CURRENCY}{balance_formatted}\n"
                f"s. {last_transaction}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
            # Update auto-delete tracking for the edited message
            message_key = f"bal_{target_id}_{original_user_id}_{query.message.message_id}"
            BAL_MESSAGES[message_key] = query.message
            asyncio.create_task(schedule_auto_delete(query.message, message_key, "bal"))
        
        elif callback_data.startswith("close_bal_"):
            # Format: close_bal_123456789_987654321 - for balance messages
            parts = callback_data.split("_")
            target_id = int(parts[2])
            original_user_id = int(parts[3])
            
            # Remove message from auto-delete tracking since user is closing
            message_key_to_remove = f"bal_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            message_key_to_remove = f"history_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            message_key_to_remove = f"per_admin_{target_id}_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in BAL_MESSAGES:
                del BAL_MESSAGES[message_key_to_remove]
            
            await query.message.delete()
        
        # Handle infobank callbacks
        elif callback_data.startswith("data_list_"):
            # Format: data_list_123456789
            original_user_id = int(callback_data.split('_')[-1])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"infobank_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            accounts = get_all_accounts()
            accounts.reverse()  # Newest first
            accounts = accounts[:1000]  # Limit to 1000
            
            message_text = "<b>data list —</b>\n\n"
            for i, acc in enumerate(accounts, 1):
                name = acc.get('Name', 'Unknown')
                balance = float(acc.get('Balance', 0))
                message_text += f"{i}. {name} {CURRENCY}{balance:,.0f}\n"
            
            # Add go back and close buttons with user ID
            keyboard = [
                [InlineKeyboardButton("go back", callback_data=f"go_back_{original_user_id}"), 
                 InlineKeyboardButton("close", callback_data=f"close_{original_user_id}")]
            ]
            
            # Edit the original message to show data list with buttons
            await query.edit_message_text(
                message_text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
            # Update auto-delete tracking for the edited message
            message_key = f"data_list_{original_user_id}_{query.message.message_id}"
            INFOBANK_MESSAGES[message_key] = query.message
            asyncio.create_task(schedule_auto_delete(query.message, message_key, "infobank"))
        
        elif callback_data.startswith("admin_list_"):
            # Format: admin_list_123456789
            original_user_id = int(callback_data.split('_')[-1])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"infobank_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            await show_admin_list(query, original_user_id)
        
        elif callback_data.startswith("go_back_"):
            # Format: go_back_123456789
            original_user_id = int(callback_data.split('_')[-1])
            
            # Remove message from auto-delete tracking since user is interacting
            message_key_to_remove = f"data_list_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            message_key_to_remove = f"admin_list_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            # Get owner info
            owner_link = f'<a href="tg://user?id={OWNER_ID}">riv</a>'
            
            # Get totals
            accounts = get_all_accounts()
            total_accs = len(accounts)
            total_value = sum(float(acc.get('Balance', 0)) for acc in accounts)
            
            # Format to 4 digits for accounts, 3 digits for value
            total_accs_formatted = f"{total_accs:04d}"
            total_value_formatted = f"{total_value:03.0f}"
            
            # Create buttons with user ID
            keyboard = [
                [InlineKeyboardButton("data list", callback_data=f"data_list_{original_user_id}"),
                 InlineKeyboardButton("admin list", callback_data=f"admin_list_{original_user_id}")],
                [InlineKeyboardButton("close", callback_data=f"close_{original_user_id}")]
            ]
            
            # Edit message back to bank info
            await query.edit_message_text(
                f"<b>the river bank</b> 🎱\n"
                f"is owned by {owner_link}\n\n"
                f"total accs  — {total_accs_formatted}\n"
                f"total value — {CURRENCY}{total_value_formatted}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
            # Update auto-delete tracking for the edited message
            message_key = f"infobank_{original_user_id}_{query.message.message_id}"
            INFOBANK_MESSAGES[message_key] = query.message
            asyncio.create_task(schedule_auto_delete(query.message, message_key, "infobank"))
        
        elif callback_data.startswith("close_"):
            # Format: close_123456789 - for infobank messages
            original_user_id = int(callback_data.split('_')[-1])
            
            # Remove message from auto-delete tracking since user is closing
            message_key_to_remove = f"infobank_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            message_key_to_remove = f"data_list_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            message_key_to_remove = f"admin_list_{original_user_id}_{query.message.message_id}"
            if message_key_to_remove in INFOBANK_MESSAGES:
                del INFOBANK_MESSAGES[message_key_to_remove]
            
            # Delete message immediately
            await query.message.delete()
    
    except (ValueError, IndexError) as e:
        print(f"Error parsing callback data: {callback_data} - {e}")
        # Ignore callback data parsing errors
        pass

# Start bot
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Add handlers
app.add_handler(CommandHandler("setlog", setlog))
app.add_handler(CommandHandler("connect", connect))
app.add_handler(CommandHandler("infobank", infobank))
app.add_handler(CommandHandler("co", co))
app.add_handler(CommandHandler("prom", prom))
app.add_handler(CommandHandler("dem", dem))
app.add_handler(CommandHandler("new", new))
app.add_handler(CommandHandler("add", add))
app.add_handler(CommandHandler("use", use))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CommandHandler("bal", bal))
app.add_handler(CallbackQueryHandler(button_callback))

# Add handler for left chat members
from telegram.ext import MessageHandler, filters
app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_member))

print("✅ River Bank Bot is running!")
app.run_polling()