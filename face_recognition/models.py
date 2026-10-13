from django.db import models

# Create your models here.
class Enrollment(models.Model):
    external_id = models.CharField(max_length=100, unique=True)
    embedding = models.BinaryField()              # 128 x float32, L2-normalized
    model_version = models.CharField(max_length=50, default="sface_2021dec")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)