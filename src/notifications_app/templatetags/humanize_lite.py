from django import template

register = template.Library()


@register.filter
def compact(value):
    """12 -> 12, 1234 -> 1.2k, 2500000 -> 2.5M."""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return value
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(n) >= size:
            text = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(n)
