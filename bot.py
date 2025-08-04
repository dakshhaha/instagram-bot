
import logging
import asyncio
import random
import os
import asyncpg
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# Bot token
TOKEN = "8184657420:AAHlcxdjqMrB6G70PQCQHsgbgEn8gePECqU"

# Admin IDs
ADMIN_IDS = [7688652530, 8115268811]

# Channel info
CHANNELS = [
    ("@dakshbio", "Join1", "https://t.me/dakshbio"),
    ("@itzdhruv1060", "Join2", "https://t.me/itzdhruv1060"),
    ("@itzpaidmodfree", "Join3", "https://t.me/itzpaidmodfree"),
    ("@F3tG9JyvsONmNjhl", "Join4", "https://t.me/+F3tG9JyvsONmNjhl"),
    ("@paidmodffreee", "Join5", "https://t.me/paidmodffreee"),
    ("@itzteamlegend", "Join6", "https://t.me/itzteamlegend"),
    ("@itzdhruvfreindsgroup", "Join7", "https://t.me/itzdhruvfreindsgroup"),
]


# Database connection (set by init_db)
DB_POOL = None


# FastAPI app for health check and webhook
fastapi_app = FastAPI()

# Root endpoint for GET /
@fastapi_app.get("/")
async def root():
    return {"status": "ok"}

@fastapi_app.get("/health")
async def health():
    return {"status": "ok"}

# Telegram webhook endpoint
from fastapi import Request
from telegram.ext import Application
import json

@fastapi_app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.body()
    print("[DEBUG] /webhook endpoint hit, raw data:", data)
    update = json.loads(data)
    print("[DEBUG] Update type:", update.get("message", {}).get("text") or update.get("callback_query", {}).get("data") or str(update.keys()))
    await fastapi_app.bot_app.update_queue.put(update)
    print("[DEBUG] Update put into Application.update_queue")
    return {"ok": True}


logging.basicConfig(level=logging.INFO)

# --- Database helpers ---
async def init_db():
    global DB_POOL
    db_url = os.environ.get("DATABASE_URL")
    print("[DEBUG] DATABASE_URL:", db_url)
    DB_POOL = await asyncpg.create_pool(
        dsn=db_url
    )
    async with DB_POOL.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                points INT DEFAULT 0,
                verified BOOLEAN DEFAULT FALSE,
                invites INT DEFAULT 0
            );
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                user_id BIGINT PRIMARY KEY,
                ref_id BIGINT
            );
        ''')

async def get_user(user_id):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
        return dict(row) if row else None

async def set_user(user_id, points=0, verified=False, invites=0):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, points, verified, invites) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
            user_id, points, verified, invites
        )

async def update_user(user_id, **kwargs):
    async with DB_POOL.acquire() as conn:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ${len(vals)+2}")
            vals.append(v)
        if not sets:
            return
        await conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id = $1", user_id, *vals)

async def get_referral(user_id):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM referrals WHERE user_id=$1", user_id)
        return dict(row) if row else None

async def set_referral(user_id, ref_id):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO referrals (user_id, ref_id) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET ref_id=$2",
            user_id, ref_id
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[DEBUG] /start handler called for user {update.effective_user.id}")
    user = update.effective_user
    user_id = user.id
    ref = None
    if context.args:
        ref = context.args[0]
        if ref.isdigit() and int(ref) != user_id:
            await set_referral(user_id, int(ref))
    user_row = await get_user(user_id)
    if not user_row:
        await set_user(user_id)
    await send_channel_join(update, context)

def get_channel_keyboard():
    # Channels 1-6: 2 per row, only show button name (Join1, Join2, ...)
    buttons = []
    row = []
    for i, (_, name, url) in enumerate(CHANNELS[:6]):
        row.append(InlineKeyboardButton(name, url=url))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Channel 7 in its own row
    if len(CHANNELS) > 6:
        _, name, url = CHANNELS[6]
        buttons.append([InlineKeyboardButton(name, url=url)])
    # Check button below
    buttons.append([InlineKeyboardButton("✅ Check", callback_data="check_channels")])
    return InlineKeyboardMarkup(buttons)

async def send_channel_join(update, context):
    text = "<b>🔒 To use the bot, please join all channels below:</b>"
    await update.message.reply_text(text, reply_markup=get_channel_keyboard(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def check_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    member = await context.bot.get_chat_member(CHANNELS[0][0], user_id)
    if member.status in ["member", "administrator", "creator"]:
        user_row = await get_user(user_id)
        if not user_row:
            await set_user(user_id)
            user_row = await get_user(user_id)
        was_verified = user_row.get("verified", False)
        await update_user(user_id, verified=True)
        await query.message.delete()
        await send_main_menu(query, context)
        if not was_verified:
            ref_row = await get_referral(user_id)
            if ref_row:
                ref_id = ref_row["ref_id"]
                ref_user = await get_user(ref_id)
                if ref_user:
                    await update_user(ref_id, points=ref_user["points"]+1, invites=ref_user["invites"]+1)
                    await context.bot.send_message(ref_id, f"🎉 Someone joined using your link!\nTotal invites: {ref_user['invites']+1}\nTotal points: {ref_user['points']+1}")
    else:
        await query.answer("❌ Please join all channels first!", show_alert=True)

def get_main_menu():
    buttons = [
        [
            InlineKeyboardButton("🔗 Refer Link", callback_data="refer_link"),
            InlineKeyboardButton("💰 My Points", callback_data="my_points")
        ],
        [InlineKeyboardButton("💥 Hack Instagram Account", callback_data="hack_ig")],
    ]
    return InlineKeyboardMarkup(buttons)

async def send_main_menu(query, context):
    await context.bot.send_message(
        query.from_user.id,
        "<b>🎉 Welcome to the Instagram Hacking Bot!</b>\n\nChoose an option below to get started. Earn points by inviting friends, check your points, or Hack someone's Instagram Account!",
        reply_markup=get_main_menu(),
        parse_mode=ParseMode.HTML)


# --- Helper functions ---
def get_refer_link(user_id, context):
    return f"https://t.me/{context.bot.username}?start={user_id}"

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_random_server():
    servers = ["🇺🇸 USA-1", "🇬🇧 UK-2", "🇩🇪 DE-3", "🇸🇬 SG-4", "🇫🇷 FR-5", "🇮🇳 IN-6", "🇯🇵 JP-7"]
    return random.choice(servers)

# --- Handler: Refer Link ---
async def refer_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    link = get_refer_link(user_id, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"🔗 <b>Your personal referral link:</b>\n<code>{link}</code>\n\n👥 Share this link with your friends! When they join and verify, you'll earn points automatically. The more you invite, the more you can use the bot's features!",
        parse_mode=ParseMode.HTML)

# --- Handler: My Points ---
async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    user_row = await get_user(user_id)
    points = user_row["points"] if user_row else 0
    invites = user_row["invites"] if user_row else 0
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"💰 <b>Your Points:</b> <b>{points}</b>\n👥 <b>Total Invites:</b> <b>{invites}</b>\n\nKeep inviting friends to earn more points and unlock more features!",
        parse_mode=ParseMode.HTML)

# --- Handler: Hack Instagram Account (stepper) ---
async def hack_ig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    user_row = await get_user(user_id)
    points = user_row["points"] if user_row else 0
    if points < 2:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "❌ <b>Not enough points!</b>\nYou need at least 2 points to use this feature.\n\nInvite friends using your referral link to earn more points and unlock the Instagram hack!",
            parse_mode=ParseMode.HTML)
        return
    await update_user(user_id, points=points-2)
    context.user_data[user_id] = {}
    await update.callback_query.answer()
    await ask_instagram_username(update, context)

async def ask_instagram_username(update, context):
    # Username is required, cannot skip
    msg = (
        "👤 <b>Enter the Instagram username you want to hack:</b>\n\n"
        "Please provide the username (e.g. <code>target_user123</code>). This is required to continue."
    )
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "username"

async def ask_target_name(update, context):
    await update.message.reply_text(
        "📝 <b>Enter the target's name:</b>\n(Or type /skip to skip)",
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "name"

async def ask_target_age(update, context):
    await update.message.reply_text(
        "🎂 <b>Enter the target's age:</b>\n(Or type /skip to skip)",
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "age"

async def ask_email(update, context):
    await update.message.reply_text(
        "📧 <b>Enter the target's email (if known):</b>\n(Or type /skip to skip)",
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "email"

async def ask_phone(update, context):
    await update.message.reply_text(
        "📱 <b>Enter the target's phone number (if known):</b>\n(Or type /skip to skip)",
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "phone"

async def ask_password_count(update, context):
    await update.message.reply_text(
        "🔢 <b>How many passwords to try? (max 100,000)</b>\n(Or type /skip to skip)",
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "count"

async def ask_vpn(update, context):
    buttons = [
        [InlineKeyboardButton("Yes, use VPN", callback_data="vpn_yes")],
        [InlineKeyboardButton("No, don't use VPN", callback_data="vpn_no")],
    ]
    await update.message.reply_text(
        "🌐 <b>Should we use VPN for the attack?</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML)
    user_id = update.effective_user.id
    context.user_data[user_id]["hack_step"] = "vpn"

async def hack_step_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    step = context.user_data.get(user_id, {}).get("hack_step")
    if not step:
        return
    text = update.message.text
    # Username is required, cannot skip
    if step == "username":
        if not text or text.strip() == "" or text.strip() == "/skip":
            await update.message.reply_text(
                "❗️ <b>Username is required!</b>\nPlease enter the Instagram username to continue.",
                parse_mode=ParseMode.HTML)
            return
        context.user_data[user_id]["username"] = text
        await ask_target_name(update, context)
    elif step == "name":
        if text == "/skip":
            text = "Skipped"
        context.user_data[user_id]["name"] = text
        await ask_target_age(update, context)
    elif step == "age":
        if text == "/skip":
            text = "Skipped"
        context.user_data[user_id]["age"] = text
        await ask_email(update, context)
    elif step == "email":
        if text == "/skip":
            text = "Skipped"
        context.user_data[user_id]["email"] = text
        await ask_phone(update, context)
    elif step == "phone":
        if text == "/skip":
            text = "Skipped"
        context.user_data[user_id]["phone"] = text
        await ask_password_count(update, context)
    elif step == "count":
        if text == "/skip":
            text = "10000"
        try:
            count = int(text)
            if count > 100000:
                count = 100000
        except:
            count = 10000
        context.user_data[user_id]["count"] = count
        await ask_vpn(update, context)

# --- VPN Callback ---
async def vpn_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    use_vpn = query.data == "vpn_yes"
    context.user_data[user_id]["vpn"] = use_vpn
    await query.answer()
    msg = await query.message.reply_text("🔌 Connecting to VPN... Please wait...", parse_mode=ParseMode.HTML)
    if use_vpn:
        server = get_random_server()
        await asyncio.sleep(4)
        await msg.edit_text(f"🔌 Connected to {server}", parse_mode=ParseMode.HTML)
        await asyncio.sleep(2)
    await msg.edit_text("🔑 Generating password wordlist... This may take a moment...", parse_mode=ParseMode.HTML)
    await asyncio.sleep(4)
    await msg.edit_text("🚀 Starting attack... Please wait while we attempt to crack the password...", parse_mode=ParseMode.HTML)
    await asyncio.sleep(4)
    await msg.delete()
    timer = random.randint(10, 15)
    fake_msg = await context.bot.send_message(user_id, f"⚡️ ATTACK STARTED!\n⏳ Estimated time: {timer} min", parse_mode=ParseMode.HTML)
    for i in range(timer, 0, -1):
        await asyncio.sleep(10)
        try:
            await fake_msg.edit_text(f"⚡️ ATTACK STARTED!\n⏳ Estimated time: {i-1} min", parse_mode=ParseMode.HTML)
        except:
            pass
    await fake_msg.delete()
    # Generate fake password
    details = context.user_data[user_id]
    fake_pw = f"{details.get('name','user')}{random.randint(1000,9999)}_{details.get('age','00')}"
    await context.bot.send_message(user_id, f"✅ <b>Cracked password successfully!</b>\n🔑 <b>Password:</b> <code>{fake_pw}</code>", parse_mode=ParseMode.HTML)



# --- Admin Commands ---
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    async with DB_POOL.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
    await update.message.reply_text(f"👑 <b>Total users in bot:</b> {total_users}", parse_mode=ParseMode.HTML)

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
    user_list = [str(row["user_id"]) for row in rows]
    msg = "<b>All user IDs:</b>\n" + "\n".join(user_list)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    await update.message.reply_text("Please send the message you want to broadcast to all users. It will be forwarded as-is. (Text, photo, video, etc. supported)")
    context.user_data["awaiting_broadcast"] = True

async def broadcast_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    if not context.user_data.get("awaiting_broadcast"):
        return
    context.user_data["awaiting_broadcast"] = False
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
    count = 0
    for row in rows:
        uid = row["user_id"]
        try:
            await update.forward(chat_id=uid)
            count += 1
        except:
            pass
    await update.message.reply_text(f"Broadcast sent to {count} users.")

async def admin_addpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /addpoints <user_id> <points>")
        return
    try:
        target_id = int(args[0])
        points = int(args[1])
    except:
        await update.message.reply_text("Invalid arguments. Usage: /addpoints <user_id> <points>")
        return
    target_user = await get_user(target_id)
    if not target_user:
        await set_user(target_id)
        target_user = await get_user(target_id)
    new_points = target_user["points"] + points
    await update_user(target_id, points=new_points)
    await update.message.reply_text(f"✅ Added {points} points to user {target_id}. Total points: {new_points}")


def main():
    print("[DEBUG] Entered main()")
    import threading
    async def run():
        print("[DEBUG] Entered run() async function")
        await init_db()
        print("[DEBUG] Finished init_db()")
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(check_channels, pattern="^check_channels$"))
        app.add_handler(CallbackQueryHandler(refer_link, pattern="^refer_link$"))
        app.add_handler(CallbackQueryHandler(my_points, pattern="^my_points$"))
        app.add_handler(CallbackQueryHandler(hack_ig, pattern="^hack_ig$"))
        app.add_handler(CallbackQueryHandler(vpn_choice, pattern="^vpn_yes$|^vpn_no$"))
        app.add_handler(CommandHandler("admin", admin_stats))
        app.add_handler(CommandHandler("users", admin_users))
        app.add_handler(CommandHandler("broadcast", admin_broadcast))
        app.add_handler(CommandHandler("addpoints", admin_addpoints))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hack_step_handler))
        app.add_handler(CommandHandler("skip", hack_step_handler))
        app.add_handler(MessageHandler(filters.ALL, broadcast_forward_handler))

        # Attach app to FastAPI for webhook handler
        fastapi_app.bot_app = app

        # Set webhook URL (replace YOUR_RENDER_URL with your actual Render HTTPS URL)
        WEBHOOK_PATH = "/webhook"
        RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") or "https://YOUR_RENDER_URL.onrender.com"
        WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"

        print("[DEBUG] Initializing Application...")
        await app.initialize()
        print(f"[DEBUG] Setting webhook to {WEBHOOK_URL}")
        await app.bot.set_webhook(WEBHOOK_URL)
        print("[DEBUG] Starting Application...")
        await app.start()

        # Start FastAPI (uvicorn) in async context
        print("[DEBUG] Starting FastAPI (uvicorn) with await server.serve() in async context")
        import uvicorn
        config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    import os
    print("[DEBUG] About to get or create event loop and run run()")
    import sys
    import asyncio
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
    print("[DEBUG] Exited loop.run_until_complete(run())")

if __name__ == "__main__":
    main()
