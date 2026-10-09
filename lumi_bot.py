# -*- coding: utf-8 -*-
"""
Луми — Telegram-бот помощник по учёбе (мультипользовательский).
Только стандартная библиотека Python — устанавливать ничего не нужно.

Запуск локально (Windows):
    set TELEGRAM_TOKEN=123456:ABC...
    py lumi_bot.py

Переменные окружения:
    TELEGRAM_TOKEN   — токен от @BotFather (обязательно)
    AI_PROVIDER      — groq | gemini | pollinations | custom (по умолчанию: groq если есть AI_KEY, иначе pollinations)
    AI_KEY           — API-ключ ИИ (для groq/gemini бесплатный)
    AI_MODEL         — модель для текста (необязательно)
    AI_VISION_MODEL  — модель для фото (необязательно)
    AI_URL           — свой OpenAI-совместимый URL (для provider=custom)
    PORT             — порт для health-сервера (на Render задаётся автоматически)
"""

import os
import json
import time
import base64
import threading
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

# ------------------------------------------------------------------
# НАСТРОЙКИ
# ------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
AI_KEY = os.environ.get("AI_KEY", "").strip()
AI_PROVIDER = os.environ.get("AI_PROVIDER", "").strip().lower()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()
AI_VISION_MODEL = os.environ.get("AI_VISION_MODEL", "").strip()
AI_URL = os.environ.get("AI_URL", "").strip()

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash",
        "vision": "gemini-2.0-flash",
    },
    "pollinations": {
        "url": "https://text.pollinations.ai/openai",
        "model": "openai",
        "vision": "openai",
    },
    "custom": {
        "url": AI_URL or "http://localhost:11434/v1/chat/completions",
        "model": "llama3.1",
        "vision": "",
    },
}

if not AI_PROVIDER:
    AI_PROVIDER = "groq" if AI_KEY else "pollinations"
if AI_PROVIDER not in PROVIDERS:
    AI_PROVIDER = "custom"
PRESET = PROVIDERS[AI_PROVIDER]
TEXT_MODEL = AI_MODEL or PRESET["model"]
VISION_MODEL = AI_VISION_MODEL or PRESET["vision"]
API_URL = AI_URL or PRESET["url"]

SYSTEM = (
    "Ты — Луми, умный и дружелюбный помощник по учёбе. Отвечай на языке пользователя "
    "(обычно по-русски), понятно и по делу, живым дружеским тоном. Если это пример или задача — "
    "реши её по шагам и дай готовый ответ. Если прислали фото — прочитай текст на нём и реши. "
    "Будь точным, не выдумывай факты. Форматируй ответ кратко."
)

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumi_users.json")
MAX_HISTORY = 12

# ------------------------------------------------------------------
# ХРАНЕНИЕ ИСТОРИИ (по пользователю)
# ------------------------------------------------------------------
_lock = threading.Lock()

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_history(hist):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
    except Exception as e:
        print("Не смог сохранить историю:", e)

HISTORY = load_history()

def get_user(chat_id):
    return HISTORY.setdefault(str(chat_id), [])

def reset_user(chat_id):
    HISTORY[str(chat_id)] = []
    save_history(HISTORY)

# ------------------------------------------------------------------
# TELEGRAM API
# ------------------------------------------------------------------
API = "https://api.telegram.org/bot%s/" % TELEGRAM_TOKEN

def tg(method, **params):
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        API + method, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def send_message(chat_id, text, reply_to=None):
    # Telegram лимит 4096 символов — рвём на части
    text = text or "…"
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        params = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if reply_to and i == 0:
            params["reply_to_message_id"] = reply_to
        try:
            tg("sendMessage", **params)
        except Exception as e:
            print("sendMessage error:", e)

def send_typing(chat_id):
    try:
        tg("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass

def download_photo(file_id):
    info = tg("getFile", file_id=file_id)
    path = info["result"]["file_path"]
    url = "https://api.telegram.org/file/bot%s/%s" % (TELEGRAM_TOKEN, path)
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()

# ------------------------------------------------------------------
# ИИ
# ------------------------------------------------------------------
def clean(text):
    marker = text.find("Support Pollinations")
    if marker != -1:
        text = text[:marker]
    return text.strip().strip("-").strip()

def ask_ai(messages, model):
    headers = {"Content-Type": "application/json"}
    if AI_KEY:
        headers["Authorization"] = "Bearer " + AI_KEY
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 1200,
    }).encode("utf-8")
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            txt = d["choices"][0]["message"]["content"]
            if txt:
                return clean(txt)
            last = "пустой ответ"
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (401, 403):
                break
        except Exception as e:
            last = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "ИИ недоступен")

def build_messages(chat_id, user_text, photo_bytes=None):
    hist = get_user(chat_id)
    messages = [{"role": "system", "content": SYSTEM}]
    for m in hist[-MAX_HISTORY:]:
        messages.append(m)
    if photo_bytes:
        b64 = base64.b64encode(photo_bytes).decode("ascii")
        data_url = "data:image/jpeg;base64," + b64
        content = [
            {"type": "text", "text": user_text or "Реши задание на фото и дай готовый ответ."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_text})
    return messages

def remember(chat_id, user_text, answer):
    hist = get_user(chat_id)
    hist.append({"role": "user", "content": user_text})
    hist.append({"role": "assistant", "content": answer})
    HISTORY[str(chat_id)] = hist[-2 * MAX_HISTORY:]
    save_history(HISTORY)

# ------------------------------------------------------------------
# ОБРАБОТКА СООБЩЕНИЙ
# ------------------------------------------------------------------
GREET = (
    "Привет! Я Луми — помощник по учёбе. ✨\n\n"
    "• Скинь задание текстом — решу\n"
    "• Скинь фото задания — прочитаю и решу\n"
    "• /reset — очистить историю\n"
    "• /help — помощь\n\n"
    "С чего начнём?"
)

HELP = (
    "Что я умею:\n"
    "• 🔢 считать примеры и решать задачи\n"
    "• 📝 сочинения и тексты\n"
    "• 🌍 переводы на английский\n"
    "• 🔬 физика, химия, биология, история\n\n"
    "Команды: /start, /reset, /help"
)

def handle_update(u):
    msg = u.get("message") or u.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text in ("/start", "/help"):
        send_message(chat_id, GREET if text == "/start" else HELP)
        return
    if text == "/reset":
        reset_user(chat_id)
        send_message(chat_id, "🧹 Готово, историю очистил. Спрашивай заново!")
        return

    photo = msg.get("photo")
    if not photo and not text:
        return

    send_typing(chat_id)
    try:
        if photo:
            if not VISION_MODEL:
                send_message(chat_id, "😕 Этот ИИ не умеет читать фото. Напиши задание текстом.")
                return
            biggest = photo[-1]
            photo_bytes = download_photo(biggest["file_id"])
            messages = build_messages(chat_id, text, photo_bytes)
            answer = ask_ai(messages, VISION_MODEL)
            remember(chat_id, "📷 Фото задания: " + (text or ""), answer)
        else:
            messages = build_messages(chat_id, text)
            answer = ask_ai(messages, TEXT_MODEL)
            remember(chat_id, text, answer)
        send_message(chat_id, answer)
    except Exception as e:
        send_message(chat_id, "⚠️ Не смог ответить: %s" % e)

# ------------------------------------------------------------------
# HEALTH-СЕРВЕР (нужен для облака, напр. Render)
# ------------------------------------------------------------------
def start_health_server():
    port = int(os.environ.get("PORT", "8080"))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Lumi bot is alive")

        def log_message(self, *a):
            pass

    try:
        srv = HTTPServer(("0.0.0.0", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print("Health-сервер слушает порт", port)
    except Exception as e:
        print("Health-сервер не запустился:", e)

# ------------------------------------------------------------------
# ГЛАВНЫЙ ЦИКЛ (long polling)
# ------------------------------------------------------------------
def main():
    if not TELEGRAM_TOKEN:
        print("ОШИБКА: не задан TELEGRAM_TOKEN. Получи токен у @BotFather и задай переменную окружения.")
        return
    print("Провайдер ИИ:", AI_PROVIDER, "| модель:", TEXT_MODEL, "| фото:", VISION_MODEL or "нет")
    start_health_server()

    # сбрасываем возможный вебхук, чтобы работал long polling
    try:
        tg("deleteWebhook", drop_pending_updates=False)
    except Exception:
        pass

    print("Луми запущен. Жду сообщения…")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = tg("getUpdates", **params)
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                try:
                    handle_update(u)
                except Exception as e:
                    print("Ошибка обработки:", e)
        except urllib.error.HTTPError as e:
            print("getUpdates HTTP", e.code)
            time.sleep(3)
        except Exception as e:
            print("getUpdates error:", e)
            time.sleep(3)

if __name__ == "__main__":
    main()
