from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.utils.decorators import method_decorator
from django.views import View
from rest_framework.views import APIView
from rest_framework.response import Response
from .engine import embed, embed_with_box, FaceError
from .models import Enrollment
from . import index
from rest_framework.permissions import BasePermission
from django.core.cache import cache

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


def check_rate_limit(request, max_per_sec: int = 4) -> bool:
    """
    Sliding window rate-limiter: blocks callers sending > 4 frames per second.
    Returns True if allowed, False if throttled.
    """
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
        total_enrolled = Enrollment.objects.count()
        recent_enrollments = Enrollment.objects.order_by("-updated_at")[:8]
        return render(request, "recognition/dashboard.html", {
            "user": request.user,
            "total_enrolled": total_enrolled,
            "recent_enrollments": recent_enrollments,
            "match_threshold": settings.MATCH_THRESHOLD,
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



class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated and request.user.is_superuser:
            return redirect(request.GET.get("next") or "/api/")
        return render(request, "recognition/login.html", {"next": request.GET.get("next", "/api/")})

    def post(self, request):
        u = request.POST.get("username", "").strip()
        p = request.POST.get("password", "")
        next_url = request.POST.get("next") or "/api/"

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
        return Response({"external_id": external_id, "status": "created" if created else "updated"},
                        status=201 if created else 200)


class RecognizeView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
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

        return Response({
            "match": True,
            "box": box,
            "landmarks": landmarks,
            "is_live": True,
            "external_id": best_id,
            "score": best_score,
            "margin": margin,
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
