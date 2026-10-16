from django.db import models

# Create your models here.
class Enrollment(models.Model):
    SOURCE_CHOICES = [
        ("EVENTXPRO", "EventXPro"),
        ("KIOSK", "Kiosk"),
        ("DIRECT", "Direct"),
    ]

    external_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default="DIRECT")
    photo_url = models.TextField(blank=True, null=True)
    embedding = models.BinaryField()              # 128 x float32, L2-normalized
    model_version = models.CharField(max_length=50, default="sface_2021dec")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.external_id} ({self.name or 'No Name'}) [{self.source}]"


class ActivityLog(models.Model):
    ACTIVITY_TYPES = [
        ("EVENTXPRO_SYNC", "EventXPro Sync"),
        ("KIOSK_SYNC", "Kiosk Sync"),
        ("KIOSK_VERIFY", "Kiosk Verify"),
        ("CHECK_FACE", "Direct Face Compare"),
        ("LIVE_RECOGNITION", "Live Recognition Check-in"),
        ("ENROLLMENT", "Face Enrollment"),
    ]

    STATUS_CHOICES = [
        ("SUCCESS", "Success"),
        ("FAILED", "Failed"),
        ("MATCH_FOUND", "Match Found"),
        ("NO_MATCH", "No Match"),
        ("COOLDOWN", "Cooldown / Anti-Passback"),
        ("SPOOF_BLOCKED", "Spoofing Blocked"),
    ]

    activity_type = models.CharField(max_length=40, choices=ACTIVITY_TYPES, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="SUCCESS", db_index=True)
    external_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    source = models.CharField(max_length=50, blank=True, null=True)
    score = models.FloatField(blank=True, null=True)
    margin = models.FloatField(blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {self.activity_type} - {self.status} ({self.external_id or 'N/A'})"


# Alias for backward-compatibility if referenced
CheckInLog = ActivityLog
