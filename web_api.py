# web_api.py

import asyncio
import os
from fastapi import FastAPI, HTTPException, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import logging

from config import WEB_API_SECRET, BOT_TOKEN
from scheduler_logic import publish_message
from shared.database import get_all_active_messages

app = FastAPI(title="Telegram Scheduler API")
logger = logging.getLogger(__name__)


class PublishRequest(BaseModel):
    chat_id: int
    text: Optional[str] = None
    photo_file_id: Optional[str] = None
    document_file_id: Optional[str] = None
    caption: Optional[str] = None
    pin: bool = False
    notify: bool = True
    delete_after_days: Optional[int] = None


@app.get("/health", summary="Health check")
async def health_check():
    """Проверяет, что сервис работает и имеет доступ к БД."""
    try:
        tasks = get_all_active_messages()
        return JSONResponse({
            "status": "ok",
            "active_tasks": len(tasks),
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            {"status": "error", "detail": str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@app.post("/publish", summary="Publish message immediately")
async def web_publish(request: PublishRequest, x_secret: str = Header(...)):
    if WEB_API_SECRET and x_secret != WEB_API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    try:
        msg_id = await publish_message(
            chat_id=request.chat_id,
            text=request.text,
            photo_file_id=request.photo_file_id,
            document_file_id=request.document_file_id,
            caption=request.caption,
            pin=request.pin,
            notify=request.notify,
            delete_after_days=request.delete_after_days
        )
        if msg_id is None:
            raise HTTPException(status_code=500, detail="Failed to send message")
        logger.info(f"Web publish: chat={request.chat_id}, msg_id={msg_id}")
        return {"ok": True, "message_id": msg_id}
    except Exception as e:
        logger.exception("Web publish error")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    import datetime  # ← добавлено для health-check
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
