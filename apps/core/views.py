from django.http import JsonResponse
from django.db import DatabaseError
from django.shortcuts import render

def home(request):
    transcript_count = None
    try:
        from apps.qa.models import RagChunk
        transcript_count = RagChunk.objects.values("source_id").distinct().count()
    except DatabaseError:
        # Keep the public homepage available during maintenance or SQLite-only UI development.
        pass
    return render(request, "core/home.html", {"transcript_count": transcript_count})

def health(request):
    return JsonResponse({"status": "ok"})
