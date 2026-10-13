from django.urls import path
from face_recognition.views import (
    RegisterView, RecognizeView, DashboardView, LoginView, LogoutView, SyncDataView, CheckFaceView
)

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("sync-data/", SyncDataView.as_view(), name="sync_data"),
    path("check-face/", CheckFaceView.as_view(), name="check_face"),
    path("register/", RegisterView.as_view(), name="register"),
    path("recognize/", RecognizeView.as_view(), name="recognize"),
]
