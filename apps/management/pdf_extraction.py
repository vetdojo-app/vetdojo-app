import base64
import json
import re
from collections import Counter
from dataclasses import dataclass

from django.conf import settings
from openai import OpenAI
from pypdf import PdfReader


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}


@dataclass(frozen=True)
class ExtractedPage:
    pdf_page_number: int
    printed_page_number: str
    raw_text: str
    cleaned_text: str
    warnings: tuple[str, ...]


def normalize_pdf_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    for source, replacement in LIGATURES.items():
        text = text.replace(source, replacement)
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _edge_lines(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[:2] + lines[-2:] if lines else []


def repeated_edge_lines(raw_pages):
    counts = Counter(line for text in raw_pages for line in set(_edge_lines(text)) if len(line) <= 160)
    threshold = max(2, (len(raw_pages) + 1) // 2)
    return {line for line, count in counts.items() if count >= threshold}


def detect_printed_page_number(raw_text):
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for line in lines[:3] + lines[-3:]:
        match = re.match(r"^(\d{1,4})(?:\s|$)", line) or re.search(r"(?:^|\s)(\d{1,4})$", line)
        if match:
            return match.group(1)
    return ""


def extract_pdf_pages(file_handle):
    file_handle.seek(0)
    reader = PdfReader(file_handle, strict=False)
    raw_pages = [(page.extract_text() or "").strip() for page in reader.pages]
    repeated = repeated_edge_lines(raw_pages)
    pages = []
    for number, raw_text in enumerate(raw_pages, 1):
        warnings = []
        lines = [line for line in raw_text.splitlines() if line.strip() not in repeated]
        cleaned = normalize_pdf_text("\n".join(lines))
        if len(cleaned) < 100:
            warnings.append("Low extractable text on this page.")
        pages.append(ExtractedPage(
            pdf_page_number=number,
            printed_page_number=detect_printed_page_number(raw_text),
            raw_text=raw_text,
            cleaned_text=cleaned,
            warnings=tuple(warnings),
        ))
    file_handle.seek(0)
    return pages


def pages_as_source_text(pages):
    sections = []
    for page in pages:
        label = f"PDF page {page.pdf_page_number}"
        if page.printed_page_number:
            label += f"; printed page {page.printed_page_number}"
        sections.append(f"[{label}]\n{page.cleaned_text}")
    return "\n\n".join(sections)


def analyze_article_metadata(article):
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    with article.pdf_file.open("rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    schema = {
        "type": "object",
        "properties": {
            "article_title": {"type": "string"},
            "authors": {"type": "string"},
            "journal": {"type": "string"},
            "publication_year": {"type": ["integer", "null"]},
            "volume": {"type": "string"},
            "issue": {"type": "string"},
            "article_pages": {"type": "string"},
            "doi": {"type": "string"},
            "abstract": {"type": "string"},
            "quality_warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "article_title", "authors", "journal", "publication_year", "volume", "issue",
            "article_pages", "doi", "abstract", "quality_warnings",
        ],
        "additionalProperties": False,
    }
    response = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=120, max_retries=2).responses.create(
        model=settings.OPENAI_PDF_MODEL,
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "filename": article.original_filename,
                    "file_data": f"data:application/pdf;base64,{encoded}",
                    "detail": "low",
                },
                {
                    "type": "input_text",
                    "text": (
                        "Extract bibliographic metadata from this veterinary journal article. "
                        "Do not infer missing values. Report extraction or reading-order concerns in quality_warnings."
                    ),
                },
            ],
        }],
        text={"format": {"type": "json_schema", "name": "journal_article_metadata", "strict": True, "schema": schema}},
    )
    return json.loads(response.output_text)
