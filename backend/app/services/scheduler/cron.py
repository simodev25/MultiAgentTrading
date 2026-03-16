from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class _CronField:
    allowed: frozenset[int]
    wildcard: bool


@dataclass(frozen=True)
class _CronSpec:
    minute: _CronField
    hour: _CronField
    day_of_month: _CronField
    month: _CronField
    day_of_week: _CronField

    def matches(self, dt: datetime) -> bool:
        if dt.minute not in self.minute.allowed:
            return False
        if dt.hour not in self.hour.allowed:
            return False
        if dt.month not in self.month.allowed:
            return False

        dom_match = dt.day in self.day_of_month.allowed
        # Python: Monday=0..Sunday=6. Cron: Sunday=0..Saturday=6.
        cron_dow = (dt.weekday() + 1) % 7
        dow_match = cron_dow in self.day_of_week.allowed

        if self.day_of_month.wildcard and self.day_of_week.wildcard:
            day_match = True
        elif self.day_of_month.wildcard:
            day_match = dow_match
        elif self.day_of_week.wildcard:
            day_match = dom_match
        else:
            day_match = dom_match or dow_match

        return day_match


def _normalize_space(expr: str) -> str:
    return ' '.join(str(expr or '').strip().split())


def _parse_number(value: str, minimum: int, maximum: int, *, day_of_week: bool = False) -> int:
    if not value:
        raise ValueError('Empty cron token')
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f'Invalid cron value "{value}"') from exc

    if day_of_week and number == 7:
        number = 0

    if number < minimum or number > maximum:
        raise ValueError(f'Cron value "{value}" out of range [{minimum}, {maximum}]')
    return number


def _expand_token(
    token: str,
    minimum: int,
    maximum: int,
    *,
    day_of_week: bool = False,
) -> set[int]:
    token = token.strip()
    if not token:
        raise ValueError('Empty cron token')

    step = 1
    base = token
    if '/' in token:
        base, raw_step = token.split('/', 1)
        if not raw_step:
            raise ValueError(f'Invalid cron step "{token}"')
        try:
            step = int(raw_step)
        except ValueError as exc:
            raise ValueError(f'Invalid cron step "{raw_step}"') from exc
        if step <= 0:
            raise ValueError(f'Cron step must be > 0 in "{token}"')

    if base == '*':
        start, end = minimum, maximum
    elif '-' in base:
        left, right = base.split('-', 1)
        start = _parse_number(left, minimum, maximum, day_of_week=day_of_week)
        end = _parse_number(right, minimum, maximum, day_of_week=day_of_week)
        if start > end:
            raise ValueError(f'Invalid cron range "{base}"')
    else:
        start = _parse_number(base, minimum, maximum, day_of_week=day_of_week)
        end = maximum if '/' in token else start

    return set(range(start, end + 1, step))


def _parse_field(
    field_expr: str,
    minimum: int,
    maximum: int,
    *,
    day_of_week: bool = False,
) -> _CronField:
    expr = field_expr.strip()
    if not expr:
        raise ValueError('Empty cron field')

    wildcard = expr == '*'
    values: set[int] = set()
    for part in expr.split(','):
        values.update(_expand_token(part, minimum, maximum, day_of_week=day_of_week))

    if not values:
        raise ValueError(f'Cron field "{field_expr}" resolved to empty set')
    return _CronField(allowed=frozenset(values), wildcard=wildcard)


def parse_cron_expression(expr: str) -> tuple[str, _CronSpec]:
    normalized = _normalize_space(expr)
    parts = normalized.split(' ')
    if len(parts) != 5:
        raise ValueError('Cron expression must have 5 fields: minute hour day-of-month month day-of-week')

    minute = _parse_field(parts[0], 0, 59)
    hour = _parse_field(parts[1], 0, 23)
    day_of_month = _parse_field(parts[2], 1, 31)
    month = _parse_field(parts[3], 1, 12)
    day_of_week = _parse_field(parts[4], 0, 6, day_of_week=True)
    return normalized, _CronSpec(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month=month,
        day_of_week=day_of_week,
    )


def validate_cron_expression(expr: str) -> str:
    normalized, _ = parse_cron_expression(expr)
    return normalized


def next_run_after(expr: str, after: datetime) -> datetime:
    normalized, spec = parse_cron_expression(expr)
    _ = normalized

    cursor = after
    if cursor.tzinfo is not None:
        cursor = cursor.astimezone(timezone.utc).replace(tzinfo=None)
    cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)

    max_lookahead_minutes = 60 * 24 * 366 * 5
    for _ in range(max_lookahead_minutes):
        if spec.matches(cursor):
            return cursor
        cursor += timedelta(minutes=1)

    raise ValueError('Unable to find the next run for cron expression within 5 years')
