from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.utils.decorators import method_decorator
from django.views import View
from rest_framework.views import APIView
from rest_framework.response import Response
from .engine import embed, embed_with_box, FaceError
from .models import Enrollment, ActivityLog
from . import index
from rest_framework.permissions import BasePermission
from django.core.cache import cache
import urllib.request
import urllib.error
import json
import base64



COOLDOWN_SECONDS = 30  # Configurable cooldown window

def check_passback_cooldown(external_id: str, gate_id: str = "GATE_1") -> tuple[bool, str]:
    """
    Checks if a person just checked in recently.
    Returns (allowed: bool, reason: str).
    """
    cache_key = f"cooldown_{external_id}"
    last_event = cache.get(cache_key)

    if last_event:
        elapsed = last_event.get("seconds_ago", COOLDOWN_SECONDS)
        return False, f"Anti-passback: {external_id} was already checked in at {last_event['gate']}. Wait {COOLDOWN_SECONDS}s."

    # Store successful check-in timestamp
    cache.set(cache_key, {"gate": gate_id, "status": "CHECKED_IN"}, timeout=COOLDOWN_SECONDS)
    return True, ""

class DeviceKeyPermission(BasePermission):
    """
    Requires an authorized device key in the X-Device-Key header.
    Allows dashboard browser sessions (request.user.is_authenticated) to bypass it.
    """
    def has_permission(self, request, view):
        # Allow logged-in superuser via web console
        if request.user and request.user.is_authenticated:
            return True

        device_key = request.headers.get("X-Device-Key")
        authorized_keys = getattr(settings, "GATE_AUTHORIZED_KEYS", ["gate-secret-key-prod-2026"])
        return device_key in authorized_keys


class ThirdPartyApiKeyPermission(BasePermission):
    """
    Verifies that the caller provides the authorized third party API Key via:
    - X-API-KEY header
    - Authorization: Bearer <key>
    - or active authenticated session (dashboard console test)
    """
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True

        expected_key = getattr(settings, "THIRD_PARTY_API_KEY", "eventxpro-kiosk-secret-key-2026")
        api_key = request.headers.get("X-API-KEY") or request.headers.get("X-Api-Key")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token_val = auth_header.split(" ", 1)[1].strip()
                # Check static key match
                if token_val == expected_key:
                    return True
                # Check valid JWT access token
                try:
                    from rest_framework_simplejwt.tokens import AccessToken
                    validated_token = AccessToken(token_val)
                    return True
                except Exception:
                    return False

        return bool(api_key and api_key == expected_key)



def check_rate_limit(request, max_per_sec: int = 4) -> bool:
    """
    Sliding window rate-limiter: blocks callers sending > 4 frames per second.
    Returns True if allowed, False if throttled.
    """
    # Check X-Forwarded-For in case requests go through Nginx / Cloudflare / reverse proxy
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "unknown")

    cache_key = f"rl_cam_{ip}"
    current_count = cache.get(cache_key, 0)

    if current_count >= max_per_sec:
        return False

    cache.set(cache_key, current_count + 1, timeout=1)  # 1-second window
    return True


def superuser_required(view_func):
    """Decorator ensuring the user is authenticated and is a superuser."""
    return user_passes_test(lambda u: u.is_authenticated and u.is_superuser, login_url='/api/login/')(view_func)


@method_decorator(superuser_required, name='dispatch')
class DashboardView(View):
    def get(self, request):
        from django.utils import timezone
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        total_enrolled = Enrollment.objects.count()
        today_sync_count = Enrollment.objects.filter(updated_at__gte=today_start).count()
        
        # EventXPro stats & records
        eventxpro_total = Enrollment.objects.filter(source="EVENTXPRO").count()
        eventxpro_today = Enrollment.objects.filter(source="EVENTXPRO", updated_at__gte=today_start).count()
        eventxpro_records = Enrollment.objects.filter(source="EVENTXPRO").order_by("-updated_at")[:100]

        # Kiosk stats & records
        kiosk_total = Enrollment.objects.filter(source="KIOSK").count()
        kiosk_today = Enrollment.objects.filter(source="KIOSK", updated_at__gte=today_start).count()
        kiosk_records = Enrollment.objects.filter(source="KIOSK").order_by("-updated_at")[:100]


        # Unified Activity Logs (Syncs, Kiosk Verifications, Face Compares, Live Checks)
        today_activity_count = ActivityLog.objects.filter(created_at__gte=today_start).count()
        activity_logs = ActivityLog.objects.order_by("-created_at")[:200]

        # Overall sync records and dashboard recent enrollments
        sync_records = Enrollment.objects.order_by("-updated_at")[:100]
        recent_enrollments = Enrollment.objects.order_by("-updated_at")[:10]

        return render(request, "recognition/dashboard.html", {
            "user": request.user,
            "total_enrolled": total_enrolled,
            "today_sync_count": today_sync_count,
            "today_activity_count": today_activity_count,
            "activity_logs": activity_logs,
            "eventxpro_count": eventxpro_total,
            "eventxpro_total": eventxpro_total,
            "eventxpro_today": eventxpro_today,
            "eventxpro_records": eventxpro_records,
            "kiosk_count": kiosk_total,
            "kiosk_total": kiosk_total,
            "kiosk_today": kiosk_today,
            "kiosk_records": kiosk_records,
            "sync_records": sync_records,
            "recent_enrollments": recent_enrollments,
            "match_threshold": settings.MATCH_THRESHOLD,
            "api_key": getattr(settings, "THIRD_PARTY_API_KEY", "eventxpro-kiosk-secret-key-2026"),
        })






class SyncDataView(APIView):
    """API endpoint to re-index or synchronize face embeddings in memory."""
    def post(self, request):
        index.invalidate()
        index._ensure()
        total = len(index._id_map)
        return Response({
            "status": "success",
            "message": f"Vector search index successfully synced and reloaded with {total} enrolled profiles.",
            "enrolled_count": total,
        })



from django.views.decorators.csrf import csrf_exempt

@method_decorator(csrf_exempt, name='dispatch')
class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated and request.user.is_superuser:
            return redirect(request.GET.get("next") or "/api/")
        return render(request, "recognition/login.html", {"next": request.GET.get("next", "/api/")})

    def post(self, request):
        # Support both standard form-data and Postman raw JSON payloads
        u = request.POST.get("username", "").strip()
        p = request.POST.get("password", "")
        next_url = request.POST.get("next") or "/api/"

        if not u and request.body:
            try:
                body_json = json.loads(request.body.decode("utf-8"))
                u = body_json.get("username", "").strip()
                p = body_json.get("password", "")
                next_url = body_json.get("next") or next_url
            except Exception:
                pass

        user = authenticate(request, username=u, password=p)
        if user is None:
            return render(request, "recognition/login.html", {
                "error": "Invalid username or password.",
                "next": next_url,
            }, status=401)

        if not user.is_superuser:
            return render(request, "recognition/login.html", {
                "error": "Access denied. Only superusers are authorized to view this console.",
                "next": next_url,
            }, status=403)

        login(request, user)
        # If client is Postman or expects JSON, return access_token + refresh_token
        if request.headers.get("Accept") == "application/json" or "application/json" in request.headers.get("Content-Type", ""):
            from rest_framework_simplejwt.tokens import RefreshToken
            refresh = RefreshToken.for_user(user)
            access_token = str(refresh.access_token)

            return JsonResponse({
                "status": "success",
                "message": f"Successfully authenticated as {user.username}",
                "user": user.username,
                "is_superuser": user.is_superuser,
                "token_type": "Bearer",
                "access_token": access_token,
                "refresh_token": str(refresh)
            })

        return redirect(next_url)


class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect("/api/login/")

    def post(self, request):
        logout(request)
        return redirect("/api/login/")


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        external_id = request.data.get("external_id")
        photo = request.FILES.get("photo")
        if not external_id or not photo:
            return Response({"error": "external_id and photo are required"}, status=400)
        try:
            vec = embed(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)

        obj, created = Enrollment.objects.update_or_create(
            external_id=external_id, defaults={"embedding": vec.tobytes()})
        index.invalidate()

        ActivityLog.objects.create(
            activity_type="ENROLLMENT",
            status="SUCCESS",
            external_id=external_id,
            name=obj.name,
            source="DIRECT",
            details=f"Profile {'created' if created else 'updated'} directly via camera capture"
        )

        return Response({"external_id": external_id, "status": "created" if created else "updated"},

                        status=201 if created else 200)


class RecognizeView(APIView):
    authentication_classes = []
    permission_classes = []
    def post(self, request):
        # 🛡️ 1. Rate Limiting Check (Max 4 frames per second per IP)
        if not check_rate_limit(request, max_per_sec=4):
            return Response(
                {"error": "Too many requests. Camera stream throttled to 4 FPS."},
                status=429
            )
        photo = request.FILES.get("photo")
        if not photo:
            return Response({"error": "photo is required"}, status=400)
        try:
            vec, box, is_live, spoof_reason, landmarks = embed_with_box(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)
        # Anti-Spoofing Gate: If phone screen or paper photo is detected, reject match
        if not is_live:
            return Response({
                "match": False,
                "box": box,
                "landmarks": landmarks,
                "is_live": False,
                "spoof_reason": spoof_reason or "Spoofing attempt detected",
                "reason": "spoof_detected"
            })

        results = index.search(vec, k=2)
        if not results:
            return Response({
                "match": False,
                "box": box,
                "landmarks": landmarks,
                "is_live": True,
                "best_score": None
            })

        best_id, best_score = results[0][0], round(results[0][1], 4)
        runner_up_score = round(results[1][1], 4) if len(results) > 1 else -1.0
        margin = round(best_score - runner_up_score, 4) if runner_up_score >= 0 else 1.0

        # Margin check: if runner-up is too close, it's ambiguous
        AMBIGUITY_MARGIN = 0.05
        is_above_threshold = best_score >= settings.MATCH_THRESHOLD
        is_clear_winner = margin >= AMBIGUITY_MARGIN

        if not is_above_threshold:
            return Response({
                "match": False,
                "box": box,
                "landmarks": landmarks,
                "is_live": True,
                "best_score": best_score,
                "runner_up_score": runner_up_score if runner_up_score >= 0 else None,
                "reason": "below_threshold"
            })

        if not is_clear_winner and len(results) > 1:
            return Response({
                "match": False,
                "box": box,
                "landmarks": landmarks,
                "is_live": True,
                "best_score": best_score,
                "runner_up_score": runner_up_score,
                "margin": margin,
                "reason": "ambiguous_match"
            })

        # Retrieve enrollment profile details
        enrollment = Enrollment.objects.filter(external_id=best_id).first()
        person_name = enrollment.name if enrollment else ""
        person_source = enrollment.source if enrollment else "DIRECT"

        # Anti-passback cooldown & check-in log
        allowed, cooldown_reason = check_passback_cooldown(best_id, gate_id="CAMERA_LIVE")
        if allowed:
            ActivityLog.objects.create(
                activity_type="LIVE_RECOGNITION",
                status="MATCH_FOUND",
                external_id=best_id,
                name=person_name,
                source=person_source,
                score=best_score,
                margin=margin,
                details=f"Live camera verified attendance for {person_name or best_id} (Score: {best_score})"
            )

        return Response({
            "match": True,
            "box": box,
            "landmarks": landmarks,
            "is_live": True,
            "external_id": best_id,
            "name": person_name,
            "source": person_source,
            "score": best_score,
            "margin": margin,
            "allowed": allowed,
            "reason": cooldown_reason if not allowed else "authorized",
            "runner_up_score": runner_up_score if runner_up_score >= 0 else None
        })




class CheckFaceView(APIView):
    """
    Checks if an uploaded image matches any registered profile by comparing
    extracted face embeddings with all enrolled identities in the database.
    Includes confidence margin evaluation to prevent false collisions.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        photo = request.FILES.get("photo")
        if not photo:
            return Response({"error": "Photo image file is required"}, status=400)
        try:
            vec, box, is_live, spoof_reason, landmarks = embed_with_box(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)

        results = index.search(vec, k=5)
        if not results:
            return Response({
                "is_registered": False,
                "external_id": None,
                "best_score": None,
                "box": box,
                "is_live": is_live,
                "spoof_reason": spoof_reason,
                "candidates": []
            })


        best_id, best_score = results[0][0], round(results[0][1], 4)
        runner_up = round(results[1][1], 4) if len(results) > 1 else -1.0
        margin = round(best_score - runner_up, 4) if runner_up >= 0 else 1.0

        AMBIGUITY_MARGIN = 0.05
        is_registered = (best_score >= settings.MATCH_THRESHOLD) and (margin >= AMBIGUITY_MARGIN or len(results) == 1)

        candidates = [
            {
                "external_id": res[0],
                "score": round(res[1], 4),
                "is_match": res[1] >= settings.MATCH_THRESHOLD
            }
            for res in results
        ]

        # Log face comparison activity
        matched_enrollment = Enrollment.objects.filter(external_id=best_id).first() if is_registered else None
        ActivityLog.objects.create(
            activity_type="CHECK_FACE",
            status="MATCH_FOUND" if is_registered else "NO_MATCH",
            external_id=best_id if is_registered else None,
            name=matched_enrollment.name if matched_enrollment else None,
            source=matched_enrollment.source if matched_enrollment else "DIRECT",
            score=best_score,
            margin=margin,
            details=f"Direct face check: {'Matched ' + best_id + ' (score: ' + str(best_score) + ')' if is_registered else 'No match found'}"
        )

        return Response({
            "is_registered": is_registered,
            "external_id": best_id if is_registered else None,
            "best_score": best_score,
            "margin": margin,
            "is_ambiguous": (best_score >= settings.MATCH_THRESHOLD and not is_registered),
            "match_threshold": settings.MATCH_THRESHOLD,
            "box": box,
            "candidates": candidates,
        })



class EventXProSyncView(APIView):
    """
    Push synchronization endpoint for EventXPro (third-party integration).
    Receives JSON with attendee records (single object or list of objects):
    [
        {
            "external_id": "EX-101",
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_link": "https://example.com/photos/jane.jpg"  # (or photo_url)
        }
    ]
    Protected with ThirdPartyApiKeyPermission.
    """
    authentication_classes = []
    permission_classes = [ThirdPartyApiKeyPermission]

    def post(self, request):
        payload = request.data
        if isinstance(payload, dict):
            items = [payload]
        elif isinstance(payload, list):
            items = payload
        else:
            return Response({"error": "Payload must be a JSON object or array of objects"}, status=400)

        results = []
        success_count = 0
        error_count = 0

        for item in items:
            ext_id = item.get("external_id")
            name = item.get("name", "")
            email = item.get("email", "")
            photo_link = item.get("photo_link") or item.get("photo_url")

            if not ext_id:
                results.append({"external_id": None, "status": "failed", "error": "external_id is required"})
                error_count += 1
                continue

            if not photo_link:
                results.append({"external_id": ext_id, "status": "failed", "error": "photo_link (or photo_url) is required"})
                error_count += 1
                continue

            try:
                req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FaceStudio/1.0"})
                with urllib.request.urlopen(req, timeout=12) as response:
                    photo_bytes = response.read()
            except Exception as e:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Failed to download image from link: {str(e)}"})
                error_count += 1
                continue

            try:
                vec = embed(photo_bytes)
            except FaceError as fe:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Face detection error: {str(fe)}"})
                error_count += 1
                continue
            except Exception as e:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Internal embedding error: {str(e)}"})
                error_count += 1
                continue

            obj, created = Enrollment.objects.update_or_create(
                external_id=ext_id,
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "EVENTXPRO",
                    "photo_url": photo_link
                }
            )
            success_count += 1
            results.append({
                "external_id": ext_id,
                "name": name,
                "email": email,
                "status": "created" if created else "updated",
                "source": "EVENTXPRO"
            })

        if success_count > 0:
            index.invalidate()

        ActivityLog.objects.create(
            activity_type="EVENTXPRO_SYNC",
            status="SUCCESS" if error_count == 0 else ("FAILED" if success_count == 0 else "SUCCESS"),
            source="EVENTXPRO",
            details=f"EventXPro push sync completed: {success_count} synced, {error_count} failed out of {len(items)}"
        )

        return Response({
            "status": "completed",
            "total_received": len(items),
            "synced_count": success_count,
            "failed_count": error_count,
            "results": results
        }, status=200 if error_count == 0 else 207)



class KioskSyncView(APIView):
    """
    Automated / Push synchronization endpoint for on-site Kiosks.
    Receives JSON or multipart payloads with attendee registrations:
    JSON:
    [
      {
        "external_id": "KIOSK-001",
        "name": "Alex Mercer",
        "email": "alex@example.com",
        "photo_base64": "data:image/jpeg;base64,...", # or raw base64 or photo_link
        "photo_link": "http://..." # optional
      }
    ]
    Protected with ThirdPartyApiKeyPermission (or dashboard admin session).
    """
    authentication_classes = []
    permission_classes = [ThirdPartyApiKeyPermission]

    def post(self, request):
        payload = request.data
        if isinstance(payload, dict):
            items = [payload]
        elif isinstance(payload, list):
            items = payload
        else:
            return Response({"error": "Payload must be a JSON object or array of objects"}, status=400)

        results = []
        success_count = 0
        error_count = 0

        for item in items:
            ext_id = item.get("external_id")
            name = item.get("name", "")
            email = item.get("email", "")
            photo_b64 = item.get("photo_base64") or item.get("photo_b64")
            photo_link = item.get("photo_link") or item.get("photo_url")

            if not ext_id:
                results.append({"external_id": None, "status": "failed", "error": "external_id is required"})
                error_count += 1
                continue

            photo_bytes = None
            # 1. Direct file upload via multipart/form-data
            if request.FILES and (request.FILES.get("photo") or request.FILES.get("image") or request.FILES.get("file")):
                file_obj = request.FILES.get("photo") or request.FILES.get("image") or request.FILES.get("file")
                photo_bytes = file_obj.read()
            # 2. Base64 payload
            elif photo_b64:
                try:
                    if "," in photo_b64:
                        photo_b64 = photo_b64.split(",", 1)[1]
                    photo_bytes = base64.b64decode(photo_b64)
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Invalid base64 image: {str(e)}"})
                    error_count += 1
                    continue
            # 3. Photo URL link
            elif photo_link:
                try:
                    req = urllib.request.Request(photo_link, headers={"User-Agent": "FaceStudio/1.0"})
                    with urllib.request.urlopen(req, timeout=12) as response:
                        photo_bytes = response.read()
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Failed to download image: {str(e)}"})
                    error_count += 1
                    continue
            else:
                results.append({"external_id": ext_id, "status": "failed", "error": "Either photo_base64 or photo_link is required"})
                error_count += 1
                continue

            try:
                vec = embed(photo_bytes)
            except FaceError as fe:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Face error: {str(fe)}"})
                error_count += 1
                continue
            except Exception as e:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Embedding error: {str(e)}"})
                error_count += 1
                continue

            obj, created = Enrollment.objects.update_or_create(
                external_id=ext_id,
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "KIOSK",
                    "photo_url": photo_link or ""
                }
            )
            success_count += 1
            results.append({
                "external_id": ext_id,
                "name": name,
                "email": email,
                "status": "created" if created else "updated",
                "source": "KIOSK"
            })

        if success_count > 0:
            index.invalidate()

        ActivityLog.objects.create(
            activity_type="KIOSK_SYNC",
            status="SUCCESS" if error_count == 0 else ("FAILED" if success_count == 0 else "SUCCESS"),
            source="KIOSK",
            details=f"Kiosk sync completed: {success_count} synced, {error_count} failed out of {len(items)}"
        )

        return Response({
            "status": "completed",
            "total_received": len(items),
            "synced_count": success_count,
            "failed_count": error_count,
            "results": results
        }, status=200 if error_count == 0 else 207)



class KioskVerifyView(APIView):
    """
    Kiosk Face Verification Endpoint: /api/kiosk/verify/
    Uploads a photo of a person (multipart/form-data file, base64, or photo_link).
    Performs face detection, extraction, and FAISS vector matching.
    Returns the matched person's external_id, name, and profile details.
    """
    authentication_classes = []
    permission_classes = [ThirdPartyApiKeyPermission]

    def post(self, request):
        photo_bytes = None

        # 1. Direct file upload (multipart/form-data or binary)
        if request.FILES:
            photo_file = request.FILES.get("photo") or request.FILES.get("image") or request.FILES.get("file")
            if photo_file:
                photo_bytes = photo_file.read()

        # 2. Base64 payload (JSON or form)
        if not photo_bytes:
            photo_b64 = request.data.get("photo_base64") or request.data.get("photo_b64") or request.data.get("image_base64")
            if photo_b64:
                try:
                    if "," in photo_b64:
                        photo_b64 = photo_b64.split(",", 1)[1]
                    photo_bytes = base64.b64decode(photo_b64)
                except Exception as e:
                    return Response({"match": False, "error": f"Invalid base64 image: {str(e)}"}, status=400)

        # 3. Photo URL link
        if not photo_bytes:
            photo_link = request.data.get("photo_link") or request.data.get("photo_url")
            if photo_link:
                try:
                    req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 FaceStudio/1.0"})
                    with urllib.request.urlopen(req, timeout=12) as response:
                        photo_bytes = response.read()
                except Exception as e:
                    return Response({"match": False, "error": f"Failed to download image: {str(e)}"}, status=400)

        if not photo_bytes:
            return Response({
                "match": False,
                "error": "No photo provided. Upload a photo file, photo_base64, or photo_link."
            }, status=400)

        # Detect face & generate embedding
        try:
            vec, box, is_live, spoof_reason, landmarks = embed_with_box(photo_bytes)
        except FaceError as e:
            return Response({
                "match": False,
                "error": f"Face error: {str(e)}"
            }, status=422)

        # Vector search in FAISS
        results = index.search(vec, k=3)
        if not results:
            return Response({
                "match": False,
                "message": "No matching face found in enrolled database.",
                "external_id": None,
                "vector_dimensions": 128,
                "vector": [round(float(val), 6) for val in vec]
            }, status=200)

        best_id, best_score = results[0][0], round(results[0][1], 4)
        is_above_threshold = best_score >= settings.MATCH_THRESHOLD

        if not is_above_threshold:
            return Response({
                "match": False,
                "message": f"Face not found because confidence score ({best_score}) is below threshold ({settings.MATCH_THRESHOLD}).",
                "score": best_score,
                "external_id": None
            }, status=200)

        # Fetch matched profile
        enrollment = Enrollment.objects.filter(external_id=best_id).first()
        name = enrollment.name if enrollment else ""
        email = enrollment.email if enrollment else ""
        source = enrollment.source if enrollment else "UNKNOWN"
        vector_list = [round(float(val), 6) for val in vec]

        print(f"[KIOSK VERIFY] Matched person -> external_id: {best_id}, name: {name}, score: {best_score}")

        ActivityLog.objects.create(
            activity_type="KIOSK_VERIFY",
            status="MATCH_FOUND",
            external_id=best_id,
            name=name,
            source=source,
            score=best_score,
            details=f"Kiosk verified attendee {name or best_id} with score {best_score}"
        )

        return Response({
            "match": True,
            "external_id": best_id,
            "name": name,
            "email": email,
            "source": source,
            "confidence_score": best_score,
            "is_live": is_live,
            "vector": vector_list,
            "message": f"Successfully identified {name or best_id}."
        }, status=200)



class KioskEnrollView(APIView):
    """
    On-site kiosk registration endpoint.
    Accepts multipart/form-data:
      - external_id (required): Badge/ticket number
      - photo (required): Camera captured image file
      - name (optional): Attendee name
      - email (optional): Attendee email
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        external_id = request.data.get("external_id")
        photo = request.FILES.get("photo")
        name = request.data.get("name", "")
        email = request.data.get("email", "")

        if not external_id or not photo:
            return Response({"error": "external_id and photo file are required for kiosk enrollment."}, status=400)

        try:
            vec, box, is_live, spoof_reason, landmarks = embed_with_box(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)

        obj, created = Enrollment.objects.update_or_create(
            external_id=external_id,
            defaults={
                "embedding": vec.tobytes(),
                "name": name,
                "email": email,
                "source": "KIOSK"
            }
        )
        index.invalidate()

        return Response({
            "status": "created" if created else "updated",
            "external_id": external_id,
            "name": name,
            "email": email,
            "source": "KIOSK",
            "box": box,
            "is_live": is_live,
            "message": f"Successfully enrolled {external_id} via Kiosk."
        }, status=201 if created else 200)



class KioskRecognizeView(APIView):
    """
    Kiosk / Turnstile Real-Time Face Recognition Endpoint.
    """
    authentication_classes = []
    permission_classes = []
    def post(self, request):
        # 🛡️ 1. Rate Limiting Check (Max 4 frames per second per IP)
        if not check_rate_limit(request, max_per_sec=4):
            return Response(
                {"error": "Too many requests. Kiosk camera throttled to 4 FPS."},
                status=429
            )
        photo = request.FILES.get("photo")
        gate_id = request.data.get("gate_id", "KIOSK_1")
        if not photo:
            return Response({"error": "photo file is required"}, status=400)

        try:
            vec, box, is_live, spoof_reason, landmarks = embed_with_box(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)

        # Anti-spoofing check
        if not is_live:
            return Response({
                "match": False,
                "is_live": False,
                "spoof_reason": spoof_reason or "Spoofing attempt detected",
                "box": box,
                "reason": "spoof_detected"
            }, status=200)

        results = index.search(vec, k=3)
        if not results:
            return Response({
                "match": False,
                "is_live": True,
                "box": box,
                "reason": "no_match_found"
            }, status=200)

        best_id, best_score = results[0][0], round(results[0][1], 4)
        runner_up = round(results[1][1], 4) if len(results) > 1 else -1.0
        margin = round(best_score - runner_up, 4) if runner_up >= 0 else 1.0

        AMBIGUITY_MARGIN = 0.05
        is_above_threshold = best_score >= settings.MATCH_THRESHOLD
        is_clear_winner = margin >= AMBIGUITY_MARGIN or len(results) == 1

        if not is_above_threshold or not is_clear_winner:
            return Response({
                "match": False,
                "is_live": True,
                "best_score": best_score,
                "runner_up_score": runner_up if runner_up >= 0 else None,
                "margin": margin,
                "box": box,
                "reason": "below_threshold" if not is_above_threshold else "ambiguous_match"
            }, status=200)

        # Retrieve profile details
        enrollment = Enrollment.objects.filter(external_id=best_id).first()
        name = enrollment.name if enrollment else ""
        email = enrollment.email if enrollment else ""
        source = enrollment.source if enrollment else "UNKNOWN"

        # Anti-passback check
        allowed, cooldown_reason = check_passback_cooldown(best_id, gate_id=gate_id)

        # Log Check-in event
        ActivityLog.objects.create(
            activity_type="LIVE_RECOGNITION",
            status="MATCH_FOUND" if allowed else "COOLDOWN",
            external_id=best_id,
            name=name,
            source=source,
            score=best_score,
            margin=margin,
            details=f"Kiosk gate {gate_id}: {'Welcome, ' + (name or best_id) if allowed else cooldown_reason}"
        )


        return Response({
            "match": True,
            "external_id": best_id,
            "name": name,
            "email": email,
            "source": source,
            "score": best_score,
            "margin": margin,
            "box": box,
            "allowed": allowed,
            "reason": cooldown_reason if not allowed else "authorized",
            "message": f"Welcome, {name or best_id}!" if allowed else cooldown_reason
        }, status=200)



class EventXProFetchSyncView(APIView):
    """
    Outbound fetch endpoint triggered by the dashboard "Sync Datas" button for EventXPro.
    Calls the configured EVENTXPRO_API_URL using EVENTXPRO_API_KEY, parses attendees,
    downloads portrait photos, computes embeddings, and updates the local database.
    """
    authentication_classes = []
    permission_classes = [ThirdPartyApiKeyPermission]

    def post(self, request):
        api_url = getattr(settings, "EVENTXPRO_API_URL", "").strip()
        api_key = getattr(settings, "EVENTXPRO_API_KEY", "").strip()

        if not api_url:
            return Response({"error": "EVENTXPRO_API_URL is not configured in .env"}, status=400)

        # Build request to external EventXPro API
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "Mozilla/5.0 FaceStudio/1.0",
                "Accept": "application/json",
                "X-API-KEY": api_key,
                "Authorization": f"Bearer {api_key}" if api_key else ""
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "")
                raw_body = response.read().decode("utf-8")
                data = json.loads(raw_body)
        except urllib.error.HTTPError as he:
            return Response({
                "status": "error",
                "message": f"EventXPro server responded with HTTP {he.code}: {he.reason}",
                "detail": "Please verify your EVENTXPRO_API_URL and EVENTXPRO_API_KEY in .env."
            }, status=502)
        except urllib.error.URLError as ue:
            return Response({
                "status": "error",
                "message": f"Unable to reach EventXPro API: {ue.reason}",
                "detail": "Check network connectivity or your EVENTXPRO_API_URL in .env."
            }, status=504)
        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Failed while calling EventXPro API: {str(e)}"
            }, status=500)

        # Parse attendee items (supporting both { data: [...] } and direct list [...])
        if isinstance(data, dict):
            items = data.get("attendees") or data.get("data") or data.get("results") or [data]
        elif isinstance(data, list):
            items = data
        else:
            return Response({"status": "error", "message": "Unexpected data format from EventXPro API"}, status=502)

        # For 1 lakh+ (or > 100 items), dispatch to Celery background worker to avoid HTTP timeout
        if len(items) > 100 or request.data.get("async") is True:
            from face_recognition.tasks import sync_eventxpro_bulk
            task = sync_eventxpro_bulk.delay(api_url=api_url, api_key=api_key)
            return Response({
                "status": "queued",
                "message": f"Bulk sync of {len(items)} EventXPro attendee records queued in Celery background worker.",
                "task_id": task.id,
                "total_records": len(items)
            }, status=202)

        results = []
        success_count = 0
        error_count = 0

        for item in items:
            ext_id = item.get("external_id") or item.get("id") or item.get("badge_id") or item.get("ticket_code")
            name = item.get("name") or f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
            email = item.get("email", "")
            photo_link = item.get("photo_link") or item.get("photo_url") or item.get("image_url") or item.get("avatar")
            photo_b64 = item.get("photo_base64") or item.get("photo_b64")

            if not ext_id:
                results.append({"external_id": None, "status": "failed", "error": "Missing external_id in item"})
                error_count += 1
                continue

            photo_bytes = None
            if photo_b64:
                try:
                    if "," in photo_b64:
                        photo_b64 = photo_b64.split(",", 1)[1]
                    photo_bytes = base64.b64decode(photo_b64)
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Invalid base64: {str(e)}"})
                    error_count += 1
                    continue
            elif photo_link:
                try:
                    img_req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 FaceStudio/1.0"})
                    with urllib.request.urlopen(img_req, timeout=12) as img_resp:
                        photo_bytes = img_resp.read()
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Failed to download image: {str(e)}"})
                    error_count += 1
                    continue
            else:
                results.append({"external_id": ext_id, "status": "failed", "error": "No image URL or base64 provided"})
                error_count += 1
                continue

            try:
                vec = embed(photo_bytes)
            except FaceError as fe:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Face detection error: {str(fe)}"})
                error_count += 1
                continue
            except Exception as e:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Embedding error: {str(e)}"})
                error_count += 1
                continue

            obj, created = Enrollment.objects.update_or_create(
                external_id=str(ext_id),
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "EVENTXPRO",
                    "photo_url": photo_link or ""
                }
            )
            success_count += 1
            results.append({
                "external_id": str(ext_id),
                "name": name,
                "email": email,
                "status": "created" if created else "updated",
                "source": "EVENTXPRO"
            })

        if success_count > 0:
            index.invalidate()
            index._ensure()

        return Response({
            "status": "completed",
            "message": f"Successfully synced {success_count} profile(s) from EventXPro.",
            "total_received": len(items),
            "synced_count": success_count,
            "failed_count": error_count,
            "results": results
        }, status=200 if error_count == 0 else 207)


class KioskFetchSyncView(APIView):
    """
    Outbound fetch endpoint triggered by the dashboard "Sync Datas" button for Kiosk.
    Calls the configured KIOSK_API_URL using KIOSK_API_KEY, parses attendee records,
    downloads or decodes images, computes embeddings, and updates the local database.
    """
    authentication_classes = []
    permission_classes = [ThirdPartyApiKeyPermission]

    def post(self, request):
        api_url = getattr(settings, "KIOSK_API_URL", "").strip()
        api_key = getattr(settings, "KIOSK_API_KEY", "").strip()

        if not api_url:
            return Response({"error": "KIOSK_API_URL is not configured in .env"}, status=400)

        # Build request to external Kiosk provider API
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "Mozilla/5.0 FaceStudio/1.0",
                "Accept": "application/json",
                "X-API-KEY": api_key,
                "Authorization": f"Bearer {api_key}" if api_key else ""
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw_body = response.read().decode("utf-8")
                data = json.loads(raw_body)
        except urllib.error.HTTPError as he:
            return Response({
                "status": "error",
                "message": f"Kiosk server responded with HTTP {he.code}: {he.reason}",
                "detail": "Please verify your KIOSK_API_URL and KIOSK_API_KEY in .env."
            }, status=502)
        except urllib.error.URLError as ue:
            return Response({
                "status": "error",
                "message": f"Unable to reach Kiosk API: {ue.reason}",
                "detail": "Check network connectivity or your KIOSK_API_URL in .env."
            }, status=504)
        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Failed while calling Kiosk API: {str(e)}"
            }, status=500)

        # Parse registrations (supporting { registrations: [...] } or direct list [...])
        if isinstance(data, dict):
            items = data.get("registrations") or data.get("attendees") or data.get("data") or data.get("results") or [data]
        elif isinstance(data, list):
            items = data
        else:
            return Response({"status": "error", "message": "Unexpected data format from Kiosk API"}, status=502)

        # For 1 lakh+ (or > 100 items), dispatch to Celery background worker to avoid HTTP timeout
        if len(items) > 100 or request.data.get("async") is True:
            from face_recognition.tasks import sync_kiosk_bulk
            task = sync_kiosk_bulk.delay(api_url=api_url, api_key=api_key)
            return Response({
                "status": "queued",
                "message": f"Bulk sync of {len(items)} Kiosk registration records queued in Celery background worker.",
                "task_id": task.id,
                "total_records": len(items)
            }, status=202)

        results = []
        success_count = 0
        error_count = 0

        for item in items:
            ext_id = item.get("external_id") or item.get("id") or item.get("badge_id") or item.get("kiosk_id")
            name = item.get("name") or f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
            email = item.get("email", "")
            photo_b64 = item.get("photo_base64") or item.get("photo_b64")
            photo_link = item.get("photo_link") or item.get("photo_url") or item.get("image_url")

            if not ext_id:
                results.append({"external_id": None, "status": "failed", "error": "Missing external_id in item"})
                error_count += 1
                continue

            photo_bytes = None
            if photo_b64:
                try:
                    if "," in photo_b64:
                        photo_b64 = photo_b64.split(",", 1)[1]
                    photo_bytes = base64.b64decode(photo_b64)
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Invalid base64: {str(e)}"})
                    error_count += 1
                    continue
            elif photo_link:
                try:
                    img_req = urllib.request.Request(photo_link, headers={"User-Agent": "Mozilla/5.0 FaceStudio/1.0"})
                    with urllib.request.urlopen(img_req, timeout=12) as img_resp:
                        photo_bytes = img_resp.read()
                except Exception as e:
                    results.append({"external_id": ext_id, "status": "failed", "error": f"Failed to download image: {str(e)}"})
                    error_count += 1
                    continue
            else:
                results.append({"external_id": ext_id, "status": "failed", "error": "Either photo_base64 or photo_link is required"})
                error_count += 1
                continue

            try:
                vec = embed(photo_bytes)
            except FaceError as fe:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Face detection error: {str(fe)}"})
                error_count += 1
                continue
            except Exception as e:
                results.append({"external_id": ext_id, "status": "failed", "error": f"Embedding error: {str(e)}"})
                error_count += 1
                continue

            obj, created = Enrollment.objects.update_or_create(
                external_id=str(ext_id),
                defaults={
                    "embedding": vec.tobytes(),
                    "name": name,
                    "email": email,
                    "source": "KIOSK",
                    "photo_url": photo_link or ""
                }
            )
            success_count += 1
            results.append({
                "external_id": str(ext_id),
                "name": name,
                "email": email,
                "status": "created" if created else "updated",
                "source": "KIOSK"
            })

        if success_count > 0:
            index.invalidate()
            index._ensure()

        return Response({
            "status": "completed",
            "message": f"Successfully synced {success_count} profile(s) from Kiosk.",
            "total_received": len(items),
            "synced_count": success_count,
            "failed_count": error_count,
            "results": results
        }, status=200 if error_count == 0 else 207)

