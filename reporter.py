import asyncio
import re
import logging
from telethon import TelegramClient
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    UserAlreadyParticipantError,
    FloodWaitError,
    ChatAdminRequiredError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)
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

SESSION_ERRORS = (
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)


def parse_message_link(target: str):
    m = re.match(r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)", target)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", target)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    return None, None


def is_invite_link(target: str) -> bool:
    return bool(re.match(r"https?://t\.me/\+[a-zA-Z0-9_-]+", target))


def extract_invite_hash(target: str) -> str:
    m = re.match(r"https?://t\.me/\+([a-zA-Z0-9_-]+)", target)
    return m.group(1) if m else target.lstrip("+")


async def report_once(client, target, reason_key, report_message) -> tuple[bool, str]:
    reason = REASON_MAP.get(reason_key, InputReportReasonSpam())
    try:
        chat_id, msg_id = parse_message_link(target)
        if chat_id is not None and msg_id is not None:
            entity = await client.get_entity(chat_id)
            result = await client(ReportRequest(
                peer=entity,
                id=[msg_id],
                reason=reason,
                message=report_message,
            ))
        else:
            entity = await client.get_entity(target)
            result = await client(ReportPeerRequest(
                peer=entity,
                reason=reason,
                message=report_message,
            ))
        return (True, "") if result else (False, "rejected_by_telegram")
    except SESSION_ERRORS as e:
        raise ConnectionError("session_expired") from e
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return False, f"flood_wait_{e.seconds}s"
    except Exception as e:
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

    total_expected = len(sessions) * count_per_id
    results = {
        "success": 0,
        "failed": 0,
        "expired": 0,
        "total_expected": total_expected,
        "errors": [],
    }

    async def worker(sdata: dict):
        phone = sdata["phone"]
        try:
            client = await build_client(sdata["session"])
            await client.connect()

            if not await client.is_user_authorized():
                await mark_expired(phone)
                results["expired"] += 1
                if progress_cb:
                    await progress_cb(results)
                await client.disconnect()
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
                except ConnectionError:
                    await mark_expired(phone)
                    results["expired"] += 1
                    break
                finally:
                    if progress_cb:
                        await progress_cb(results)
                # 1 report per second per ID
                await asyncio.sleep(1)

            await client.disconnect()
        except Exception as e:
            results["failed"] += count_per_id
            err = str(e)
            if err not in results["errors"]:
                results["errors"].append(err)
            if progress_cb:
                await progress_cb(results)

    # All IDs run simultaneously
    await asyncio.gather(*[worker(s) for s in sessions])
    return results


async def mass_join(
    sessions: list[dict],
    target: str,
    progress_cb=None,
) -> dict:
    from session_manager import build_client

    results = {"joined": 0, "already": 0, "failed": 0}

    async def worker(sdata: dict):
        phone = sdata["phone"]
        try:
            client = await build_client(sdata["session"])
            await client.connect()

            if not await client.is_user_authorized():
                await mark_expired(phone)
                results["failed"] += 1
                if progress_cb:
                    await progress_cb(results)
                await client.disconnect()
                return

            try:
                if is_invite_link(target):
                    hash_ = extract_invite_hash(target)
                    await client(ImportChatInviteRequest(hash_))
                else:
                    await client(JoinChannelRequest(target))
                results["joined"] += 1
            except UserAlreadyParticipantError:
                results["already"] += 1
            except Exception as e:
                err = str(e).lower()
                if "already" in err or "participant" in err:
                    results["already"] += 1
                else:
                    results["failed"] += 1
                    logger.warning(f"join error [{phone}]: {e}")

            await client.disconnect()
        except Exception as e:
            results["failed"] += 1
            logger.warning(f"worker error [{phone}]: {e}")

        if progress_cb:
            await progress_cb(results)

    await asyncio.gather(*[worker(s) for s in sessions])
    return results
