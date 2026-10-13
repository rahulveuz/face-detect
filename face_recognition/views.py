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


def superuser_required(view_func):
    """Decorator ensuring the user is authenticated and is a superuser."""
    return user_passes_test(lambda u: u.is_authenticated and u.is_superuser, login_url='/api/login/')(view_func)


@method_decorator(superuser_required, name='dispatch')
class DashboardView(View):
    def get(self, request):
        return render(request, "recognition/test.html", {"user": request.user})


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
            vec, box = embed_with_box(photo.read())
        except FaceError as e:
            return Response({"error": str(e)}, status=422)

        results = index.search(vec, k=2)
        if not results or results[0][1] < settings.MATCH_THRESHOLD:
            return Response({"match": False, "box": box,
                             "best_score": results[0][1] if results else None})
        return Response({"match": True, "box": box, "external_id": results[0][0],
                         "score": round(results[0][1], 4),
                         "runner_up_score": round(results[1][1], 4) if len(results) > 1 else None})