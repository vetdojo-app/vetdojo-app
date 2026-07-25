import re

from django import template

register = template.Library()


@register.filter
def clean_ai_text(value):
    """Remove Markdown emphasis markers from legacy structured answers."""
    text = str(value or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    return text
