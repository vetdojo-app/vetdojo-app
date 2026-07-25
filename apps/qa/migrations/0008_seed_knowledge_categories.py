from django.db import migrations


def seed_categories(apps, schema_editor):
    Category = apps.get_model("qa", "KnowledgeCategory")
    for name in (
        "Anaesthesia",
        "Cardiology",
        "Dentistry",
        "Dermatology",
        "Diagnostic Imaging",
        "Emergency and Critical Care",
        "Internal Medicine",
        "Neurology",
        "Oncology",
        "Ophthalmology",
        "Orthopaedics",
        "Soft Tissue Surgery",
    ):
        Category.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [("qa", "0007_shared_source_schema")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
