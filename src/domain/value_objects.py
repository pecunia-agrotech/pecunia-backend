"""Canonical units and rounding policy. No binary floats are stored."""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .exceptions import DomainException

ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")


def real_to_cents(value: Decimal | str | int) -> int:
    if isinstance(value, (float, bool)):
        raise DomainException("Use um decimal, texto decimal ou inteiro para converter valores monetários.")
    try:
        decimal = Decimal(value)
        if not decimal.is_finite() or abs(decimal) > MAX_MONEY:
            raise DomainException("Valor monetário fora do limite permitido.")
        return int((decimal * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as error:
        raise DomainException("Valor monetário inválido.") from error


def cents_to_real(value: int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainException("O valor em centavos deve ser inteiro.")
    return Decimal(value) / 100


def month_bounds(month: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(month + "-01")
        if start.strftime("%Y-%m") != month:
            raise ValueError
        return start, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    except ValueError as error:
        raise DomainException("Mês inválido. Use o formato AAAA-MM.") from error


def allocate_fixed(total_cents: int, day: date) -> int:
    count = calendar.monthrange(day.year, day.month)[1]
    quotient, remainder = divmod(total_cents, count)
    return quotient + int(day.day > count - remainder)
