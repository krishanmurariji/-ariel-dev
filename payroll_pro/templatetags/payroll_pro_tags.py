"""
payroll_pro template tags
"""

from django import template

register = template.Library()


def _currency_settings():
    """(symbol, position) from the app's Payroll Settings (prefix/suffix)."""
    try:
        from payroll.models.tax_models import PayrollSettings

        settings = PayrollSettings.objects.first()
        if settings and settings.currency_symbol:
            return settings.currency_symbol, settings.position or "postfix"
    except Exception:
        pass
    return "$", "postfix"


def _format_currency(amount: float, decimals: int) -> str:
    symbol, position = _currency_settings()
    formatted = f"{amount:,.{decimals}f}"
    if position == "prefix":
        return f"{symbol} {formatted}"
    return f"{formatted} {symbol}"


@register.filter
def pp_money(value):
    """Amount with the configured currency symbol & position, spaced."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return _format_currency(amount, 2)


@register.filter
def pp_money0(value):
    """Amount with the configured currency symbol & position, no decimals."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return _format_currency(amount, 0)


@register.filter
def get_item(dictionary, key):
    """Dict lookup by variable key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.filter
def month_name(month_number):
    import calendar

    try:
        return calendar.month_name[int(month_number)]
    except Exception:
        return month_number


@register.filter
def pct(value):
    """0.9166 -> '91.66%'"""
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"
