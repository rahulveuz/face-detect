from django.urls import path
from face_recognition.views import (
    RegisterView, RecognizeView, DashboardView, LoginView, LogoutView, SyncDataView, CheckFaceView,
    EventXProSyncView, EventXProFetchSyncView, KioskEnrollView, KioskRecognizeView, KioskSyncView, KioskFetchSyncView,
    KioskVerifyView
)

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("sync-data/", SyncDataView.as_view(), name="sync_data"),
    path("check-face/", CheckFaceView.as_view(), name="check_face"),
    path("register/", RegisterView.as_view(), name="register"),
    path("recognize/", RecognizeView.as_view(), name="recognize"),
    path("eventxpro/sync/", EventXProSyncView.as_view(), name="eventxpro_sync"),
    path("eventxpro/fetch-sync/", EventXProFetchSyncView.as_view(), name="eventxpro_fetch_sync"),
    path("kiosk/verify/", KioskVerifyView.as_view(), name="kiosk_verify"),
    path("kiosk/sync/", KioskSyncView.as_view(), name="kiosk_sync"),
    path("kiosk/fetch-sync/", KioskFetchSyncView.as_view(), name="kiosk_fetch_sync"),
    path("kiosk/enroll/", KioskEnrollView.as_view(), name="kiosk_enroll"),
    path("kiosk/recognize/", KioskRecognizeView.as_view(), name="kiosk_recognize"),
]
