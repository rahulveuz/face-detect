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