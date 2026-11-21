import asyncio
import time
import logging
import re
import html
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop
from config import BOT_TOKEN, API_ID, API_HASH, OWNER_ID, OWNER_USERNAME
from access_manager import is_sudo, add_sudo, remove_sudo, get_sudo_users, remember_user
from telethon import TelegramClient
from telethon.sessions import StringSession
from session_manager import (
    save_session, get_all_sessions, delete_session,
    get_total_count, get_active_count, get_expired_count,
    get_session_by_phone, get_expired_sessions,
    check_session_authorized, refresh_all_sessions
)
from reporter import mass_report, mass_join
from proxy_manager import fetch_proxies, rotate_forever

logging.basicConfig(level=logging.INFO)

(
    ADD_PHONE, ADD_CODE, ADD_2FA,
    REPORT_TARGET, REPORT_REASON_CAT, REPORT_REASON_SUB,
    REPORT_MESSAGE, REPORT_COUNT, CONFIRM_REPORT
) = range(9)

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

tmp = {}

ALLOWED_USER_COMMANDS = {"/start", "/host", "/cancel", "/help"}
ALLOWED_USER_CALLBACKS = {"add_account", "cancel_flow", "help", "back"}

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def deny_non_sudo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = OWNER_USERNAME.lstrip("@")
    owner_link = f"@{html.escape(username)}" if username else "@NODDY_FUCKER"
    message = (
        "🔒 <b>ACCESS DENIED</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚫 You are not authorized to access this feature.\n\n"
        "This command is restricted to <b>Owner &amp; Sudo Users</b> only.\n\n"
        "👑 <b>Need Full Bot Access?</b>\n\n"
        "Contact the Bot Owner to request Sudo Access.\n\n"
        f"👤 <b>Owner:</b> {owner_link}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛡️ <b>SECURE ACCESS • AUTHORIZED USERS ONLY</b>"
    )
    if update.callback_query:
        await update.callback_query.answer("🔒 ACCESS DENIED", show_alert=True)
        if update.callback_query.message:
            await update.callback_query.message.reply_text(
                message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
    elif update.effective_message:
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

async def access_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        raise ApplicationHandlerStop

    # The owner is trusted by the configured OWNER_ID and must not depend on MongoDB.
    if is_owner(user.id):
        return

    try:
        await remember_user(user.id, user.full_name, user.username)
        user_is_sudo = await is_sudo(user.id)
    except Exception:
        logging.exception("Access database check failed for user %s", user.id)
        if update.callback_query:
            await update.callback_query.answer(
                "Access service temporarily unavailable. Try again shortly.",
                show_alert=True
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ <b>ACCESS SERVICE UNAVAILABLE</b>\n\n"
                "The access database could not be reached. Please try again shortly.",
                parse_mode=ParseMode.HTML
            )
        raise ApplicationHandlerStop

    if user_is_sudo:
        return

    if update.callback_query:
        if update.callback_query.data in ALLOWED_USER_CALLBACKS:
            return
        await deny_non_sudo(update, ctx)
        raise ApplicationHandlerStop

    message = update.effective_message
    text_value = message.text or "" if message else ""
    command = text_value.split(maxsplit=1)[0].split("@", 1)[0].lower() if text_value.startswith("/") else ""
    if command in ALLOWED_USER_COMMANDS:
        return
    await deny_non_sudo(update, ctx)
    raise ApplicationHandlerStop

async def sudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.effective_message.reply_text(
            "⛔ <b>OWNER ACCESS REQUIRED</b>\n\nOnly the bot owner can grant Sudo access.",
            parse_mode=ParseMode.HTML
        )
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text(
            "🛡️ <b>GRANT SUDO ACCESS</b>\n\n"
            "<b>Usage</b>  <code>/sudo USER_ID</code>\n"
            "<b>Example</b>  <code>/sudo 123456789</code>\n\n"
            "<i>Enter the numeric Telegram user ID.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    target_id = int(ctx.args[0])
    if target_id == OWNER_ID:
        await update.effective_message.reply_text(
            "👑 <b>ALREADY THE OWNER</b>\n\nThe bot owner already has full access.",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        chat = await ctx.bot.get_chat(target_id)
        full_name = chat.full_name or "Unknown"
        username = chat.username
    except Exception:
        full_name = "Unknown"
        username = None
    await add_sudo(target_id, full_name, username)
    username_text = f"@{html.escape(username)}" if username else "Not available"
    await update.effective_message.reply_text(
        "✅ <b>SUDO ACCESS GRANTED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Name:</b> {html.escape(full_name)}\n"
        f"🔗 <b>Username:</b> {username_text}\n"
        f"🆔 <b>User ID:</b> <code>{target_id}</code>\n\n"
        "<i>This user now has elevated bot access.</i>",
        parse_mode=ParseMode.HTML
    )


async def removesudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ <b>OWNER ACCESS REQUIRED</b>\n\nOnly the bot owner can revoke Sudo access.",
            parse_mode=ParseMode.HTML
        )
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.effective_message.reply_text(
            "🛡️ <b>REVOKE SUDO ACCESS</b>\n\n"
            "<b>Usage</b>  <code>/removesudo USER_ID</code>\n"
            "<b>Example</b>  <code>/removesudo 123456789</code>",
            parse_mode=ParseMode.HTML
        )
        return
    target_id = int(ctx.args[0])
    if target_id == OWNER_ID:
        await update.effective_message.reply_text(
            "⛔ <b>OWNER PROTECTED</b>\n\nThe bot owner cannot be removed from the access list.",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        removed = await remove_sudo(target_id)
    except Exception:
        logging.exception("Failed to remove Sudo user %s", target_id)
        await update.effective_message.reply_text(
            "⚠️ <b>DATABASE OPERATION FAILED</b>\n\nCould not update Sudo access. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        return
    if removed:
        await update.effective_message.reply_text(
            "✅ <b>SUDO ACCESS REVOKED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>User ID:</b> <code>{target_id}</code>\n\n"
            "<i>The user will no longer have elevated access.</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            "ℹ️ <b>NO SUDO RECORD FOUND</b>\n\n"
            f"No Sudo user exists with ID <code>{target_id}</code>.",
            parse_mode=ParseMode.HTML
        )


async def sudoinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        permitted = is_owner(user_id) or await is_sudo(user_id)
    except Exception:
        logging.exception("Sudo permission lookup failed for %s", user_id)
        await update.effective_message.reply_text(
            "⚠️ <b>ACCESS CHECK UNAVAILABLE</b>\n\nThe access database could not be reached. Please try again shortly.",
            parse_mode=ParseMode.HTML
        )
        return
    if not permitted:
        await update.effective_message.reply_text(
            "⛔ <b>RESTRICTED COMMAND</b>\n\nOnly the owner and Sudo users can view access details.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        try:
            owner_chat = await ctx.bot.get_chat(OWNER_ID)
            owner_name = owner_chat.full_name or "Bot Owner"
            owner_username = owner_chat.username or OWNER_USERNAME.lstrip("@") or None
        except Exception:
            owner_name = "Bot Owner"
            owner_username = OWNER_USERNAME.lstrip("@") or None

        owner_handle = f"@{html.escape(owner_username)}" if owner_username else "Not available"
        lines = [
            "👑 <b>BOT OWNER</b>",
            f"👤 <b>Name:</b> {html.escape(owner_name)}",
            f"🔗 <b>Username:</b> {owner_handle}",
            f"🆔 <b>User ID:</b> <code>{OWNER_ID}</code>",
            "",
            "🛡️ <b>SUDO USERS</b>",
            "━━━━━━━━━━━━━━━━━━"
        ]
        sudo_users = await get_sudo_users()
        if not sudo_users:
            lines.append("<i>No Sudo users have been added.</i>")
        for index, item in enumerate(sudo_users, 1):
            name = html.escape(str(item.get("name") or "Unknown"))
            username = item.get("username")
            handle = f"@{html.escape(str(username))}" if username else "Not available"
            lines.extend([
                f"<b>{index}. {name}</b>",
                f"   🔗 {handle}",
                f"   🆔 <code>{int(item['user_id'])}</code>",
                ""
            ])
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception:
        logging.exception("Failed to build Sudo information response")
        await update.effective_message.reply_text(
            "⚠️ <b>COULD NOT LOAD SUDO INFORMATION</b>\n\n"
            "The request reached the command, but the access records could not be displayed. "
            "Check the MongoDB connection and bot logs, then try again.",
            parse_mode=ParseMode.HTML
        )



_BUTTON_STYLE_INDEX = 0

def colored_button(text, **kwargs):
    """Create a Telegram inline button with a native Bot API color style."""
    global _BUTTON_STYLE_INDEX
    action = (str(text) + " " + str(kwargs.get("callback_data", ""))).lower()
    if any(word in action for word in ("cancel", "back", "stop", "delete")):
        style = "danger"
    elif any(word in action for word in ("confirm", "start reporting", "add account")):
        style = "success"
    else:
        # Telegram supports only three native button colors; rotate them for variety.
        styles = ("primary", "success", "danger")
        style = styles[_BUTTON_STYLE_INDEX % len(styles)]
        _BUTTON_STYLE_INDEX += 1
    return InlineKeyboardButton(text, style=style, **kwargs)



def cancel_kb():
    return InlineKeyboardMarkup([[colored_button("❌ Cancel / Stop", callback_data="cancel_flow")]])


def main_menu_kb():
    return InlineKeyboardMarkup([
        [colored_button("➕ Add Account", callback_data="add_account"),
         colored_button("📊 Status",      callback_data="status")],
        [colored_button("🚨 Mass Report", callback_data="start_report"),
         colored_button("📋 Sessions",    callback_data="sessions")],
        [colored_button("❔ Help", callback_data="help")],
    ])


def limited_menu_kb():
    return InlineKeyboardMarkup([
        [colored_button("➕ Add Account", callback_data="add_account")],
        [colored_button("❔ Help", callback_data="help")],
    ])

async def menu_for_user(user_id: int):
    return main_menu_kb() if is_owner(user_id) or await is_sudo(user_id) else limited_menu_kb()


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
            "❌ *Cancelled.*\n\nNo session was saved.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(uid)
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "❌ Cancelled.", reply_markup=await menu_for_user(uid)
        )
    return ConversationHandler.END


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Mass Report Bot*\n\n_Multi-account • Fast Parallel Reports • MongoDB Sessions_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=await menu_for_user(update.effective_user.id)
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    privileged = is_owner(uid) or await is_sudo(uid)
    help_text = (
        "🤖 <b>MASS REPORT BOT</b>\n"
        "<i>Command Center • Quick Reference</i>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>👤 ACCOUNT &amp; MENU</b>\n"
        "• <code>/start</code> — Open bot menu\n"
        "• <code>/host</code> — Host an account (OTP / optional 2FA)\n"
        "• <code>/session</code> — View your saved sessions\n"
        "• <code>/unhost +919876543210</code> — Remove your hosted session\n"
        "• <code>/cancel</code> — Cancel the current setup\n"
        "• <code>/help</code> — Show this command guide\n"
    )
    if privileged:
        help_text += (
            "\n<b>⚙️ BOT OPERATIONS</b>\n"
            "• <code>/status</code> — View session statistics\n"
            "• <code>/refresh</code> — Verify and refresh all saved session statuses\n"
            "• <code>/join @username</code> — Join a chat with hosted accounts\n"
            "• <code>/otp +919876543210</code> — Retrieve the latest Telegram OTP for a hosted account\n"
            "• <code>/sudoinfo</code> — View owner and Sudo directory\n"
        )
    if is_owner(uid):
        help_text += (
            "\n<b>👑 OWNER CONTROLS</b>\n"
            "• <code>/sudo USER_ID</code> — Grant Sudo access\n"
            "• <code>/removesudo USER_ID</code> — Revoke Sudo access\n"
        )
    if privileged:
        help_text += (
            "\n<b>🔐 ACCESS LEVELS</b>\n"
            "• Owner — Full access + Sudo management\n"
            "• Sudo — Full bot access + Sudo directory\n"
        )
    else:
        help_text += (
            "\n<b>🔐 ACCESS LEVEL</b>\n"
            "• Standard user — Start, host, cancel and help\n"
            "<i>Contact the bot owner if you need elevated access.</i>\n"
        )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            help_text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[colored_button("🔙 Back", callback_data="back")]])
        )
    else:
        await update.effective_message.reply_text(
            help_text, parse_mode=ParseMode.HTML,
            reply_markup=await menu_for_user(uid),
            disable_web_page_preview=True
        )



async def host_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📱 <b>ADD TELEGRAM ACCOUNT</b>\n\n"
        "Send your phone number in international format.\n"
        "Example: <code>+919876543210</code>\n\n"
        "Use /cancel to stop.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    return ADD_PHONE


async def session_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from session_manager import get_user_sessions
    sessions = await get_user_sessions(update.effective_user.id)
    lines = [f"`{i}. {item.get('phone', 'Unknown')}`" for i, item in enumerate(sessions, start=1)]
    message = "📋 *Saved Sessions*\n\n" + ("\n".join(lines) if lines else "No active sessions found.")
    await update.effective_message.reply_text(
        message, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
    )


async def unhost_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: `/unhost +919876543210`", parse_mode=ParseMode.MARKDOWN
        )
        return
    phone = ctx.args[0].strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.effective_message.reply_text(
            "❌ Invalid phone number. Include country code, e.g. `+919876543210`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    removed = await delete_session(phone, update.effective_user.id)
    if removed:
        await update.effective_message.reply_text(
            f"✅ Session `{phone}` removed.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )
    else:
        await update.effective_message.reply_text(
            f"ℹ️ No session found for `{phone}`.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb()
        )


def status_keyboard():
    return InlineKeyboardMarkup([
        [colored_button("🔄 Refresh", callback_data="refresh_status")],
        [colored_button("🔴 Expired IDs", callback_data="expired_ids"),
         colored_button("🔙 Back", callback_data="back")],
    ])


async def render_status(target, ctx: ContextTypes.DEFAULT_TYPE):
    total = await get_total_count()
    active = await get_active_count()
    expired = await get_expired_count()
    body = (
        "╔══════════════════╗\n"
        "║   📊 BOT STATUS   ║\n"
        "╚══════════════════╝\n\n"
        f"🟢 Active IDs   : {active}\n"
        f"🔴 Expired IDs  : {expired}\n"
        f"📦 Total IDs    : {total}\n\n"
        "<i>Use Refresh to verify saved sessions with Telegram.</i>"
    )
    await target.edit_message_text(
        body, parse_mode=ParseMode.HTML, reply_markup=status_keyboard()
    )


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await render_status(update.callback_query, ctx)
    else:
        total = await get_total_count()
        active = await get_active_count()
        expired = await get_expired_count()
        body = (
            "📊 <b>ACCOUNT STATUS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 <b>Active IDs:</b> <code>{active}</code>\n"
            f"🔴 <b>Expired IDs:</b> <code>{expired}</code>\n"
            f"📦 <b>Total Accounts:</b> <code>{total}</code>"
        )
        await update.effective_message.reply_text(
            body, parse_mode=ParseMode.HTML, reply_markup=status_keyboard()
        )


async def expired_ids_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = await get_expired_sessions()
    if not records:
        body = "🔴 <b>EXPIRED ACCOUNTS</b>\n━━━━━━━━━━━━━━━━━━\n\n<i>No expired accounts found.</i>"
    else:
        lines = []
        for index, item in enumerate(records, 1):
            phone = html.escape(str(item.get("phone") or "Unknown"))
            checked = item.get("status_checked_at")
            checked_text = checked.strftime("%Y-%m-%d %H:%M UTC") if checked else "Not checked yet"
            lines.append(
                f"<b>{index}. {phone}</b>\n"
                f"   🕒 <i>Last checked: {html.escape(checked_text)}</i>"
            )
        body = "🔴 <b>EXPIRED ACCOUNTS</b>\n━━━━━━━━━━━━━━━━━━\n\n" + "\n\n".join(lines)
    await update.callback_query.edit_message_text(
        body, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [colored_button("🔄 Refresh", callback_data="refresh_status")],
            [colored_button("🔙 Back to Status", callback_data="status")],
        ])
    )


async def refresh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text(
        "⏳ <b>REFRESHING ACCOUNT STATUS</b>\n\nChecking saved sessions with Telegram...",
        parse_mode=ParseMode.HTML
    )
    try:
        result = await refresh_all_sessions()
        await msg.edit_text(
            "✅ <b>STATUS REFRESH COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 Active: <code>{result['active']}</code>\n"
            f"🔴 Expired: <code>{result['expired']}</code>\n"
            f"⚠️ Check errors: <code>{result['errors']}</code>\n"
            f"📦 Total: <code>{result['total']}</code>\n\n"
            "<i>Temporary connection errors do not mark sessions as expired.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=status_keyboard()
        )
    except Exception:
        logging.exception("Session refresh failed")
        await msg.edit_text(
            "⚠️ <b>REFRESH FAILED</b>\n\nCould not complete the session check. Please try again.",
            parse_mode=ParseMode.HTML
        )


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid  = q.from_user.id

    if data == "back":
        await q.edit_message_text(
            "🤖 *Mass Report Bot*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(uid)
        )
        return ConversationHandler.END

    if data == "status":
        await status_cmd(update, ctx)
        return

    if data == "expired_ids":
        await expired_ids_cmd(update, ctx)
        return

    if data == "refresh_status":
        await q.edit_message_text(
            "⏳ <b>Refreshing saved account sessions...</b>",
            parse_mode=ParseMode.HTML
        )
        try:
            result = await refresh_all_sessions()
            await render_status(q, ctx)
        except Exception:
            logging.exception("Status refresh callback failed")
            await q.edit_message_text(
                "⚠️ <b>REFRESH FAILED</b>\\n\\nCould not verify the saved sessions. Please try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=status_keyboard()
            )
        return

    if data == "help":
        await help_cmd(update, ctx)
        return

    if data == "sessions":
        sessions = await get_all_sessions()
        lines = [f"`{i}. {s['phone']}`" for i, s in enumerate(sessions, start=1)] or ["No active sessions."]
        await q.edit_message_text(
            "📋 *Active Sessions:*\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[colored_button("🔙 Back", callback_data="back")]])
        )
        return

    if data == "add_account":
        await q.edit_message_text(
            "📱 Enter phone number with country code:\n\nExample: `+919876543210`\n\nUse /cancel to stop.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kb()
        )
        return ADD_PHONE

    if data == "start_report":
        await q.edit_message_text(
            "🎯 *Send target:*\n\n"
            "• `@username` — user, bot, channel\n"
            "• `https://t.me/username` — peer link\n"
            "• `https://t.me/username/123` — specific message\n"
            "• `https://t.me/c/CHANNELID/123` — private channel msg\n"
            "• Numeric Telegram user ID",
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
                "✍️ *Report message:*\n\nType what you want written in the report:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=cancel_kb()
            )
            return REPORT_MESSAGE
        kb = [[colored_button(label, callback_data=f"sub_{key}")] for key, label in subs]
        kb.append([colored_button("🔙 Back", callback_data="start_report")])
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
            "✍️ *Report message:*\n\nType what you want written in the report:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kb()
        )
        return REPORT_MESSAGE

    if data == "confirm_report":
        sessions = await get_all_sessions()
        if not sessions:
            await q.edit_message_text(
                "❌ *No active sessions.*\n\nAdd accounts first via ➕ Add Account.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_kb()
            )
            return ConversationHandler.END

        target         = tmp[uid]["target"]
        reason         = tmp[uid]["reason"]
        count          = tmp[uid]["count"]
        report_message = tmp[uid]["report_message"]
        total_ids      = len(sessions)
        total_expected = total_ids * count

        await q.edit_message_text("⏳ *Mass reporting started...*", parse_mode=ParseMode.MARKDOWN)

        sent_msg = await q.message.reply_text(
            f"📡 *Reporting in progress...*\n\n"
            f"✅ Success  : `0`\n"
            f"❌ Failed   : `0`\n"
            f"🔴 Expired  : `0`\n"
            f"📊 Done     : `0 / {total_expected}`",
            parse_mode=ParseMode.MARKDOWN
        )

        last_edit = {"t": 0}

        async def cb(r):
            now = time.time()
            done = r["success"] + r["failed"] + r["expired"]
            if now - last_edit["t"] >= 1 or done >= total_expected:
                last_edit["t"] = now
                try:
                    await sent_msg.edit_text(
                        f"📡 *Reporting in progress...*\n\n"
                        f"✅ Success  : `{r['success']}`\n"
                        f"❌ Failed   : `{r['failed']}`\n"
                        f"🔴 Expired  : `{r['expired']}`\n"
                        f"📊 Done     : `{done} / {total_expected}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

        result = await mass_report(
            sessions, target, reason, count, report_message, progress_cb=cb
        )

        err_summary = ""
        if result.get("errors"):
            unique_errors = list(set(result["errors"]))[:2]
            err_summary = "\n⚠️ `" + "` | `".join(unique_errors) + "`"

        await sent_msg.edit_text(
            "╔══════════════════╗\n"
            "║   📊 REPORT DONE  ║\n"
            "╚══════════════════╝\n\n"
            f"🎯 Target   : `{target}`\n"
            f"✅ Success  : `{result['success']}`\n"
            f"❌ Failed   : `{result['failed']}`\n"
            f"🔴 Expired  : `{result['expired']}`\n"
            f"👥 Total IDs: `{total_ids}`\n"
            f"📊 Total    : `{total_ids * count}`"
            f"{err_summary}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(uid)
        )
        tmp.pop(uid, None)
        return ConversationHandler.END

    return None


# --- Add Account Flow ---
async def recv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    phone = update.message.text.strip()

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        await update.message.reply_text(
            "❌ Invalid phone number. Include country code, e.g. +919876543210.",
            reply_markup=cancel_kb()
        )
        return ADD_PHONE

    existing = await get_session_by_phone(phone)
    if existing:
        try:
            is_active = await check_session_authorized(existing["session"])
        except Exception:
            logging.exception("Could not verify existing session for %s", phone)
            await update.message.reply_text(
                "⚠️ This number already has a saved session, but its status could not be verified. "
                "Use /refresh and try again.",
                reply_markup=cancel_kb()
            )
            return ADD_PHONE

        if is_active:
            await update.message.reply_text(
                f"ℹ️ <b>{html.escape(phone)}</b> — This number is already hosted and its session is active.",
                parse_mode=ParseMode.HTML,
                reply_markup=await menu_for_user(uid)
            )
            return ConversationHandler.END

        await update.message.reply_text(
            f"⚠️ <b>{html.escape(phone)}</b> has an expired session.\n"
            "You can continue to re-authorize this account.",
            parse_mode=ParseMode.HTML
        )

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await client.connect()
        await client.send_code_request(phone)
        tmp[uid] = {"client": client, "phone": phone}
        await update.message.reply_text(
            "🔑 <b>OTP VERIFICATION</b>\n\nEnter the Telegram login code you received.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return ADD_CODE
    except Exception as e:
        logging.exception("Phone verification request failed for %s", phone)
        if client.is_connected():
            await client.disconnect()
        await update.message.reply_text(
            "❌ <b>Could not send login code.</b>\nPlease verify the number and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
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
            f"✅ Account `{data['phone']}` added!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(uid)
        )
        tmp.pop(uid, None)
        return ConversationHandler.END
    except Exception as e:
        if "SessionPasswordNeeded" in str(e) or "2FA" in str(e) or "password" in str(e).lower():
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
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await menu_for_user(uid)
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
    kb = [[colored_button(v[0], callback_data=f"cat_{k}")] for k, v in REASONS.items()]
    await update.message.reply_text(
        f"📂 *Select report category:*\n\n🎯 Target: `{target}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return REPORT_REASON_CAT


async def recv_report_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
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
        f"✍️ Message        : `{t['report_message'][:60]}{'...' if len(t['report_message']) > 60 else ''}`\n"
        f"🔢 Per ID         : `{count}`\n"
        f"👥 Active IDs     : `{len(sessions)}`\n"
        f"📊 Total reports  : `{len(sessions) * count}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [colored_button("✅ Start Reporting", callback_data="confirm_report"),
             colored_button("❌ Cancel",          callback_data="back")]
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

    total = len(sessions)
    msg = await update.message.reply_text(
        f"⏳ *Joining* `{target}`\n\n"
        f"✅ Joined  : `0`\n"
        f"🔄 Already : `0`\n"
        f"❌ Failed  : `0`\n"
        f"📊 Done    : `0 / {total}`",
        parse_mode=ParseMode.MARKDOWN
    )

    last_edit = {"t": 0}

    async def progress_cb(r):
        now  = time.time()
        done = r["joined"] + r["already"] + r["failed"]
        if now - last_edit["t"] >= 1 or done >= total:
            last_edit["t"] = now
            try:
                await msg.edit_text(
                    f"⏳ *Joining* `{target}`\n\n"
                    f"✅ Joined  : `{r['joined']}`\n"
                    f"🔄 Already : `{r['already']}`\n"
                    f"❌ Failed  : `{r['failed']}`\n"
                    f"📊 Done    : `{done} / {total}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

    result = await mass_join(sessions, target, progress_cb=progress_cb)

    await msg.edit_text(
        "╔══════════════════╗\n"
        "║    ✅ JOIN DONE   ║\n"
        "╚══════════════════╝\n\n"
        f"🎯 Target  : `{target}`\n"
        f"✅ Joined  : `{result['joined']}`\n"
        f"🔄 Already : `{result['already']}`\n"
        f"❌ Failed  : `{result['failed']}`\n"
        f"👥 Total   : `{total}`",
        parse_mode=ParseMode.MARKDOWN
    )


async def otp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /otp +919876543210")
        return

    phone = ctx.args[0].strip()

    db_sessions = await get_all_sessions()
    matched = next((s for s in db_sessions if s.get("phone") == phone), None)

    if not matched:
        await update.message.reply_text(
            f"❌ `{phone}` bot mein hosted nahi hai.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    msg = await update.message.reply_text(
        f"⏳ `{phone}` ka latest OTP fetch kar raha hoon...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        from session_manager import build_client
        client = await build_client(matched["session"])
        await client.connect()

        if not await client.is_user_authorized():
            await msg.edit_text(
                f"❌ `{phone}` ka session expire ho gaya hai.",
                parse_mode=ParseMode.MARKDOWN
            )
            await client.disconnect()
            return

        # 777000 = Telegram ka official OTP sender
        messages = await client.get_messages(777000, limit=5)

        if not messages:
            await msg.edit_text(
                "❌ Koi OTP message nahi mila.",
                parse_mode=ParseMode.MARKDOWN
            )
            await client.disconnect()
            return

        latest   = messages[0]
        text     = latest.message or "(empty)"
        date_str = latest.date.strftime("%Y-%m-%d %H:%M:%S UTC") if latest.date else "unknown"

        await msg.edit_text(
            f"📱 *{phone}* ka latest OTP message:\n\n"
            f"`{text}`\n\n"
            f"🕐 `{date_str}`",
            parse_mode=ParseMode.MARKDOWN
        )
        await client.disconnect()

    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)


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

    app.add_handler(MessageHandler(filters.COMMAND, access_guard), group=-1)
    app.add_handler(CallbackQueryHandler(access_guard), group=-1)

    app.add_handler(CommandHandler("sudo", sudo_cmd))
    app.add_handler(CommandHandler("removesudo", removesudo_cmd))
    app.add_handler(CommandHandler("sudoinfo", sudoinfo_cmd))
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("status",  status_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("session", session_cmd))
    app.add_handler(CommandHandler("unhost",  unhost_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("join",    join_cmd))
    app.add_handler(CommandHandler("otp",     otp_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel",  cancel_flow))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        await fetch_proxies()
        asyncio.create_task(rotate_forever())
        await app.bot.set_my_commands([
            BotCommand("start",   "Main menu"),
            BotCommand("host",    "Add/host a Telegram account"),
            BotCommand("status",  "Bot status"),
            BotCommand("refresh", "Refresh hosted account status"),
            BotCommand("session", "List saved sessions"),
            BotCommand("unhost",  "Remove a saved session by phone"),
            BotCommand("join",    "Join with all accounts"),
            BotCommand("cancel",  "Cancel current flow"),
            BotCommand("help",    "Show all commands"),
            BotCommand("sudo", "Grant sudo access (owner only)"),
            BotCommand("removesudo", "Remove sudo access (owner only)"),
            BotCommand("sudoinfo", "Show owner and sudo users"),
        ])

    app.post_init = on_startup
    app.run_polling()


if __name__ == "__main__":
    main()