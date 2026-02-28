import os
import asyncio
import sqlite3
from datetime import date

import fitz  # PyMuPDF
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from docx import Document

load_dotenv()

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it in environment variables.")

# Comma-separated list of admin Telegram IDs. Example: "5542225054,123456"
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

BMC_LINK = os.getenv("BMC_LINK", "https://buymeacoffee.com/pdftotext").strip()

# Optional: Telegram channel link
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/ConvertPDFtotext").strip()

# Optional: Max upload size (bytes). Telegram bots can receive bigger files, but keep it safe on server.
# Default: 20 MB
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(20 * 1024 * 1024)))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========= PLANS (match your BMC tiers) =========
# FREE: 10 pages/day
# BASIC ($3): 30 pages/day
# PRO   ($5): 200 pages/day + DOCX
# PREMIUM ($10): 1000 pages/day + DOCX
PLANS = {
    "FREE": 10,
    "BASIC": 30,
    "PRO": 200,
    "PREMIUM": 1000,
}

PAID_PLANS = {"BASIC", "PRO", "PREMIUM"}
DOCX_PLANS = {"PRO", "PREMIUM"}

DB_PATH = "data.db"


# ========= DB =========
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'FREE',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                pages_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
            """
        )


def ensure_user(user_id: int):
    today = date.today().isoformat()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, plan, created_at) VALUES(?,?,?)",
            (user_id, "FREE", today),
        )


def get_user_plan(user_id: int) -> str:
    ensure_user(user_id)
    with db() as conn:
        row = conn.execute("SELECT plan FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else "FREE"


def set_user_plan(user_id: int, plan: str):
    ensure_user(user_id)
    with db() as conn:
        conn.execute("UPDATE users SET plan=? WHERE user_id=?", (plan, user_id))


def get_usage_today(user_id: int) -> int:
    ensure_user(user_id)
    today = date.today().isoformat()
    with db() as conn:
        row = conn.execute(
            "SELECT pages_used FROM usage WHERE user_id=? AND day=?",
            (user_id, today),
        ).fetchone()
    return int(row[0]) if row else 0


def add_usage_today(user_id: int, pages: int):
    ensure_user(user_id)
    today = date.today().isoformat()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO usage(user_id, day, pages_used)
            VALUES(?,?,?)
            ON CONFLICT(user_id, day) DO UPDATE SET pages_used = pages_used + excluded.pages_used
            """,
            (user_id, today, pages),
        )


# ========= TEXT HELPERS =========
def upgrade_text() -> str:
    return (
        "💎 Upgrade to unlock higher limits & features:\n\n"
        "• BASIC ($3/month): 30 pages/day\n"
        "• PRO ($5/month): 200 pages/day + TXT + DOCX\n"
        "• PREMIUM ($10/month): 1000 pages/day + TXT + DOCX\n\n"
        f"👉 Support here: {BMC_LINK}\n\n"
        "After payment, send your Telegram ID with /id to the admin (or wait for auto-activation when enabled)."
    )


def plan_text(user_id: int) -> str:
    p = get_user_plan(user_id)
    used = get_usage_today(user_id)
    limit = PLANS.get(p, PLANS["FREE"])
    return (
        f"📊 Your Plan: {p}\n"
        f"Daily Limit: {limit} pages\n"
        f"Used Today: {used} pages\n\n"
        f"Use /upgrade to unlock higher limits."
    )


def help_text() -> str:
    return (
        "📄 PDF to Text Bot — Commands\n\n"
        "• /start — Introduction\n"
        "• /plan — Show your current plan and daily usage\n"
        "• /upgrade — Membership plans and payment link\n"
        "• /id — Show your Telegram ID\n"
        "• /help — Show this help\n\n"
        "Send a PDF file to convert it to text.\n"
        "PRO/PREMIUM users also receive a DOCX file."
    )


# ========= COMMANDS =========
@dp.message(Command("start"))
async def start(message: Message):
    ensure_user(message.from_user.id)
    await message.answer(
        "📄 PDF to Text Bot\n\n"
        "Send me a PDF file and I will convert it to text.\n"
        "Use /plan to see your current plan.\n"
        "Use /upgrade to unlock higher limits.\n\n"
        f"📢 Channel: {CHANNEL_LINK}"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(help_text())


@dp.message(Command("id"))
async def id_cmd(message: Message):
    await message.answer(
        f"🆔 Your Telegram ID: {message.from_user.id}\n\n"
        "Copy this ID and send it to the admin after payment for manual activation."
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    await message.answer(plan_text(message.from_user.id))


@dp.message(Command("upgrade"))
async def upgrade_cmd(message: Message):
    await message.answer(upgrade_text())


@dp.message(Command("setplan"))
async def setplan_cmd(message: Message):
    # Admin only
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("You are not authorized to use this command.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("Usage: /setplan TELEGRAM_ID FREE|BASIC|PRO|PREMIUM")

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("TELEGRAM_ID must be a number.")

    plan_name = parts[2].upper().strip()
    if plan_name not in PLANS:
        return await message.answer("Invalid plan. Use: FREE | BASIC | PRO | PREMIUM")

    set_user_plan(target_id, plan_name)
    await message.answer(f"✅ Plan {plan_name} has been set for user {target_id}.")


# ========= PDF HANDLER =========
@dp.message(F.document)
async def handle_document(message: Message):
    user_id = message.from_user.id
    ensure_user(user_id)

    doc = message.document
    filename = (doc.file_name or "").lower()

    if not filename.endswith(".pdf"):
        return await message.answer("Please send a PDF file.")

    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        mb = MAX_FILE_BYTES / (1024 * 1024)
        return await message.answer(
            f"⚠️ File too large. Max allowed size is {mb:.0f} MB.\n\n" + upgrade_text()
        )

    current_plan = get_user_plan(user_id)
    daily_limit = PLANS.get(current_plan, PLANS["FREE"])
    used = get_usage_today(user_id)

    # Download file from Telegram
    file_info = await bot.get_file(doc.file_id)
    file_stream = await bot.download_file(file_info.file_path)
    pdf_bytes = file_stream.read()

    # Read PDF + pages
    try:
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return await message.answer("⚠️ Failed to read this PDF. Please try another file.")

    pages = len(pdf)

    # Quota check
    if used + pages > daily_limit:
        pdf.close()
        return await message.answer(
            f"⚠️ Daily limit exceeded.\n"
            f"Used: {used}/{daily_limit} pages\n"
            f"This file has {pages} pages.\n\n"
            + upgrade_text()
        )

    # Extract text
    text_parts = []
    for page in pdf:
        text_parts.append(page.get_text())
    pdf.close()

    text = "\n".join(text_parts).strip()
    if not text:
        return await message.answer(
            "No text found in this PDF.\n"
            "If your PDF is scanned images, OCR is not enabled yet on this bot."
        )

    # Save usage
    add_usage_today(user_id, pages)

    # Send TXT
    await message.answer_document(
        BufferedInputFile(text.encode("utf-8"), filename="converted.txt"),
        caption=f"✅ Converted successfully. Pages used today: {get_usage_today(user_id)}/{daily_limit}"
    )

    # Send DOCX for PRO/PREMIUM
    if current_plan in DOCX_PLANS:
        d = Document()
        d.add_paragraph(text)

        out_path = "converted.docx"
        d.save(out_path)
        with open(out_path, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="converted.docx"),
                caption="✅ DOCX is available for PRO/PREMIUM."
            )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
