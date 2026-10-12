import re

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def clean_ai_text(value):
    """Remove Markdown emphasis markers from legacy structured answers."""
    text = str(value or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    return text


@register.filter
def render_source_citations(value, answer):
    """Link valid model citation markers without trusting model-generated HTML."""
    text = clean_ai_text(value)
    valid_orders = {source.citation_order for source in answer.sources.all()}
    escaped = escape(text)

    def citation_link(match):
        number = int(match.group(1))
        if number not in valid_orders:
            return match.group(0)
        return format_html(
            '<a class="inline-citation" href="#answer-{}-source-{}" aria-label="View source {}">[Source {}]</a>',
            answer.pk, number, number, number,
        )

    return mark_safe(re.sub(r"\[Source\s+(\d+)\]", citation_link, escaped, flags=re.IGNORECASE))
