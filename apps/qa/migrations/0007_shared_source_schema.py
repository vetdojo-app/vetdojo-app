from django.db import migrations


POSTGRES_SQL = """
ALTER TABLE rag_source_documents ALTER COLUMN video_id DROP NOT NULL;
ALTER TABLE rag_source_documents ALTER COLUMN video_url DROP NOT NULL;

ALTER TABLE rag_chunks
    DROP CONSTRAINT IF EXISTS rag_chunks_source_id_chunk_index_embedding_model_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_chunks_source_type_identity
    ON rag_chunks (source_type, source_id, chunk_index, embedding_model);

CREATE OR REPLACE FUNCTION match_rag_chunks(
    query_embedding vector(1536),
    match_count int DEFAULT 5
)
RETURNS TABLE (
    chunk_id bigint,
    source_type text,
    source_id text,
    source_title text,
    source_url text,
    chunk_index integer,
    chunk_text text,
    similarity double precision,
    metadata jsonb
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        c.id,
        c.source_type,
        c.source_id,
        c.source_title,
        c.source_url,
        c.chunk_index,
        c.chunk_text,
        1 - (c.embedding <=> query_embedding),
        c.metadata
    FROM rag_chunks c
    INNER JOIN qa_knowledgesourceprofile p
        ON p.source_document_id = c.source_document_id
       AND p.is_active = TRUE
    ORDER BY c.embedding <=> query_embedding
    LIMIT match_count;
$$;
"""


def update_shared_schema(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    tables = connection.introspection.table_names()
    if "rag_source_documents" not in tables or "rag_chunks" not in tables:
        return
    with connection.cursor() as cursor:
        cursor.execute(POSTGRES_SQL)


class Migration(migrations.Migration):
    dependencies = [("qa", "0006_knowledgecategory_knowledgebasesettings")]
    operations = [migrations.RunPython(update_shared_schema, migrations.RunPython.noop)]
