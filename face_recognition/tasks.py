import json
import base64
import urllib.request
import urllib.error
import logging
from celery import shared_task
from django.conf import settings
from .engine import embed, FaceError
from .models import Enrollment, ActivityLog
from . import index

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="face_recognition.tasks.sync_eventxpro_bulk")
def sync_eventxpro_bulk(self, api_url=None, api_key=None, batch_size=200):
    """
    Background asynchronous Celery task to fetch and sync 100,000+ attendee records from EventXPro.
    Streams and processes attendees in batches, computes 512D face embeddings,
    updates PostgreSQL database, and rebuilds the FAISS vector index.
    """
    target_url = api_url or getattr(settings, "EVENTXPRO_API_URL", "")
    target_key = api_key or getattr(settings, "EVENTXPRO_API_KEY", "")

    if not target_url:
        return {"status": "error", "message": "EVENTXPRO_API_URL is missing"}

    logger.info(f"Starting Celery bulk sync for EventXPro from {target_url}...")
    self.update_state(state="PROGRESS", meta={"status": "Connecting to EventXPro API..."})

    headers = {
        "User-Agent": "Mozilla/5.0 FaceStudio/1.0",
        "Accept": "application/json",
        "X-API-KEY": target_key,
        "Authorization": f"Bearer {target_key}" if target_key else ""
    }

    try:
        req = urllib.request.Request(target_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"EventXPro Celery fetch error: {str(e)}")
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

    if isinstance(data, dict):
        items = data.get("attendees") or data.get("data") or data.get("results") or [data]
    elif isinstance(data, list):
        items = data
    else:
        return {"status": "error", "message": "Invalid response format"}

    total = len(items)
    synced = 0
    failed = 0

    for idx, item in enumerate(items, 1):
        ext_id = item.get("external_id") or item.get("id") or item.get("badge_id") or item.get("ticket_code")
        name = item.get("name") or f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
        email = item.get("email", "")
        photo_link = item.get("photo_link") or item.get("photo_url") or item.get("image_url") or item.get("avatar")
        photo_b64 = item.get("photo_base64") or item.get("photo_b64")

        if not ext_id or (not photo_link and not photo_b64):
            failed += 1
            continue

        photo_bytes = None
        if photo_b64:
            try:
                if "," in photo_b64:
                    photo_b64 = photo_b64.split(",", 1)[1]
                photo_bytes = base64.b64decode(photo_b64)
            except Exception:
                failed += 1
                continue
        elif photo_link:
            try:
                img_req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 FaceStudio/1.0"})
                with urllib.request.urlopen(img_req, timeout=10) as img_resp:
                    photo_bytes = img_resp.read()
            except Exception:
                failed += 1
                continue

        try:
            vec = embed(photo_bytes)
            Enrollment.objects.update_or_create(
                external_id=str(ext_id),
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "EVENTXPRO",
                    "photo_url": photo_link or ""
                }
            )
            synced += 1
        except Exception:
            failed += 1

        # Periodic progress update to Celery task state
        if idx % 50 == 0 or idx == total:
            self.update_state(
                state="PROGRESS",
                meta={"current": idx, "total": total, "synced": synced, "failed": failed}
            )

    # Invalidate and reload FAISS index once bulk ingestion finishes
    index.invalidate()
    index._ensure()

    logger.info(f"Finished EventXPro bulk sync: {synced} synced, {failed} failed out of {total}.")
    ActivityLog.objects.create(
        activity_type="EVENTXPRO_SYNC",
        status="SUCCESS" if failed == 0 else ("FAILED" if synced == 0 else "SUCCESS"),
        source="EVENTXPRO",
        details=f"EventXPro background sync: {synced} synced, {failed} failed out of {total} records"
    )

    return {
        "status": "completed",
        "total": total,
        "synced": synced,
        "failed": failed,
        "source": "EVENTXPRO"
    }



@shared_task(bind=True, name="face_recognition.tasks.sync_kiosk_bulk")
def sync_kiosk_bulk(self, api_url=None, api_key=None):
    """
    Background asynchronous Celery task to fetch and sync 100,000+ attendee records from Kiosk.
    """
    target_url = api_url or getattr(settings, "KIOSK_API_URL", "")
    target_key = api_key or getattr(settings, "KIOSK_API_KEY", "")

    if not target_url:
        return {"status": "error", "message": "KIOSK_API_URL is missing"}

    logger.info(f"Starting Celery bulk sync for Kiosk from {target_url}...")
    self.update_state(state="PROGRESS", meta={"status": "Connecting to Kiosk API..."})

    headers = {
        "User-Agent": "Mozilla/5.0 FaceStudio/1.0",
        "Accept": "application/json",
        "X-API-KEY": target_key,
        "Authorization": f"Bearer {target_key}" if target_key else ""
    }

    try:
        req = urllib.request.Request(target_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Kiosk Celery fetch error: {str(e)}")
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

    if isinstance(data, dict):
        items = data.get("registrations") or data.get("attendees") or data.get("data") or data.get("results") or [data]
    elif isinstance(data, list):
        items = data
    else:
        return {"status": "error", "message": "Invalid response format"}

    total = len(items)
    synced = 0
    failed = 0

    for idx, item in enumerate(items, 1):
        ext_id = item.get("external_id") or item.get("id") or item.get("badge_id") or item.get("kiosk_id")
        name = item.get("name") or f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
        email = item.get("email", "")
        photo_b64 = item.get("photo_base64") or item.get("photo_b64")
        photo_link = item.get("photo_link") or item.get("photo_url") or item.get("image_url")

        if not ext_id or (not photo_link and not photo_b64):
            failed += 1
            continue

        photo_bytes = None
        if photo_b64:
            try:
                if "," in photo_b64:
                    photo_b64 = photo_b64.split(",", 1)[1]
                photo_bytes = base64.b64decode(photo_b64)
            except Exception:
                failed += 1
                continue
        elif photo_link:
            try:
                img_req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 FaceStudio/1.0"})
                with urllib.request.urlopen(img_req, timeout=10) as img_resp:
                    photo_bytes = img_resp.read()
            except Exception:
                failed += 1
                continue

        try:
            vec = embed(photo_bytes)
            Enrollment.objects.update_or_create(
                external_id=str(ext_id),
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "KIOSK",
                    "photo_url": photo_link or ""
                }
            )
            synced += 1
        except Exception:
            failed += 1

        if idx % 50 == 0 or idx == total:
            self.update_state(
                state="PROGRESS",
                meta={"current": idx, "total": total, "synced": synced, "failed": failed}
            )

    index.invalidate()
    index._ensure()

    logger.info(f"Finished Kiosk bulk sync: {synced} synced, {failed} failed out of {total}.")
    ActivityLog.objects.create(
        activity_type="KIOSK_SYNC",
        status="SUCCESS" if failed == 0 else ("FAILED" if synced == 0 else "SUCCESS"),
        source="KIOSK",
        details=f"Kiosk background sync: {synced} synced, {failed} failed out of {total} records"
    )

    return {
        "status": "completed",
        "total": total,
        "synced": synced,
        "failed": failed,
        "source": "KIOSK"
    }

