import asyncio
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from config import BOT_TOKEN, API_ID, API_HASH
from telethon import TelegramClient
from telethon.sessions import StringSession
from session_manager import (
    save_session, get_all_sessions, delete_session,
    get_total_count, get_active_count, get_expired_count
)
from reporter import mass_report, mass_join
from proxy_manager import fetch_proxies, rotate_forever

logging.basicConfig(level=logging.INFO)

# States
(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_MESSAGE, REPORT_COUNT, CONFIRM_REPORT
) = range(9)

REASONS = {
    "1":  ("😤 I don't like it",        []),
    "2":  ("👶 Child Abuse",            [("2a","Child illegal abuse"),("2b","Child sexual abuse")]),
    "3":  ("🔪 Violence",               [("3a","Insult / false info"),("3b","Graphic content"),("3c","Extreme violence"),("3d","Hate speech"),("3e","Calling for violence"),("3f","Terrorism")]),
    "4":  ("🚫 Illegal Goods/Services", [("4a","Weapons"),("4b","Drugs"),("4c","Fake documents"),("4d","Counterfeit money"),("4e","Hacking tools"),("4f","Counterfeit merch"),("4g","Other")]),
    "5":  ("🔞 Illegal Adult Content",  [("5a","Child abuse"),("5b","Illegal sexual service"),("5c","Animal abuse"),("5d","Non-consensual imagery"),("5e","Pornography"),("5f","Other")]),
    "6":  ("🧾 Personal Data",          [("6a","Private images"),("6b","Private number"),("6c","Address"),("6d","Credentials"),("6e","Other")]),
    "7":  ("💸 Scam or Fraud",          [("7a","Impersonation"),("7b","Financial scams"),("7c","Malware/phishing"),("7d","Fraudulent seller")]),
    "8":  ("©️ Copyright",              []),
    "9":  ("🔎 Other",                  []),
    "10": ("⚠️ Not illegal but remove", []),
}

tmp = {}


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_flow")]])


def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
         InlineKeyboardButton("📊 Status",      callback_data="status")],
        [InlineKeyboardButton("🚨 Mass Report", callback_data="start_report"),
         InlineKeyboardButton("📋 Sessions",    callback_data="sessions")],
        [InlineKeyboardButton("❔ Help",         callback_data="help")],
    ])


async def cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = tmp.pop(uid, None)
    if data and data.get("client"):
        try:
            await data["client"].disconnect()
        except Exception:
            pass
    if update.callback_query:
        await update.callback_query.answer("Cancelled.")
        await update.callback_query.edit_message_text(
            "❌ *Cancelled.*", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )
    elif update.effective_message:
        await update.effective_message.reply_text("❌ Cancelled.", reply_markup=main_menu_kb())
    return ConversationHandler.END


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Mass Report Bot*\n\n_Multi-account • Proxy Rotation • MongoDB Sessions_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb()
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *MASS REPORT BOT — HELP*\n\n"
        "*/start* — Main menu\n"
        "*/host* — Add a Telegram account\n"
        "*/status* — Bot + account stats\n"
        "*/session* — List saved accounts\n"
        "*/unhost +91xxx* — Remove an account\n"
        "*/join <link/@user>* — Join with all accounts\n"
        "*/cancel* — Cancel current flow\n\n"
        "*Target formats for reporting:*\n"
        "• `@username` — user or channel\n"
        "• `https://t.me/username` — peer\n"
        "• `https://t.me/username/123` — specific message\n"
        "• `https://t.me/c/CHANNELID/123` — private channel message\n"
        "• Numeric Telegram ID"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]])
        )
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total   = await get_total_count()
    active  = await get_active_count()
    expired = await get_expired_count()
    text = (
        "╔══════════════════╗\n"
        "║   📊 BOT STATUS   ║\n"
        "╚══════════════════╝\n\n"
        f"🟢 Active IDs   : `{active}`\n"
        f"🔴 Expired IDs  : `{expired}`\n"
        f"📦 Total IDs    : `{total}`\n"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]])
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def session_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sessions = await get_all_sessions()
    lines = [f"`{i}. {s.get('phone','?')}`" for i, s in enumerate(sessions, 1)]
    msg = "📋 *Saved Sessions*\n\n" + ("\n".join(lines) if lines else "No sessions found.")
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())


async def unhost_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.effective_message.reply_text("Usage: `/unhost +919876543210`", parse_mode=ParseMode.MARKDOWN)
        return
    phone = ctx.args[0].strip()
    removed = await delete_session(phone, update.effective_user.id)
    if removed:
        await update.effective_message.reply_text(f"✅ `{phone}` removed.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_message.reply_text(f"ℹ️ No session for `{phone}`.", parse_mode=ParseMode.MARKDOWN)


async def host_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📱 Enter phone number with country code:\n\nExample: `+919876543210`",
        parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb()
    )
    return ADD_PHONE


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid  = q.from_user.id

    if data == "back":
        await q.edit_message_text(
            "🤖 *Mass Report Bot*", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )
        return ConversationHandler.END

    if data == "status":
        await status_cmd(update, ctx)
        return

    if data == "help":
        await help_cmd(update, ctx)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        lines = [f"`{i}. {s['phone']}`" for i, s in enumerate(sessions, 1)] or ["No active sessions."]
        await q.edit_message_text(
            "📋 *Active Sessions:*\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]])
        )
        return

    if data == "add_account":
        await q.edit_message_text(
            "📱 Enter phone number with country code:\n\nExample: `+919876543210`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb()
        )
        return ADD_PHONE

    if data == "start_report":
        await q.edit_message_text(
            "🎯 *Send target:*\n\n"
            "• `@username` — user, bot, channel\n"
            "• `https://t.me/username` — peer link\n"
            "• `https://t.me/username/123` — specific message\n"
            "• `https://t.me/c/CHANNELID/123` — private channel msg\n"
            "• Numeric Telegram ID",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kb()
        )
        return REPORT_TARGET

    if data.startswith("cat_"):
        cat = data[4:]
        if uid not in tmp:
            tmp[uid] = {}
        tmp[uid]["cat"] = cat
        subs = REASONS[cat][1]
        if not subs:
            tmp[uid]["reason"] = cat
            await q.edit_message_text(
                "✍️ *Report message:*\n\nType what you want to write in the report (1-512 chars):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=cancel_kb()
            )
            return REPORT_MESSAGE
        kb = [[InlineKeyboardButton(label, callback_data=f"sub_{key}")] for key, label in subs]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="start_report")])
        await q.edit_message_text(
            f"📂 *{REASONS[cat][0]}*\n\nSelect sub-reason:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return REPORT_REASON_SUB

    if data.startswith("sub_"):
        if uid not in tmp:
            tmp[uid] = {}
        tmp[uid]["reason"] = data[4:]
        await q.edit_message_text(
            "✍️ *Report message:*\n\nType what you want to write in the report (1-512 chars):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kb()
        )
        return REPORT_MESSAGE

    if data == "confirm_report":
        sessions = await get_all_sessions()
        if not sessions:
            await q.edit_message_text(
                "❌ *No active sessions.*\n\nAdd accounts first via ➕ Add Account.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
            )
            return ConversationHandler.END

        target         = tmp[uid]["target"]
        reason         = tmp[uid]["reason"]
        count          = tmp[uid]["count"]
        report_message = tmp[uid]["report_message"]

        await q.edit_message_text("⏳ *Mass reporting started...*", parse_mode=ParseMode.MARKDOWN)
        sent_msg = await q.message.reply_text("📡 Starting...")

        progress = {"last": 0}

        async def cb(r):
            total_done = r["success"] + r["failed"] + r["expired"]
            if total_done - progress["last"] >= 3 or total_done == len(sessions) * count:
                progress["last"] = total_done
                try:
                    await sent_msg.edit_text(
                        f"📡 *Reporting in progress...*\n\n"
                        f"✅ Success : `{r['success']}`\n"
                        f"❌ Failed  : `{r['failed']}`\n"
                        f"🔴 Expired : `{r['expired']}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

        result = await mass_report(
            sessions, target, reason, count, report_message, progress_cb=cb
        )

        err_summary = ""
        if result["errors"]:
            unique_errors = list(set(result["errors"]))[:3]
            err_summary = "\n⚠️ Errors: `" + "` | `".join(unique_errors) + "`"

        await sent_msg.edit_text(
            "╔══════════════════╗\n"
            "║   📊 REPORT DONE  ║\n"
            "╚══════════════════╝\n\n"
            f"🎯 Target   : `{target}`\n"
            f"✅ Success  : `{result['success']}`\n"
            f"❌ Failed   : `{result['failed']}`\n"
            f"🔴 Expired  : `{result['expired']}`\n"
            f"👥 Total IDs: `{len(sessions)}`"
            f"{err_summary}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
        return ConversationHandler.END

    return None


# --- Add Account Flow ---
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    phone = update.message.text.strip()
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        tmp[uid] = {"client": client, "phone": phone}
        await update.message.reply_text("🔑 Enter the OTP you received:", reply_markup=cancel_kb())
        return ADD_CODE
    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`\n\nTry again or /cancel", parse_mode=ParseMode.MARKDOWN)
        return ADD_PHONE


async def recv_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = update.message.text.strip()
    data = tmp.get(uid, {})
    try:
        await data["client"].sign_in(data["phone"], code)
        session_str = data["client"].session.save()
        await save_session(uid, data["phone"], session_str)
        await data["client"].disconnect()
        await update.message.reply_text(
            f"✅ Account `{data['phone']}` added successfully!",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
        return ConversationHandler.END
    except Exception as e:
        if "2FA" in str(e) or "password" in str(e).lower() or "SessionPasswordNeeded" in str(e):
            await update.message.reply_text("🔐 Enter your 2FA password:", reply_markup=cancel_kb())
            return ADD_2FA
        await update.message.reply_text(f"❌ OTP error: `{e}`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END


async def recv_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    password = update.message.text.strip()
    data     = tmp.get(uid, {})
    try:
        await data["client"].sign_in(password=password)
        session_str = data["client"].session.save()
        await save_session(uid, data["phone"], session_str)
        await data["client"].disconnect()
        await update.message.reply_text(
            f"✅ Account `{data['phone']}` added!",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )
        tmp.pop(uid, None)
    except Exception as e:
        await update.message.reply_text(f"❌ 2FA failed: `{e}`", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# --- Report Flow ---
async def recv_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    target = update.message.text.strip()
    tmp[uid] = {"target": target}
    kb = [[InlineKeyboardButton(v[0], callback_data=f"cat_{k}")] for k, v in REASONS.items()]
    await update.message.reply_text(
        f"📂 *Select report category:*\n\n🎯 Target: `{target}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return REPORT_REASON_CAT


async def recv_report_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Report message cannot be empty. Type a message:")
        return REPORT_MESSAGE
    if len(text) > 512:
        await update.message.reply_text("❌ Too long (max 512 chars). Try again:")
        return REPORT_MESSAGE
    tmp[uid]["report_message"] = text
    await update.message.reply_text(
        "🔢 *How many reports per ID?*\n\nEnter a number (e.g. 20):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return REPORT_COUNT


async def recv_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text.strip())
        if count < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number:")
        return REPORT_COUNT

    tmp[uid]["count"] = count
    sessions = await get_all_sessions()
    t = tmp[uid]

    await update.message.reply_text(
        "⚠️ *Confirm Report*\n\n"
        f"🎯 Target         : `{t['target']}`\n"
        f"📋 Reason         : `{t['reason']}`\n"
        f"✍️ Report message : `{t['report_message'][:60]}{'...' if len(t['report_message']) > 60 else ''}`\n"
        f"🔢 Per ID         : `{count}`\n"
        f"👥 Active IDs     : `{len(sessions)}`\n"
        f"📊 Total reports  : `{len(sessions) * count}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Start Reporting", callback_data="confirm_report"),
             InlineKeyboardButton("❌ Cancel",          callback_data="back")]
        ])
    )
    return CONFIRM_REPORT


async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/join @username` or `/join https://t.me/+invitehash`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    target   = ctx.args[0]
    sessions = await get_all_sessions()
    if not sessions:
        await update.message.reply_text("❌ No active sessions found.")
        return

    msg = await update.message.reply_text(f"⏳ Joining `{target}` with {len(sessions)} accounts...", parse_mode=ParseMode.MARKDOWN)
    result = await mass_join(sessions, target)

    await msg.edit_text(
        f"✅ Joined    : `{result['joined']}`\n"
        f"🔄 Already  : `{result['already']}`\n"
        f"❌ Failed    : `{result['failed']}`",
        parse_mode=ParseMode.MARKDOWN
    )


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("host", host_cmd),
            CallbackQueryHandler(button_handler, pattern="^(add_account|start_report)$"),
        ],
        states={
            ADD_PHONE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            ADD_CODE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_code)],
            ADD_2FA:           [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_2fa)],
            REPORT_TARGET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_target)],
            REPORT_REASON_CAT: [CallbackQueryHandler(button_handler, pattern="^cat_")],
            REPORT_REASON_SUB: [CallbackQueryHandler(button_handler, pattern="^sub_")],
            REPORT_MESSAGE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_report_message)],
            REPORT_COUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_count)],
            CONFIRM_REPORT:    [CallbackQueryHandler(button_handler, pattern="^confirm_report$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(cancel_flow, pattern="^cancel_flow$"),
            CallbackQueryHandler(button_handler, pattern="^back$"),
        ],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("status",  status_cmd))
    app.add_handler(CommandHandler("session", session_cmd))
    app.add_handler(CommandHandler("unhost",  unhost_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("join",    join_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel",  cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",   "Main menu"),
            BotCommand("host",    "Add a Telegram account"),
            BotCommand("status",  "Bot status"),
            BotCommand("session", "List saved accounts"),
            BotCommand("unhost",  "Remove an account"),
            BotCommand("join",    "Join with all accounts"),
            BotCommand("cancel",  "Cancel current flow"),
            BotCommand("help",    "Help & commands"),
        ])

    app.post_init = on_startup
    app.run_polling()


if __name__ == "__main__":
    main()
