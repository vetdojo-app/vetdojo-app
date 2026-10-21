from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("qa", "0015_ai_disclaimer_acceptance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VoiceConversation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("active", "Active"), ("ended", "Ended")], default="active", max_length=12)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="voice_conversations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddField(
            model_name="question",
            name="voice_conversation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="questions", to="qa.voiceconversation"),
        ),
        migrations.AddField(
            model_name="question",
            name="voice_turn_index",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(fields=("voice_conversation", "voice_turn_index"), name="unique_voice_conversation_turn"),
        ),
    ]
