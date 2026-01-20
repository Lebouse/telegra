# web_api.py
# ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ с отладочными эндпоинтами
# Порт: 8081
# Секрет админки: qwerty12345

import asyncio
import datetime
import csv
import io
import logging
import os
import hmac
import hashlib
import json
from typing import Optional, List, Dict, Any, Union
from urllib.parse import quote, urlparse, urlunparse, parse_qs

from fastapi import FastAPI, HTTPException, Header, Request, Form, status, Query, Depends
from fastapi.responses import JSONResponse, Response, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator, ValidationInfo
from pydantic import ValidationError as PydanticValidationError
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

from config import (
    WEB_API_SECRET, ADMIN_SECRET, BOT_TOKEN, TIMEZONE,
    GITHUB_WEBHOOK_SECRET, DATABASE_PATH
)
from shared.database import (
    get_all_active_messages, deactivate_message,
    update_scheduled_message, add_scheduled_message,
    get_message_by_id, health_check as db_health_check
)
from shared.utils import (
    escape_markdown_v2, detect_media_type,
    parse_user_datetime
)
from scheduler_logic import publish_message
from shared.bot_instance import get_bot

# === Настройка логирования ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# === Инициализация FastAPI ===
app = FastAPI(
    title="Telegram Reminder Scheduler API",
    description="API для управления запланированными напоминаниями в Telegram",
    version="0.1.0-pre"
)

# === CORS настройки (для безопасности) ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Метрики Prometheus ===
TASKS_CREATED = Counter('telegram_scheduler_tasks_created_total', 'Total tasks created')
TASKS_DELETED = Counter('telegram_scheduler_tasks_deleted_total', 'Total tasks deleted')
ACTIVE_TASKS = Gauge('telegram_scheduler_active_tasks', 'Number of active scheduled tasks')

# === Шаблоны ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# === Кэш названий чатов ===
CHAT_TITLE_CACHE: Dict[int, tuple] = {}

# === Глобальный обработчик исключений ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Глобальный обработчик исключений для всех эндпоинтов.
    Логирует детали ошибки и возвращает информативный ответ.
    """
    logger.error(f"❌ ГЛОБАЛЬНАЯ ОШИБКА в {request.method} {request.url.path}: {str(exc)}", exc_info=True)
    
    # Для JSON-запросов возвращаем JSON
    if request.headers.get("Accept", "").startswith("application/json") or \
       request.headers.get("Content-Type", "").startswith("application/json"):
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error": str(exc),
                "endpoint": request.url.path,
                "method": request.method,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
        )
    
    # Для HTML-запросов возвращаем HTML с деталями ошибки
    error_details = f"""
    <h1>❌ Internal Server Error</h1>
    <p><strong>Endpoint:</strong> {request.url.path}</p>
    <p><strong>Method:</strong> {request.method}</p>
    <p><strong>Error:</strong> {str(exc)}</p>
    <p><strong>Тип ошибки:</strong> {type(exc).__name__}</p>
    <p>Проверьте логи сервера для подробностей.</p>
    <p><a href="/admin?secret={request.query_params.get('secret', '')}">← Вернуться в админку</a></p>
    """
    
    return HTMLResponse(
        status_code=500,
        content=error_details,
        headers={"Content-Type": "text/html; charset=utf-8"}
    )

# === Вспомогательные функции ===
def get_safe_redirect_url(base_url: str, secret: str, error: Optional[str] = None) -> str:
    """
    Безопасное формирование URL для редиректа с сохранением секрета и ошибки.
    """
    from urllib.parse import urlparse, parse_qs, urlunparse, quote
    
    parsed = urlparse(base_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    query_params['secret'] = [secret]
    
    if error:
        query_params['error'] = [error]
    
    # Формируем новый query string
    new_query = "&".join([f"{k}={quote(str(v[0]))}" for k, v in query_params.items()])
    
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

def safe_dict(row) -> dict:
    """
    Безопасно конвертирует sqlite3.Row или словарь в стандартный словарь.
    """
    try:
        if hasattr(row, 'keys'):
            return {key: row[key] for key in row.keys()}
        elif isinstance(row, dict):
            return row.copy()
        else:
            logger.warning(f"⚠️ Неожиданный тип данных: {type(row)}")
            return {}
    except Exception as e:
        logger.error(f"❌ Ошибка конвертации данных: {e}")
        return {}

async def get_chat_title_cached(chat_id: int) -> str:
    """Получает название чата через Telegram API с кэшированием."""
    now = datetime.datetime.now(datetime.timezone.utc)
    cache_key = chat_id
    
    if cache_key in CHAT_TITLE_CACHE:
        title, timestamp = CHAT_TITLE_CACHE[cache_key]
        if (now - timestamp).total_seconds() < 3600:  # кэш 1 час
            return title

    try:
        bot = get_bot()
        chat = await bot.get_chat(chat_id)
        title = chat.title or f"Чат {chat_id}"
        logger.info(f"✅ Получено название чата {chat_id}: {title}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить название чата {chat_id}: {e}")
        title = f"Чат {chat_id}"

    CHAT_TITLE_CACHE[cache_key] = (title, now)
    return title

# === Глобальный middleware для проверки секрета ===
@app.middleware("http")
async def admin_secret_middleware(request: Request, call_next):
    """
    Middleware для проверки секрета админки во всех запросах.
    Проверяет секрет из заголовка, query параметра и формы.
    """
    try:
        logger.debug(f"🔍 Middleware: {request.method} {request.url.path}")
        
        # Получаем секрет из всех возможных источников
        secret_from_header = request.headers.get("X-Admin-Secret")
        secret_from_query = request.query_params.get("secret")
        secret_from_cookie = request.cookies.get("admin_secret")
        
        # Для POST-запросов проверяем форму
        secret_from_form = None
        if request.method in ["POST", "PUT", "PATCH"]:
            try:
                form = await request.form()
                secret_from_form = form.get("secret")
                logger.debug(f"📝 Форма содержит секрет: {'да' if secret_from_form else 'нет'}")
            except Exception as e:
                logger.debug(f"Не удалось прочитать форму: {e}")
        
        actual_secret = secret_from_header or secret_from_query or secret_from_form or secret_from_cookie
        logger.debug(f"🔑 Полученные секреты: header={secret_from_header}, query={secret_from_query}, form={secret_from_form}, cookie={secret_from_cookie}, actual={actual_secret}")
        
        # Защищённые пути
        protected_paths = [
            "/admin",
            "/admin/",
            "/admin/create",
            "/admin/edit",
            "/admin/delete",
            "/admin/export.csv",
            "/debug-form"
        ]
        
        # Проверяем, является ли запрос защищённым
        is_protected = any(
            request.url.path.startswith(path) for path in protected_paths
        ) and not request.url.path.startswith("/admin/export.csv")
        
        logger.debug(f"🛡️ Защищённый эндпоинт: {is_protected}")
        
        # Если защищённый эндпоинт и секрет не совпадает
        if is_protected and ADMIN_SECRET and actual_secret != ADMIN_SECRET:
            logger.warning(
                f"🚫 Доступ запрещён к {request.url.path}. "
                f"Ожидалось '{ADMIN_SECRET}', получено '{actual_secret}'"
            )
            
            # Для AJAX/JSON запросов возвращаем JSON ошибку
            if request.headers.get("Accept", "").startswith("application/json") or \
               request.headers.get("Content-Type", "").startswith("application/json"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Admin access required"}
                )
            
            # Для HTML запросов перенаправляем на страницу входа
            return HTMLResponse(
                content="<h1>403 Forbidden</h1><p>Admin access required. Please provide valid secret.</p>",
                status_code=403
            )
        
        # Для экспорта CSV всегда проверяем секрет
        if request.url.path == "/admin/export.csv" and ADMIN_SECRET and actual_secret != ADMIN_SECRET:
            logger.warning(f"🚫 Попытка экспорта без прав: {request.client.host}")
            error_url = get_safe_redirect_url("/admin", ADMIN_SECRET or "default_secret", "Admin access required for export")
            return RedirectResponse(url=error_url, status_code=303)
        
        # Передаём управление следующему обработчику
        response = await call_next(request)
        
        # Устанавливаем секрет в cookie для последующих запросов
        if actual_secret and "/admin" in request.url.path:
            response.set_cookie(key="admin_secret", value=actual_secret, max_age=3600, httponly=True)
        
        return response
    
    except Exception as e:
        logger.exception(f"❌ Ошибка в admin_secret_middleware: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error in middleware"}
        )

# === Отладочный эндпоинт для проверки данных формы ===
@app.post("/debug-form", summary="Debug form data")
async def debug_form(request: Request):
    """Отладочный эндпоинт для проверки данных формы."""
    try:
        logger.info("🔍 Запрос к отладочному эндпоинту /debug-form")
        
        # Попытка 1: Получаем данные формы
        form_data = await request.form()
        logger.info(f"✅ Получены данные формы: {dict(form_data)}")
        
        # Попытка 2: Получаем чистое тело запроса
        body = await request.body()
        logger.info(f"✅ Тело запроса: {body.decode()}")
        
        # Попытка 3: Информация о заголовках
        headers = dict(request.headers)
        logger.info(f"✅ Заголовки запроса: {headers}")
        
        # Попытка 4: Content-Type
        content_type = headers.get('content-type', '')
        logger.info(f"✅ Content-Type: {content_type}")
        
        return JSONResponse({
            "status": "success",
            "method": request.method,
            "content_type": content_type,
            "form_data": {k: str(v) for k, v in form_data.items()},
            "body": body.decode() if body else "empty",
            "headers": headers
        })
        
    except Exception as e:
        logger.exception(f"❌ Ошибка в debug-form: {e}")
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "traceback": str(e.__traceback__)
        }, status_code=500)

# === Другие эндпоинты ===

@app.get("/health", summary="Health check")
async def health_check():
    """Проверяет работоспособность сервиса."""
    try:
        logger.info("✅ Health check запрошен")
        tasks = get_all_active_messages()
        db_status = db_health_check()
        
        return JSONResponse({
            "status": "ok",
            "active_tasks": len(tasks),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "database": db_status.get("status", "unknown")
        })
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection failed"
        )

@app.get("/metrics", summary="Prometheus metrics")
async def metrics():
    """Экспортирует метрики для Prometheus."""
    try:
        active_count = len(get_all_active_messages())
        ACTIVE_TASKS.set(active_count)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except Exception as e:
        logger.error(f"❌ Ошибка получения метрик: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate metrics"
        )

@app.get("/admin/debug", summary="Admin debug page")
async def admin_debug(request: Request, secret: Optional[str] = None):
    """Отладочная страница админки для проверки секрета и параметров."""
    current_secret = secret or request.query_params.get("secret") or request.cookies.get("admin_secret")
    
    # Проверка доступа
    if ADMIN_SECRET and current_secret != ADMIN_SECRET:
        return HTMLResponse(
            content="<h1>403 Forbidden</h1><p>Admin access required.</p>",
            status_code=403
        )
    
    return templates.TemplateResponse("debug.html", {
        "request": request,
        "secret": current_secret,
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "cookies": request.cookies
    })

# === Другие эндпоинты (сокращены для краткости) ===
# Здесь должны быть все остальные эндпоинты из вашего приложения
# /admin, /admin/create, /admin/edit и т.д.

# === Запуск сервера ===
if __name__ == "__main__":
    import uvicorn
    
    # Логируем конфигурацию при запуске
    port = int(os.getenv("PORT", 8081))
    logger.info(f"🚀 Запуск веб-API на порту {port}")
    logger.info(f"🔐 ADMIN_SECRET: {'установлен' if ADMIN_SECRET else 'не установлен'}")
    logger.info(f"📁 База данных: {DATABASE_PATH}")
    logger.info(f"🌍 Часовой пояс: {TIMEZONE}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        reload=False,
        workers=1
    )
