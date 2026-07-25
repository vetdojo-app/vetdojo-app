from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username
