from django.db import migrations


def create_admin_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="ADMIN")


def remove_admin_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="ADMIN").delete()


class Migration(migrations.Migration):
    dependencies = [("qa", "0004_activate_existing_knowledge_sources")]
    operations = [migrations.RunPython(create_admin_group, remove_admin_group)]
