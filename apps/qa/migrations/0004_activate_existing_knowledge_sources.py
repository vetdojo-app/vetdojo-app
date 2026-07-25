from django.db import migrations


def activate_existing_sources(apps, schema_editor):
    Profile = apps.get_model("qa", "KnowledgeSourceProfile")
    connection = schema_editor.connection
    table_names = connection.introspection.table_names()
    if "rag_source_documents" not in table_names:
        return

    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM rag_source_documents")
        source_ids = [row[0] for row in cursor.fetchall()]

    existing_ids = set(Profile.objects.filter(source_document_id__in=source_ids).values_list("source_document_id", flat=True))
    Profile.objects.filter(source_document_id__in=source_ids).update(status="completed", is_active=True)
    Profile.objects.bulk_create(
        [
            Profile(source_document_id=source_id, status="completed", is_active=True)
            for source_id in source_ids
            if source_id not in existing_ids
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):
    dependencies = [("qa", "0003_knowledgesourceprofile_knowledgeprocessingrun_and_more")]
    operations = [migrations.RunPython(activate_existing_sources, migrations.RunPython.noop)]
