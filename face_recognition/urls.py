from django.urls import path
from face_recognition.views import RegisterView, RecognizeView, DashboardView, LoginView, LogoutView

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", RegisterView.as_view(), name="register"),
    path("recognize/", RecognizeView.as_view(), name="recognize"),
]