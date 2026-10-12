from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("qa", "0012_journal_article_extraction")]

    operations = [
        migrations.AddField(
            model_name="answersource",
            name="citation_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
