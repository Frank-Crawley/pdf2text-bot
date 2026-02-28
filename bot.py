import os
import io
import re
import asyncio
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command

import fitz  # PyMuPDF
from docx import Document


# =========================
# ENV
# =========================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it in environment variables.")

# Comma-separated list of admin Telegram IDs. Example: "5542225054,123456"
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

BMC_LINK = os.getenv("BMC_LINK", "https://buymeacoffee.com/pdftotext").strip()
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/ConvertPDFtotext").strip()

DB_PATH = os.getenv("DB_PATH", "data.db").strip()

# =========================
# PLANS (match your BMC tiers)
# FREE: 10 pages/day
# BASIC ($3): 50 pages/day
# STANDARD ($5): 120 pages/day
# PREMIUM ($10): 300 pages/day + DOCX
# =========================
PLANS = {
    "FREE": 10,
    "BASIC": 50,
    "STANDARD": 120,
    "PREMIUM": 300,
}

PAID_PLANS = {"BASIC", "STANDARD", "PREMIUM"}
DOCX_PLANS = {"PREMIUM"}


# =========================
# DB Helpers
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'FREE'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                pages_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, day)
            )
            """
        )


def today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_user(user_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, plan) VALUES (?, 'FREE')",
            (user_id,),
        )


def get_user_plan(user_id: int) -> str:
    ensure_user(user_id)
    with db() as conn:
        row = conn.execute("SELECT plan FROM users WHERE user_id=?", (user_id,)).fetchone()
    return (row[0] if row else "FREE").upper()


def set_user_plan(user_id: int, plan: str) -> None:
    plan = plan.upper().strip()
    if plan not in PLANS:
        raise ValueError("Invalid plan")
    ensure_user(user_id)
    with db() as conn:
        conn.execute("UPDATE users SET plan=? WHERE user_id=?", (plan, user_id))


def get_usage_today(user_id: int) -> int:
    ensure_user(user_id)
    day = today_utc_str()
    with db() as conn:
        row = conn.execute(
            "SELECT pages_used FROM usage WHERE user_id=? AND day=?",
            (user_id, day),
        ).fetchone()
    return int(row[0]) if row else 0


def add_usage_today(user_id: int, pages: int) -> None:
    ensure_user(user_id)
    day = today_utc_str()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO usage(user_id, day, pages_used)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET pages_used = pages_used + excluded.pages_used
            """,
            (user_id, day, int(pages)),
        )


# =========================
# Text Builders
# =========================
def help_text() -> str:
    return (
        "📄 PDF to Text Bot\n\n"
        "Commands:\n"
        "/start - Show welcome message\n"
        "/help - Show this help\n"
        "/id - Show your Telegram ID (for manual activation)\n"
        "/plan - Show your current plan and daily usage\n"
        "/upgrade - Show upgrade options\n\n"
        "Just send a PDF file and I will convert it to text.\n"
    )


def upgrade_text() -> str:
    lines = [
        "💎 Upgrade to unlock higher limits & features:",
        "",
        "• BASIC ($3/month): 50 pages/day",
        "• STANDARD ($5/month): 120 pages/day",
        "• PREMIUM ($10/month): 300 pages/day + DOCX",
        "",
        f"👉 Support here: {BMC_LINK}",
        f"📣 Telegram channel: {CHANNEL_LINK}",
        "",
        "After payment, send your Telegram ID using /id to the admin for manual activation.",
    ]
    return "\n".join(lines)


def plan_text(user_id: int) -> str:
    plan = get_user_plan(user_id)
    used = get_usage_today(user_id)
    limit = PLANS.get(plan, PLANS["FREE"])
    extra = ""
    if plan in DOCX_PLANS:
        extra = "\nDOCX export: ✅ Enabled"
    else:
        extra = "\nDOCX export: ❌ Premium only"
    return (
        f"📊 Your Plan: {plan}\n"
        f"Daily Limit: {limit} pages\n"
        f"Used Today: {used} pages\n"
        f"Use /upgrade to unlock higher limits.\n"
        f"{extra}"
    )


# =========================
# PDF Conversion
# =========================
def extract_text_from_pdf(pdf_bytes: bytes) -> tuple[str, int]:
    """
    Returns: (text, page_count)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = doc.page_count
    parts = []
    for i in range(page_count):
        page = doc.load_page(i)
        parts.append(page.get_text("text"))
    doc.close()
    text = "\n".join(parts).strip()
    return text, page_count


def build_docx_bytes(text: str) -> bytes:
    d = Document()
    # Split into paragraphs
    for para in re.split(r"\n\s*\n", text.strip() or ""):
        d.add_paragraph(para.strip())
    bio = io.BytesIO()
    d.save(bio)
    return bio.getvalue()


# =========================
# Bot Setup
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "Send me a PDF file and I will convert it to text.\n"
        "Use /plan to see your current plan.\n"
        "Use /upgrade to see upgrade options."
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(help_text())


@dp.message(Command("upgrade"))
async def upgrade_cmd(message: Message):
    await message.answer(upgrade_text())


@dp.message(Command("id"))
async def id_cmd(message: Message):
    user_id = message.from_user.id
    await message.answer(
        f"🆔 Your Telegram ID: {user_id}\n\n"
        "Copy this ID and send it to the admin after payment for manual activation."
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    user_id = message.from_user.id
    await message.answer(plan_text(user_id))


@dp.message(Command("setplan"))
async def setplan_cmd(message: Message):
    # Admin only
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("You are not authorized to use this command.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("Usage: /setplan TELEGRAM_ID FREE|BASIC|STANDARD|PREMIUM")

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("TELEGRAM_ID must be a number.")

    plan_name = parts[2].upper().strip()
    if plan_name not in PLANS:
        return await message.answer("Invalid plan. Use: FREE | BASIC | STANDARD | PREMIUM")

    set_user_plan(target_id, plan_name)
    await message.answer(f"✅ Plan {plan_name} has been set for user {target_id}.")


@dp.message(F.document)
async def handle_document(message: Message):
    user_id = message.from_user.id
    ensure_user(user_id)

    doc = message.document
    filename = (doc.file_name or "").lower()

    if not filename.endswith(".pdf"):
        return await message.answer("Please send a PDF file (.pdf).")

    # Download file
    tg_file = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(tg_file.file_path)
    pdf_bytes = file_bytes.read()

    # Convert
    try:
        text, pages = extract_text_from_pdf(pdf_bytes)
    except Exception:
        return await message.answer("Failed to read this PDF. Please try another file.")

    # Check limit
    plan = get_user_plan(user_id)
    limit = PLANS.get(plan, PLANS["FREE"])
    used = get_usage_today(user_id)

    if used + pages > limit:
        remaining = max(0, limit - used)
        return await message.answer(
            f"⚠️ Daily limit reached.\n"
            f"Plan: {plan}\n"
            f"Remaining today: {remaining} pages\n\n"
            f"Use /upgrade to unlock higher limits."
        )

    # Count usage
    add_usage_today(user_id, pages)

    # Build TXT
    txt_bytes = (text or "").encode("utf-8", errors="replace")
    txt_file = BufferedInputFile(txt_bytes, filename="converted.txt")

    # Send TXT
    used_after = get_usage_today(user_id)
    await message.answer_document(
        txt_file,
        caption=(
            f"✅ Converted successfully.\n"
            f"Pages used today: {used_after}/{limit}"
        ),
    )

    # Optional DOCX for PREMIUM only
    if plan in DOCX_PLANS:
        try:
            docx_bytes = build_docx_bytes(text or "")
            docx_file = BufferedInputFile(docx_bytes, filename="converted.docx")
            await message.answer_document(docx_file, caption="📄 DOCX export (Premium).")
        except Exception:
            # Don't fail whole flow if DOCX fails
            await message.answer("DOCX export failed, but TXT was created successfully.")


# Fallback
@dp.message()
async def fallback(message: Message):
    await message.answer("Send a PDF file to convert it to text. Use /help for commands.")


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())