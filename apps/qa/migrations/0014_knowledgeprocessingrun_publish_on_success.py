from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("qa", "0013_answersource_citation_metadata")]

    operations = [
        migrations.AddField(
            model_name="knowledgeprocessingrun",
            name="publish_on_success",
            field=models.BooleanField(default=False),
        ),
    ]
