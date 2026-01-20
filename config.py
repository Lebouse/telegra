# config.py
import os
from pytz import timezone

# Загружаем переменные из окружения
BOT_TOKEN = os.environ["BOT_TOKEN"]
AUTHORIZED_USER_IDS = {int(x.strip()) for x in os.environ["AUTHORIZED_USER_IDS"].split(",")}
WEB_API_SECRET = os.environ.get("WEB_API_SECRET")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

# Путь к базе данных — теперь относительно директории проекта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "data", "scheduled_messages.db")

# Часовой пояс
TIMEZONE = timezone(os.getenv("TIMEZONE", "UTC"))

# Секрет для GitHub webhook
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
