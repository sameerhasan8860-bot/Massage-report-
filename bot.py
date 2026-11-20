import asyncio
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
from config import BOT_TOKEN, API_ID, API_HASH
from telethon import TelegramClient
from telethon.sessions import StringSession
from session_manager import (
    save_session, get_all_sessions,
    get_total_count, get_active_count, get_expired_count
)
from reporter import mass_report
from proxy_manager import fetch_proxies, rotate_forever

logging.basicConfig(level=logging.INFO)

# States
(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_COUNT, CONFIRM_REPORT
) = range(8)

REASONS = {
    "1":  ("😤 I don't like it",         []),
    "2":  ("👶 Child Abuse",             [("2a","Child illegal abuse"),("2b","Child sexual abuse")]),
    "3":  ("🔪 Violence",                [("3a","Insult / false info"),("3b","Graphic content"),("3c","Extreme violence"),("3d","Hate speech"),("3e","Calling for violence"),("3f","Terrorism")]),
    "4":  ("🚫 Illegal Goods/Services",  [("4a","Weapons"),("4b","Drugs"),("4c","Fake documents"),("4d","Counterfeit money"),("4e","Hacking tools"),("4f","Counterfeit merch"),("4g","Other")]),
    "5":  ("🔞 Illegal Adult Content",   [("5a","Child abuse"),("5b","Illegal sexual service"),("5c","Animal abuse"),("5d","Non-consensual imagery"),("5e","Pornography"),("5f","Other illegal sexual")]),
    "6":  ("🧾 Personal Data",           [("6a","Private images"),("6b","Private number"),("6c","Address"),("6d","Credentials"),("6e","Other personal data")]),
    "7":  ("💸 Scam or Fraud",           [("7a","Impersonation"),("7b","Financial scams"),("7c","Malware/phishing"),("7d","Fraudulent seller")]),
    "8":  ("©️ Copyright",               []),
    "9":  ("🔎 Other",                   []),
    "10": ("⚠️ Not illegal but remove",  []),
}

tmp = {}  # user state temp storage

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel / Stop", callback_data="cancel_flow")]])

async def cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = tmp.pop(uid, None)
    if data and data.get("client"):
        try:
            await data["client"].disconnect()
        except Exception:
            pass

    if update.callback_query:
        q = update.callback_query
        await q.answer("Process cancelled.")
        await q.edit_message_text("❌ *Host process cancelled.*\n\nNo new session was saved.", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    elif update.effective_message:
        await update.effective_message.reply_text("❌ Host process cancelled. No new session was saved.", reply_markup=main_menu_kb())
    return ConversationHandler.END

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
         InlineKeyboardButton("📊 Status",      callback_data="status")],
        [InlineKeyboardButton("🚨 Mass Report", callback_data="start_report"),
         InlineKeyboardButton("📋 Sessions",    callback_data="sessions")],
    ])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Mass Report Bot*\n\n_Multi-account • Proxy Rotation • MongoDB Sessions_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb()
    )

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total   = await get_total_count()
    active  = await get_active_count()
    expired = await get_expired_count()

    text = (
        f"╔══════════════════╗\n"
        f"║   📊 BOT STATUS   ║\n"
        f"╚══════════════════╝\n\n"
        f"🟢 Active IDs   : `{active}`\n"
        f"🔴 Expired IDs  : `{expired}`\n"
        f"📦 Total IDs    : `{total}`\n"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]]))

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid  = q.from_user.id

    if data == "back":
        await q.edit_message_text(
            "🤖 *Mass Report Bot*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb()
        )
        return ConversationHandler.END

    if data == "status":
        await status_cmd(update, ctx)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        lines = [f"`{i}. {s['phone']}`" for i, s in enumerate(sessions, start=1)] or ["No active sessions."]
        await q.edit_message_text(
            "📋 *Active Sessions:*\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]])
        )
        return

    if data == "add_account":
        await q.edit_message_text("📱 Enter phone number with country code:\n\nExample: `+919876543210`\n\nUse /cancel or the button below to stop.", parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb())
        return ADD_PHONE

    if data == "start_report":
        await q.edit_message_text("🎯 Send target:\n\n• `@username`\n• Channel/Group link\n• Bot username", parse_mode=ParseMode.MARKDOWN)
        return REPORT_TARGET

    if data.startswith("cat_"):
        cat = data[4:]
        tmp[uid]["cat"] = cat
        subs = REASONS[cat][1]
        if not subs:
            tmp[uid]["reason"] = cat
            await q.edit_message_text("🔢 How many reports per ID? (e.g. 20)", parse_mode=ParseMode.MARKDOWN)
            return REPORT_COUNT
        kb = [[InlineKeyboardButton(label, callback_data=f"sub_{key}")] for key, label in subs]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="start_report")])
        await q.edit_message_text(f"📂 *{REASONS[cat][0]}*\n\nSelect sub-reason:", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        return REPORT_REASON_SUB

    if data.startswith("sub_"):
        tmp[uid]["reason"] = data[4:]
        await q.edit_message_text("🔢 How many reports per ID? (e.g. 20)", parse_mode=ParseMode.MARKDOWN)
        return REPORT_COUNT

    if data == "confirm_report":
        await q.edit_message_text("⏳ *Mass reporting started...*\n\nThis may take a while.", parse_mode=ParseMode.MARKDOWN)
        sessions = await get_all_sessions()
        target   = tmp[uid]["target"]
        reason   = tmp[uid]["reason"]
        count    = tmp[uid]["count"]

        sent_msg = await q.message.reply_text("📡 Reporting in progress...")

        progress = {"last": 0}
        async def cb(r):
            total_done = r["success"] + r["failed"] + r["expired"]
            if total_done - progress["last"] >= 5:
                progress["last"] = total_done
                await sent_msg.edit_text(
                    f"📡 *Reporting...*\n\n"
                    f"✅ Success : `{r['success']}`\n"
                    f"❌ Failed  : `{r['failed']}`\n"
                    f"🔴 Expired : `{r['expired']}`",
                    parse_mode=ParseMode.MARKDOWN
                )

        result = await mass_report(sessions, target, reason, count, progress_cb=cb)
        await sent_msg.edit_text(
            f"╔══════════════════╗\n"
            f"║   ✅ REPORT DONE  ║\n"
            f"╚══════════════════╝\n\n"
            f"🎯 Target   : `{target}`\n"
            f"✅ Success  : `{result['success']}`\n"
            f"❌ Failed   : `{result['failed']}`\n"
            f"🔴 Expired  : `{result['expired']}`\n"
            f"📦 Total IDs: `{len(sessions)}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb()
        )
        return ConversationHandler.END

# --- Add Account Flow ---
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    phone = update.message.text.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    tmp[uid] = {"client": client, "phone": phone}
    await client.send_code_request(phone)
    await update.message.reply_text("🔑 Enter OTP code:", reply_markup=cancel_kb())
    return ADD_CODE

async def recv_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = update.message.text.strip()
    data = tmp[uid]
    try:
        await data["client"].sign_in(data["phone"], code)
    except Exception as e:
        if "2FA" in str(e) or "password" in str(e).lower():
            await update.message.reply_text("🔐 Enter your 2FA password:", reply_markup=cancel_kb())
            tmp[uid]["code"] = code
            return ADD_2FA
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END
    session_str = data["client"].session.save()
    await save_session(uid, data["phone"], session_str)
    await data["client"].disconnect()
    await update.message.reply_text(f"✅ Account `{data['phone']}` added!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    return ConversationHandler.END

async def recv_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    password = update.message.text.strip()
    data     = tmp[uid]
    try:
        await data["client"].sign_in(password=password)
        session_str = data["client"].session.save()
        await save_session(uid, data["phone"], session_str)
        await data["client"].disconnect()
        await update.message.reply_text(f"✅ Account `{data['phone']}` added!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ 2FA failed: {e}")
    return ConversationHandler.END

# --- Report Flow ---
async def recv_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = update.message.text.strip()
    tmp[uid] = {"target": target}
    kb = [[InlineKeyboardButton(v[0], callback_data=f"cat_{k}")] for k, v in REASONS.items()]
    await update.message.reply_text("📂 Select report category:", reply_markup=InlineKeyboardMarkup(kb))
    return REPORT_REASON_CAT

async def recv_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ Enter a valid number.")
        return REPORT_COUNT
    tmp[uid]["count"] = count
    sessions = await get_all_sessions()
    await update.message.reply_text(
        f"⚠️ *Confirm Report*\n\n"
        f"🎯 Target : `{tmp[uid]['target']}`\n"
        f"📋 Reason : `{tmp[uid]['reason']}`\n"
        f"🔢 Per ID : `{count}`\n"
        f"👥 IDs    : `{len(sessions)}`\n"
        f"📊 Total  : `{len(sessions) * count}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Start Reporting", callback_data="confirm_report"),
             InlineKeyboardButton("❌ Cancel",          callback_data="back")]
        ])
    )
    return CONFIRM_REPORT

async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /join <link or @username>")
        return
    target   = ctx.args[0]
    sessions = await get_all_sessions()
    joined, failed = 0, 0
    for i, s in enumerate(sessions):
        try:
            from session_manager import build_client
            from telethon.tl.functions.channels import JoinChannelRequest
            client = await build_client(s["session"], i)
            await client.connect()
            await client(JoinChannelRequest(target))
            await client.disconnect()
            joined += 1
        except:
            failed += 1
    await update.message.reply_text(f"✅ Joined: `{joined}` | ❌ Failed: `{failed}`", parse_mode=ParseMode.MARKDOWN)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(add_account|start_report)$")],
        states={
            ADD_PHONE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            ADD_CODE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_code)],
            ADD_2FA:           [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_2fa)],
            REPORT_TARGET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_target)],
            REPORT_REASON_CAT: [CallbackQueryHandler(button_handler, pattern="^cat_")],
            REPORT_REASON_SUB: [CallbackQueryHandler(button_handler, pattern="^sub_")],
            REPORT_COUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_count)],
            CONFIRM_REPORT:    [CallbackQueryHandler(button_handler, pattern="^confirm_report$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(cancel_flow, pattern="^cancel_flow$"),
            CallbackQueryHandler(button_handler, pattern="^back$")
        ],
        per_message=False
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("join",   join_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",  "Main menu"),
            BotCommand("status", "Bot status"),
            BotCommand("join",   "Join with all IDs"),
            BotCommand("cancel", "Cancel the current host process"),
        ])

    app.post_init = on_startup
    app.run_polling()

if __name__ == "__main__":
    main()
