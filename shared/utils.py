# shared/utils.py

import datetime
import hashlib
import re
from typing import Optional, List, Tuple
from pytz import UTC, timezone
from config import TIMEZONE


def escape_markdown_v2(text: str) -> str:
    """
    Экранирует спецсимволы для Telegram MarkdownV2.
    Сохраняет разметку (*жирный*, _курсив_, `код`), но защищает остальное.
    """
    if not text or not isinstance(text, str):
        return text

    # Временные маркеры для сохранения разметки
    bold_matches = []
    italic_matches = []
    code_matches = []

    def save_bold(m):
        bold_matches.append(m.group(0))
        return f"__BOLD{len(bold_matches) - 1}__"

    def save_italic(m):
        italic_matches.append(m.group(0))
        return f"__ITALIC{len(italic_matches) - 1}__"

    def save_code(m):
        code_matches.append(m.group(0))
        return f"__CODE{len(code_matches) - 1}__"

    # Сохраняем *жирный*
    temp = re.sub(r'\*([^*]+)\*', save_bold, text)
    # Сохраняем _курсив_
    temp = re.sub(r'_([^_]+)_', save_italic, temp)
    # Сохраняем `код`
    temp = re.sub(r'`([^`]+)`', save_code, temp)

    # Экранируем все спецсимволы MarkdownV2
    escaped = re.sub(r'([_\*\[\]\(\)~`>#+\-=|{}.!])', r'\\\1', temp)

    # Восстанавливаем разметку
    for i, match in enumerate(bold_matches):
        escaped = escaped.replace(f"__BOLD{i}__", match)
    for i, match in enumerate(italic_matches):
        escaped = escaped.replace(f"__ITALIC{i}__", match)
    for i, match in enumerate(code_matches):
        escaped = escaped.replace(f"__CODE{i}__", match)

    return escaped


def detect_media_type(file_id: str) -> Optional[str]:
    """
    Определяет тип медиа по file_id.
    Возвращает: 'photo', 'document' или None.
    Согласно официальному формату Telegram Bot API.
    """
    if not file_id or not isinstance(file_id, str):
        return None

    # Photo file_id начинается с AgAC или AAMC
    if file_id.startswith(("AgAC", "AAMC")):
        return "photo"
    # Document file_id начинается с BQAD или AwAD
    elif file_id.startswith(("BQAD", "AwAD")):
        return "document"
    else:
        return None


def generate_task_hash(
    chat_id: int,
    text: Optional[str],
    photo_file_id: Optional[str],
    document_file_id: Optional[str],
    publish_at: str,
    recurrence: str
) -> str:
    """Генерирует уникальный хэш для предотвращения дублирования задач."""
    content = f"{chat_id}|{text or ''}|{photo_file_id or ''}|{document_file_id or ''}|{publish_at}|{recurrence}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def next_recurrence_time(
    original: datetime.datetime,
    recurrence: str,
    last: datetime.datetime,
    weekly_days: Optional[List[int]] = None,
    monthly_days: Optional[List[int]] = None
) -> Optional[datetime.datetime]:
    """
    Вычисляет следующую дату публикации на основе правил повтора.
    Все входные даты — naive, но интерпретируются как UTC.
    Возвращает naive datetime в UTC или None.
    """
    if recurrence == 'once':
        return None
    elif recurrence == 'daily':
        return last + datetime.timedelta(days=1)
    elif recurrence == 'weekly':
        if not weekly_days:
            weekly_days = [0]  # Понедельник по умолчанию
        return find_next_weekday(last, weekly_days)
    elif recurrence == 'monthly':
        if not monthly_days:
            monthly_days = [1]
        return find_next_monthly_day(last, monthly_days)
    else:
        return None


def find_next_weekday(current: datetime.datetime, days: List[int]) -> datetime.datetime:
    """Находит следующий день недели из списка (0=пн, 6=вс)."""
    current_date = current.date()
    for i in range(1, 8):
        candidate_date = current_date + datetime.timedelta(days=i)
        if candidate_date.weekday() in days:
            return datetime.datetime.combine(candidate_date, current.time())
    # Fallback: через неделю
    return current + datetime.timedelta(weeks=1)


def find_next_monthly_day(current: datetime.datetime, days: List[int]) -> datetime.datetime:
    """Находит следующее число месяца из списка."""
    current_day = current.day
    current_month = current.month
    current_year = current.year

    # Фильтруем и сортируем допустимые числа
    valid_days = sorted([d for d in days if 1 <= d <= 31])
    if not valid_days:
        valid_days = [1]

    # Ищем в текущем месяце
    for d in valid_days:
        if d > current_day:
            try:
                return current.replace(day=d)
            except ValueError:
                # Некорректная дата (например, 31 апреля)
                continue

    # Переход в следующий месяц
    next_month = current_month + 1
    next_year = current_year
    if next_month > 12:
        next_month = 1
        next_year += 1

    # Берём первое доступное число
    for d in valid_days:
        try:
            return current.replace(year=next_year, month=next_month, day=d)
        except ValueError:
            continue

    # Fallback: 1-е число
    return current.replace(year=next_year, month=next_month, day=1)


def parse_user_datetime(user_input: str) -> Tuple[datetime.datetime, datetime.datetime]:
    """
    Парсит строку вида 'ДД.ММ.ГГГГ ЧЧ:ММ' как локальное время.
    Возвращает (naive_local, utc_naive).
    """
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", user_input.strip())
    if not match:
        raise ValueError("Неверный формат даты. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")

    day, month, year, hour, minute = map(int, match.groups())
    naive_local = datetime.datetime(year, month, day, hour, minute)
    local_dt = TIMEZONE.localize(naive_local)
    utc_naive = local_dt.astimezone(UTC).replace(tzinfo=None)
    return naive_local, utc_naive


def days_in_month(year: int, month: int) -> int:
    """Возвращает количество дней в месяце."""
    if month == 2:
        return 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
    return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
