#!/usr/bin/env python3
"""Prosty bot Telegram do generowania CV (dwie fazy: analiza -> decyzja -> generacja).

Zmienne w .env:
    TELEGRAM_TOKEN             token bota z @BotFather (wymagany)
    TELEGRAM_ALLOWED_USER_ID   Twoje ID w Telegramie (wymagane); bot odpowiada tylko tej osobie
    CV_DEFAULT_JEZYK           pl albo en (domyslnie pl)
    CV_PYTHON                  interpreter Pythona do uruchamiania skryptu (domyslnie ten sam co bot)
"""

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Adresy zadan do Telegrama zawieraja token bota. Na poziomie INFO biblioteki HTTP wpisywalyby go do logow.
for _nazwa in ("httpx", "httpcore"):
    logging.getLogger(_nazwa).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0") or 0)
JEZYK = os.getenv("CV_DEFAULT_JEZYK", "pl").strip()
PYTHON = os.getenv("CV_PYTHON") or sys.executable

ROOT = Path(__file__).parent.resolve()
SCRIPT = ROOT / "generuj_json_z_oferty.py"
FOLDER_BAZ = ROOT / "bazy"
FOLDER_LOGOW = ROOT / "logi"

OSOBA, FIRMA, STANOWISKO, OFERTA, DECYZJA = range(5)


def slug(tekst: str) -> str:
    return re.sub(r"[^\w-]+", "-", tekst.lower()).strip("-")[:30] or "x"


def get_osoby() -> list[str]:
    """Lista osob z plikow '<Imie> - baza zatrudnienia.md' w folderze bazy/."""
    if not FOLDER_BAZ.exists():
        return []
    wynik = []
    for p in sorted(FOLDER_BAZ.glob("* - baza zatrudnienia.md")):
        wynik.append(p.stem.replace(" - baza zatrudnienia", "").strip())
    return wynik


def folder_logu(osoba: str, firma: str, stanowisko: str) -> Path:
    return FOLDER_LOGOW / slug(osoba)[:16] / f"{slug(firma)}_{slug(stanowisko)}"


def is_authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID


async def send_safe(update: Update, text: str):
    if not text:
        return
    try:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])
    except Exception as e:
        log.warning(f"send_safe: {e}")


async def stream_subprocess(update: Update, args: list, timeout: int = 1800):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,        # skrypt nigdy nie czeka na klawiature
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(ROOT),
        env=env,
    )

    all_lines, pending = [], []
    last_send = time.monotonic()
    start = time.monotonic()

    async for line in proc.stdout:
        if time.monotonic() - start > timeout:
            proc.kill()
            await send_safe(update, f"⏱️ Timeout {timeout}s.")
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        all_lines.append(text)
        pending.append(text)
        if time.monotonic() - last_send > 15 and pending:
            await send_safe(update, "\n".join(pending[-40:]))
            pending.clear()
            last_send = time.monotonic()

    await proc.wait()
    if pending:
        await send_safe(update, "\n".join(pending[-40:]))
    return proc.returncode, "\n".join(all_lines)


def find_pdf_path(output: str):
    for line in output.splitlines():
        if line.startswith("✅ PDF:"):
            return line.replace("✅ PDF:", "").strip()
    return None


def cleanup(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()


# ---------- Handlers ----------

async def cmd_start(update, context):
    if not is_authorized(update):
        await update.message.reply_text("Brak dostępu.")
        return
    osoby = get_osoby()
    info = f"Dostępne osoby: {', '.join(osoby) or '(brak w bazy/)'}\n"
    await update.message.reply_text(
        "Cześć! Komendy:\n/cv - zacznij generowanie CV\n/anuluj - przerwij\n\n" + info
    )


async def cmd_help(update, context):
    if not is_authorized(update):
        return
    osoby = get_osoby()
    await update.message.reply_text(
        "/cv - zacznij generowanie CV\n"
        "/anuluj - przerwij\n"
        f"Język: {JEZYK}\n"
        f"Osoby (z bazy/): {', '.join(osoby) or '(brak)'}"
    )


async def cmd_cv(update, context):
    if not is_authorized(update):
        return ConversationHandler.END
    context.user_data.clear()

    osoby = get_osoby()
    if not osoby:
        await update.message.reply_text(
            f"❌ Nie znalazłem baz w folderze {FOLDER_BAZ}. "
            "Dodaj plik '<Imię> - baza zatrudnienia.md'."
        )
        return ConversationHandler.END

    if len(osoby) == 1:
        context.user_data["osoba"] = osoby[0]
        await update.message.reply_text(
            f"Osoba: {osoby[0]}.\n\nPodaj nazwę firmy:"
        )
        return FIRMA

    # kilka osob -> inline keyboard
    buttons = [[InlineKeyboardButton(o, callback_data=f"os:{o}")] for o in osoby]
    await update.message.reply_text(
        "Dla kogo generujemy CV?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return OSOBA


async def wybierz_osobe(update, context):
    query = update.callback_query
    await query.answer()
    if not is_authorized(update) or not query.data.startswith("os:"):
        return OSOBA
    osoba = query.data[3:]
    if osoba not in get_osoby():
        await query.edit_message_text("Nie ma takiej bazy.")
        return ConversationHandler.END
    context.user_data["osoba"] = osoba
    await query.edit_message_text(f"Osoba: {osoba}.\n\nPodaj nazwę firmy:")
    return FIRMA


async def get_firma(update, context):
    firma = (update.message.text or "").strip()
    if len(firma) < 2:
        await update.message.reply_text("Podaj poprawną nazwę firmy:")
        return FIRMA
    context.user_data["firma"] = firma
    await update.message.reply_text("Podaj stanowisko:")
    return STANOWISKO


async def get_stanowisko(update, context):
    stan = (update.message.text or "").strip()
    if len(stan) < 2:
        await update.message.reply_text("Podaj poprawne stanowisko:")
        return STANOWISKO
    context.user_data["stanowisko"] = stan
    await update.message.reply_text("Wyślij treść oferty (tekst lub plik .txt).")
    return OFERTA


async def get_oferta(update, context):
    if update.message.document:
        doc = update.message.document
        if not (doc.file_name or "").lower().endswith(".txt"):
            await update.message.reply_text("Wyślij plik .txt albo wklej tekst.")
            return OFERTA
        f = await doc.get_file()
        data = await f.download_as_bytearray()
        try:
            tekst = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            tekst = data.decode("cp1250", errors="replace")
    elif update.message.text:
        tekst = update.message.text
    else:
        await update.message.reply_text("Nie widzę treści. Wyślij tekst lub plik .txt.")
        return OFERTA

    if len(tekst.strip()) < 200:
        await update.message.reply_text(
            f"Oferta ma tylko {len(tekst.strip())} znaków (minimum 200). Wyślij ponownie."
        )
        return OFERTA

    # sciezki wynikowe zdeterminowane (nie tymczasowe)
    flog = folder_logu(context.user_data["osoba"],
                       context.user_data["firma"],
                       context.user_data["stanowisko"])
    flog.mkdir(parents=True, exist_ok=True)
    oferta_path = flog / "oferta_input.txt"
    oferta_path.write_text(tekst, encoding="utf-8")
    context.user_data["folder_logu"] = str(flog)
    context.user_data["oferta_path"] = str(oferta_path)

    await update.message.reply_text("⏳ Krok 1/2: analizuję ofertę...")

    args = [
        PYTHON, str(SCRIPT),
        "--firma", context.user_data["firma"],
        "--stanowisko", context.user_data["stanowisko"],
        "--oferta", str(oferta_path),
        "--jezyk", JEZYK,
        "--osoba", context.user_data["osoba"],
        "--folder-log", str(flog),
        "--zapisz-analize", str(flog / "wymagania_z_oferty.json"),
    ]

    try:
        rc, output = await stream_subprocess(update, args, timeout=1800)
    except Exception as e:
        log.exception("phase1")
        await update.message.reply_text(f"❌ Błąd uruchomienia: {e}")
        cleanup(context)
        return ConversationHandler.END

    if rc != 0 or not (flog / "wymagania_z_oferty.json").exists():
        await update.message.reply_text(
            f"❌ Analiza nie powiodła się (kod {rc})."
        )
        cleanup(context)
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ Analiza gotowa.\n"
        "Napisz [generuj] żeby wygenerować CV, albo [przerwij] żeby anulować.\n"
        f"📁 Folder: {flog.name}"
    )
    return DECYZJA


async def get_decyzja(update, context):
    text = (update.message.text or "").strip().lower()

    if text in ("przerwij", "p", "anuluj", "nie"):
        await update.message.reply_text("Anulowano. Pliki w folderze logu zostały.")
        cleanup(context)
        return ConversationHandler.END

    if text not in ("generuj", "g", "tak", "ok"):
        await update.message.reply_text("Napisz [generuj] lub [przerwij].")
        return DECYZJA

    await update.message.reply_text("⏳ Krok 2/2: generuję CV...")

    flog = Path(context.user_data["folder_logu"])
    args = [
        PYTHON, str(SCRIPT),
        "--firma", context.user_data["firma"],
        "--stanowisko", context.user_data["stanowisko"],
        "--oferta", context.user_data["oferta_path"],
        "--jezyk", JEZYK,
        "--osoba", context.user_data["osoba"],
        "--folder-log", str(flog),
        "--wczytaj-analize", str(flog / "wymagania_z_oferty.json"),
        "--bez-pytan",
        "--pdf",
    ]

    try:
        rc, output = await stream_subprocess(update, args, timeout=1800)
    except Exception as e:
        log.exception("phase2")
        await update.message.reply_text(f"❌ Błąd: {e}")
        cleanup(context)
        return ConversationHandler.END

    if rc != 0:
        await update.message.reply_text(f"❌ Generowanie zakończone kodem {rc}.")
        cleanup(context)
        return ConversationHandler.END

    pdf_path = find_pdf_path(output)
    if pdf_path and Path(pdf_path).exists():
        try:
            with open(pdf_path, "rb") as f:
                await update.message.reply_document(
                    document=f, filename=Path(pdf_path).name,
                    caption=f"✅ CV gotowe\n📁 {flog.name}",
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ PDF jest, ale błąd wysyłki: {e}")
    else:
        await update.message.reply_text(
            "⚠️ Proces zakończony, ale nie znalazłem PDF. Sprawdź logi powyżej."
        )

    cleanup(context)
    return ConversationHandler.END


async def cmd_anuluj(update, context):
    if not is_authorized(update):
        return ConversationHandler.END
    await update.message.reply_text("Anulowano.")
    cleanup(context)
    return ConversationHandler.END


def main():
    if not TOKEN:
        raise SystemExit("Brak TELEGRAM_TOKEN w .env")
    if not ALLOWED_USER_ID:
        raise SystemExit("Brak TELEGRAM_ALLOWED_USER_ID w .env")
    if not SCRIPT.exists():
        raise SystemExit(f"Nie znaleziono {SCRIPT}")

    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("cv", cmd_cv)],
        states={
            OSOBA: [CallbackQueryHandler(wybierz_osobe, pattern=r"^os:")],
            FIRMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_firma)],
            STANOWISKO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_stanowisko)],
            OFERTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_oferta),
                MessageHandler(filters.Document.ALL, get_oferta),
            ],
            DECYZJA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_decyzja)],
        },
        fallbacks=[CommandHandler("anuluj", cmd_anuluj)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(conv)

    log.info("Bot uruchomiony.")
    app.run_polling()


if __name__ == "__main__":
    main()
