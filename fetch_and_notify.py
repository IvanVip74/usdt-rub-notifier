#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT/RUB Telegram Notifier
============================
 
Отдельный, самодостаточный скрипт (без внешних зависимостей — только
стандартная библиотека Python) для запуска по расписанию на GitHub
Actions. Проверяет курс USDT/RUB на бирже Rapira и шлёт сообщение в
Telegram, если курс изменился на 0.10 ₽ (10 копеек) и больше по
сравнению с последним значением, о котором уже было уведомление.
 
Состояние (последний уведомлённый курс) хранится в state.json рядом со
скриптом и коммитится обратно в репозиторий после каждого запуска —
так между запусками (каждый из которых на GitHub Actions стартует "с
нуля") память не теряется.
 
Нужны переменные окружения (задаются как GitHub Actions Secrets):
    TELEGRAM_BOT_TOKEN — токен бота от @BotFather
    TELEGRAM_CHAT_ID   — id канала/группы/чата, куда слать сообщения
"""
 
from __future__ import annotations
 
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
 
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "state.json"
HISTORY_FILE = SCRIPT_DIR / "history.log"
 
THRESHOLD_RUB = 0.10
 
RAPIRA_URL = "https://api.rapira.net/open/market/rates"
USER_AGENT = "Mozilla/5.0 (compatible; USDTRUBTelegramBot/1.0)"
 
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
 
 
def fetch_rapira_price(timeout: int = 10) -> float:
    """Курс USDT/RUB с биржи Rapira: середина между bid и ask."""
    req = urllib.request.Request(
        RAPIRA_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
 
    items = data.get("data") if isinstance(data, dict) else data
    items = items or []
    entry = next((it for it in items if it.get("symbol") == "USDT/RUB"), None)
    if not entry:
        raise RuntimeError("Rapira: пара USDT/RUB не найдена в ответе")
 
    bid, ask = entry.get("bidPrice"), entry.get("askPrice")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2
    if entry.get("close") is not None:
        return float(entry["close"])
    raise RuntimeError("Rapira: не удалось определить цену из ответа")
 
 
def send_telegram(text: str) -> bool:
    """Отправляет сообщение в Telegram. Возвращает True, только если
    Telegram API реально подтвердил отправку (ok: true в ответе)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — сообщение не отправлено", file=sys.stderr)
        return False
 
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Telegram вернул код ошибки (например, 400/401/403) — читаем тело
        # ответа, там обычно есть понятное описание причины.
        try:
            details = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            details = "(не удалось прочитать тело ответа)"
        print(f"Ошибка отправки в Telegram: HTTP {exc.code}: {details}", file=sys.stderr)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка отправки в Telegram: {exc}", file=sys.stderr)
        return False
 
    if not body.get("ok"):
        print(f"Telegram API отклонил сообщение: {body}", file=sys.stderr)
        return False
 
    print("Сообщение в Telegram отправлено успешно.")
    return True
 
 
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}
 
 
def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
 
 
def append_history(price: float, diff: float) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    arrow = "▲" if diff > 0 else "▼"
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts} UTC\t{price:.4f}\t{arrow}{abs(diff):.4f}\n")
 
 
def main() -> None:
    state = load_state()
    last_notified = state.get("last_notified_price")
 
    try:
        price = fetch_rapira_price()
    except Exception as exc:  # noqa: BLE001
        # Не роняем workflow из-за временной ошибки сети/API — просто
        # пропускаем этот запуск, следующий будет через несколько минут.
        print(f"Ошибка получения курса: {exc}", file=sys.stderr)
        return
 
    print(f"USDT/RUB: {price:.4f}")
 
    if last_notified is None:
        sent = send_telegram(
            f"🟢 USDT/RUB — мониторинг запущен\n"
            f"Текущий курс: {price:.2f} ₽\n"
            f"Буду сообщать при изменении на {THRESHOLD_RUB:.2f} ₽ и больше."
        )
        # Состояние сохраняем, ТОЛЬКО если сообщение реально ушло —
        # иначе при сбое отправки бот "забудет" отправить его снова и
        # будет молча ждать следующего изменения курса.
        if sent:
            state["last_notified_price"] = price
            state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            save_state(state)
        else:
            print("Первое уведомление не отправлено — попробуем снова в следующий запуск.", file=sys.stderr)
        return
 
    diff = price - last_notified
    if abs(diff) >= THRESHOLD_RUB:
        direction = "выросла ▲" if diff > 0 else "упала ▼"
        sent = send_telegram(
            f"USDT/RUB {direction} на {abs(diff):.2f} ₽\n"
            f"Текущий курс: {price:.2f} ₽ (было {last_notified:.2f} ₽)"
        )
        if sent:
            append_history(price, diff)
            state["last_notified_price"] = price
            state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            save_state(state)
        else:
            print("Уведомление об изменении курса не отправлено — попробуем снова в следующий запуск.", file=sys.stderr)
 
 
if __name__ == "__main__":
    main()
