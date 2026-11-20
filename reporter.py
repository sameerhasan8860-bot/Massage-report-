from telethon import TelegramClient
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.channels import ReportSpamRequest
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
    InputReportReasonGeoIrrelevant,
)
import asyncio
from session_manager import mark_expired

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

async def report_once(client: TelegramClient, target: str, reason_key: str, message_id: int = None) -> bool:
    try:
        entity = await client.get_entity(target)
        reason = REASON_MAP.get(reason_key, InputReportReasonSpam())
        await client(ReportPeerRequest(
            peer=entity,
            reason=reason,
            message="Reported via mass report system"
        ))
        return True
    except Exception as e:
        err = str(e).lower()
        if "auth" in err or "session" in err or "deactivated" in err:
            raise ConnectionError("session_expired")
        return False

async def mass_report(sessions: list[dict], target: str, reason_key: str, count_per_id: int, progress_cb=None):
    from session_manager import build_client
    results = {"success": 0, "failed": 0, "expired": 0}

    async def worker(sdata: dict, idx: int):
        phone = sdata["phone"]
        try:
            client = await build_client(sdata["session"], idx)
            await client.connect()
            if not await client.is_user_authorized():
                await mark_expired(phone)
                results["expired"] += 1
                return
            for _ in range(count_per_id):
                try:
                    ok = await report_once(client, target, reason_key)
                    if ok:
                        results["success"] += 1
                    else:
                        results["failed"] += 1
                    await asyncio.sleep(1)
                except ConnectionError:
                    await mark_expired(phone)
                    results["expired"] += 1
                    break
            await client.disconnect()
        except Exception:
            results["failed"] += 1

        if progress_cb:
            await progress_cb(results)

    tasks = [worker(s, i) for i, s in enumerate(sessions)]
    await asyncio.gather(*tasks)
    return results
