# Dockerfile

FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Создаём директорию для данных (база SQLite)
RUN mkdir -p /data

# Запуск
CMD ["python", "bot.py"]
