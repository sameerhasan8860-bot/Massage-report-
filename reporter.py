import asyncio
import re
import logging
from telethon import TelegramClient
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonChildAbuse,
    InputReportReasonCopyright,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails,
)
from session_manager import mark_expired

logger = logging.getLogger(__name__)

REASON_MAP = {
    "1":  InputReportReasonSpam(),
    "2a": InputReportReasonChildAbuse(),
    "2b": InputReportReasonChildAbuse(),
    "3a": InputReportReasonViolence(),
    "3b": InputReportReasonViolence(),
    "3c": InputReportReasonViolence(),
    "3d": InputReportReasonViolence(),
    "3e": InputReportReasonViolence(),
    "3f": InputReportReasonViolence(),
    "4a": InputReportReasonOther(),
    "4b": InputReportReasonIllegalDrugs(),
    "4c": InputReportReasonFake(),
    "4d": InputReportReasonFake(),
    "4e": InputReportReasonOther(),
    "4f": InputReportReasonOther(),
    "4g": InputReportReasonOther(),
    "5a": InputReportReasonChildAbuse(),
    "5b": InputReportReasonPornography(),
    "5c": InputReportReasonPornography(),
    "5d": InputReportReasonPornography(),
    "5e": InputReportReasonPornography(),
    "5f": InputReportReasonPornography(),
    "6a": InputReportReasonPersonalDetails(),
    "6b": InputReportReasonPersonalDetails(),
    "6c": InputReportReasonPersonalDetails(),
    "6d": InputReportReasonPersonalDetails(),
    "6e": InputReportReasonPersonalDetails(),
    "7a": InputReportReasonFake(),
    "7b": InputReportReasonFake(),
    "7c": InputReportReasonOther(),
    "7d": InputReportReasonOther(),
    "8":  InputReportReasonCopyright(),
    "9":  InputReportReasonOther(),
    "10": InputReportReasonOther(),
}


def parse_message_link(target: str):
    """
    Returns (chat_identifier, message_id) if target is a message link.
    chat_identifier is username string or int channel_id.
    Returns (None, None) if not a message link.
    """
    # https://t.me/username/123
    m = re.match(r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)", target)
    if m:
        return m.group(1), int(m.group(2))

    # https://t.me/c/1234567890/123  (private channel)
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", target)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))

    return None, None


def is_invite_link(target: str) -> bool:
    return bool(re.match(r"https?://t\.me/\+[a-zA-Z0-9_-]+", target))


def extract_invite_hash(target: str) -> str:
    m = re.match(r"https?://t\.me/\+([a-zA-Z0-9_-]+)", target)
    return m.group(1) if m else target.lstrip("+")


async def report_once(
    client: TelegramClient,
    target: str,
    reason_key: str,
    report_message: str,
) -> tuple[bool, str]:
    """
    Returns (success: bool, error_string: str)
    """
    reason = REASON_MAP.get(reason_key, InputReportReasonSpam())

    try:
        chat_id, msg_id = parse_message_link(target)

        if chat_id is not None and msg_id is not None:
            # --- Report a specific message ---
            try:
                entity = await client.get_entity(chat_id)
            except Exception:
                # Try joining public group if get_entity fails
                try:
                    await client(JoinChannelRequest(chat_id))
                    entity = await client.get_entity(chat_id)
                except Exception as je:
                    return False, f"entity_error: {je}"

            result = await client(ReportRequest(
                peer=entity,
                id=[msg_id],
                reason=reason,
                message=report_message,
            ))
            if result:
                return True, ""
            else:
                return False, "telegram_returned_false"

        else:
            # --- Report a peer (user / channel / group) ---
            # Resolve entity
            try:
                entity = await client.get_entity(target)
            except Exception as e:
                return False, f"entity_error: {e}"

            result = await client(ReportPeerRequest(
                peer=entity,
                reason=reason,
                message=report_message,
            ))
            if result:
                return True, ""
            else:
                return False, "telegram_returned_false"

    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["auth", "session", "deactivated", "user_deactivated", "session_revoked"]):
            raise ConnectionError("session_expired")
        logger.warning(f"report_once error: {e}")
        return False, str(e)


async def join_target(client: TelegramClient, target: str) -> tuple[bool, str]:
    """
    Join a channel/group. Returns (success, error).
    Handles both public @username links and private +hash invite links.
    """
    try:
        if is_invite_link(target):
            hash_ = extract_invite_hash(target)
            await client(ImportChatInviteRequest(hash_))
            return True, ""
        else:
            await client(JoinChannelRequest(target))
            return True, ""
    except Exception as e:
        err = str(e).lower()
        if "already" in err:
            return True, "already_member"
        return False, str(e)


async def mass_report(
    sessions: list[dict],
    target: str,
    reason_key: str,
    count_per_id: int,
    report_message: str,
    progress_cb=None,
) -> dict:
    from session_manager import build_client

    results = {"success": 0, "failed": 0, "expired": 0, "errors": []}

    async def worker(sdata: dict, idx: int):
        phone = sdata["phone"]
        try:
            client = await build_client(sdata["session"], idx)
            await client.connect()

            if not await client.is_user_authorized():
                await mark_expired(phone)
                results["expired"] += 1
                if progress_cb:
                    await progress_cb(results)
                return

            for _ in range(count_per_id):
                try:
                    ok, err = await report_once(client, target, reason_key, report_message)
                    if ok:
                        results["success"] += 1
                    else:
                        results["failed"] += 1
                        if err and err not in results["errors"]:
                            results["errors"].append(err)
                    await asyncio.sleep(1)
                except ConnectionError:
                    await mark_expired(phone)
                    results["expired"] += 1
                    break

            await client.disconnect()

        except Exception as e:
            results["failed"] += 1
            err = str(e)
            if err not in results["errors"]:
                results["errors"].append(err)

        if progress_cb:
            await progress_cb(results)

    tasks = [worker(s, i) for i, s in enumerate(sessions)]
    await asyncio.gather(*tasks)
    return results


async def mass_join(sessions: list[dict], target: str) -> dict:
    from session_manager import build_client

    results = {"joined": 0, "failed": 0, "already": 0}

    async def worker(sdata: dict, idx: int):
        try:
            client = await build_client(sdata["session"], idx)
            await client.connect()
            if not await client.is_user_authorized():
                results["failed"] += 1
                return
            ok, err = await join_target(client, target)
            if ok:
                if err == "already_member":
                    results["already"] += 1
                else:
                    results["joined"] += 1
            else:
                results["failed"] += 1
            await client.disconnect()
        except Exception:
            results["failed"] += 1

    tasks = [worker(s, i) for i, s in enumerate(sessions)]
    await asyncio.gather(*tasks)
    return results
