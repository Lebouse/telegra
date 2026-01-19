# shared/utils.py

import datetime
import hashlib
from typing import Optional, Tuple
from pytz import UTC, timezone
from config import TIMEZONE


def next_recurrence_time(
    original: datetime.datetime,
    recurrence: str,
    last: datetime.datetime
) -> Optional[datetime.datetime]:
    """
    Вычисляет следующую дату публикации на основе типа повтора.
    Все входные даты — naive, но в UTC.
    Возвращает naive datetime в UTC или None.
    """
    if recurrence == 'once':
        return None
    elif recurrence == 'daily':
        return last + datetime.timedelta(days=1)
    elif recurrence == 'weekly':
        return last + datetime.timedelta(weeks=1)
    elif recurrence == 'monthly':
        # Переводим в локальный часовой пояс для корректного "месяца"
        local_last = UTC.localize(last).astimezone(TIMEZONE)
        year = local_last.year + (local_last.month // 12)
        month = (local_last.month % 12) + 1
        day = min(local_last.day, days_in_month(year, month))
        try:
            next_local = local_last.replace(year=year, month=month, day=day)
        except ValueError:
            # На случай, если день не существует (например, 31 апреля)
            next_local = local_last.replace(year=year, month=month, day=1) + datetime.timedelta(days=31)
        # Возвращаем в UTC
        return next_local.astimezone(UTC).replace(tzinfo=None)
    else:
        return None


def days_in_month(year: int, month: int) -> int:
    """Возвращает количество дней в месяце."""
    if month == 2:
        return 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
    return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]


def generate_task_hash(
    chat_id: int,
    text: Optional[str],
    photo_file_id: Optional[str],
    document_file_id: Optional[str],
    publish_at: str,
    recurrence: str
) -> str:
    """Генерирует уникальный хэш для предотвращения дублей."""
    content = f"{chat_id}|{text or ''}|{photo_file_id or ''}|{document_file_id or ''}|{publish_at}|{recurrence}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def parse_user_datetime(user_input: str) -> Tuple[datetime.datetime, datetime.datetime]:
    """
    Парсит строку вида 'ДД.ММ.ГГГГ ЧЧ:ММ' как локальное время.
    Возвращает (naive_local, utc_naive).
    """
    import re
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", user_input.strip())
    if not match:
        raise ValueError("Неверный формат даты. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")
    
    day, month, year, hour, minute = map(int, match.groups())
    naive_local = datetime.datetime(year, month, day, hour, minute)
    local_dt = TIMEZONE.localize(naive_local)
    utc_naive = local_dt.astimezone(UTC).replace(tzinfo=None)
    return naive_local, utc_naive
