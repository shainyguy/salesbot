from __future__ import annotations
import logging
import uuid
from datetime import datetime, timedelta

import httpx

from config import config

logger = logging.getLogger(__name__)

_token_cache: dict = {"access_token": "", "expires_at": 0}

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"


async def _get_token() -> str:
    now = datetime.utcnow().timestamp() * 1000
    if _token_cache["access_token"] and _token_cache["expires_at"] > now:
        return _token_cache["access_token"]

    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                AUTH_URL,
                headers={
                    "Authorization": f"Basic {config.GIGACHAT_API_KEY}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": "GIGACHAT_API_PERS"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            _token_cache["access_token"] = data["access_token"]
            _token_cache["expires_at"] = data["expires_at"]
            return data["access_token"]
    except Exception as e:
        logger.error(f"GigaChat auth error: {e}")
        return ""


async def ai_chat(prompt: str, system: str | None = None) -> str:
    """Отправляет запрос в GigaChat и возвращает ответ."""
    token = await _get_token()
    if not token:
        return "⚠️ AI-сервис временно недоступен."

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "GigaChat",
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 1024,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"GigaChat error: {e}")
        return "⚠️ Не удалось получить ответ от AI."


async def generate_diagnostics_report(quiz_data: dict) -> str:
    system = (
        "Ты — эксперт по продажам и автоматизации бизнеса. "
        "Проанализируй ответы пользователя из квиз-опроса и дай конкретные рекомендации "
        "по увеличению конверсии, снижению потерь заявок и автоматизации. "
        "Отвечай на русском языке. Будь конкретен, используй цифры."
    )
    prompt = f"Результаты опроса клиента:\n{_format_quiz(quiz_data)}\n\nДай подробный анализ и рекомендации."
    return await ai_chat(prompt, system)


async def generate_followup_message(lead_info: dict, step: int) -> str:
    system = (
        "Ты — менеджер по продажам. Напиши короткое дожимающее сообщение клиенту. "
        "Будь дружелюбным, но настойчивым. Используй приёмы: социальное доказательство, "
        "ограничение по времени, выгода. Отвечай на русском."
    )
    prompts = {
        1: f"Напиши первое касание после консультации. Информация о клиенте: {lead_info}",
        2: f"Напиши второе касание с кейсом/отзывом. Клиент: {lead_info}",
        3: f"Напиши третье касание с спецпредложением. Клиент: {lead_info}",
        4: f"Напиши финальное касание с дедлайном (24 часа). Клиент: {lead_info}",
    }
    prompt = prompts.get(step, prompts[1])
    return await ai_chat(prompt, system)


async def ai_business_consultant(question: str) -> str:
    system = (
        "Ты — AI-консультант по бизнесу и продажам. "
        "Помогаешь предпринимателям увеличить продажи, автоматизировать процессы, "
        "выстроить воронки. Отвечай конкретно и по делу. Русский язык."
    )
    return await ai_chat(question, system)


def _format_quiz(data: dict) -> str:
    labels = {
        "niche": "Ниша",
        "leads_count": "Заявок/мес",
        "avg_check": "Средний чек",
        "conversion": "Конверсия",
        "response_time": "Время ответа",
        "has_crm": "CRM",
        "has_repeat_sales": "Повторные продажи",
        "leak_control": "Контроль утечек",
    }
    lines = []
    for key, val in data.items():
        label = labels.get(key, key)
        lines.append(f"• {label}: {val}")
    return "\n".join(lines)