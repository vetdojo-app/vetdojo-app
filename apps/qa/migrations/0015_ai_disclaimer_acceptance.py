from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("qa", "0014_knowledgeprocessingrun_publish_on_success"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIDisclaimerAcceptance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("disclaimer_version", models.CharField(max_length=40)),
                ("content_hash", models.CharField(max_length=64)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("text", "Text assistant"),
                            ("voice", "Voice assistant"),
                        ],
                        max_length=12,
                    ),
                ),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_disclaimer_acceptances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-accepted_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "disclaimer_version"),
                        name="unique_user_disclaimer_version",
                    )
                ],
            },
        ),
    ]
