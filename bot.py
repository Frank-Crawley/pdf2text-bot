import os
import asyncio
import sqlite3
from datetime import date

import fitz
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from docx import Document

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
BMC_LINK = os.getenv("BMC_LINK", "https://buymeacoffee.com/pdftotext")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== PLAN CONFIG =====
PLANS = {
    "FREE": 10,
    "BASIC": 30,
    "PRO": 200,
    "PREMIUM": 1000,
}

DB_PATH = "data.db"


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


def upgrade_text() -> str:
    return (
        "💎 Upgrade to unlock higher limits & features:\n\n"
        f"• BASIC ($3): 30 pages/day\n"
        f"• PRO ($5): 200 pages/day + DOCX\n"
        f"• PREMIUM ($10): 1000 pages/day + DOCX\n\n"
        f"👉 Support here: {BMC_LINK}\n"
        "After payment, send your Telegram ID to admin (or wait for auto-activation when enabled)."
    )


# ===== COMMANDS =====
@dp.message(Command("start"))
async def start(message: Message):
    ensure_user(message.from_user.id)
    await message.answer(
        "📄 PDF to Text Bot\n\n"
        "Send me a PDF file and I will convert it.\n"
        "Use /plan to see your current plan.\n"
        "Use /upgrade to support and unlock higher limits."
    )


@dp.message(Command("plan"))
async def plan(message: Message):
    user_id = message.from_user.id
    current_plan = get_user_plan(user_id)
    used = get_usage_today(user_id)
    limit = PLANS.get(current_plan, PLANS["FREE"])

    await message.answer(
        f"📊 Your Plan: {current_plan}\n"
        f"Daily Limit: {limit} pages\n"
        f"Used Today: {used} pages"
    )


@dp.message(Command("upgrade"))
async def upgrade(message: Message):
    await message.answer(upgrade_text())


@dp.message(Command("setplan"))
async def cmd_setplan(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Bạn không có quyền dùng lệnh này.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("Dùng: /setplan TELEGRAM_ID FREE|BASIC|PRO|PREMIUM")

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("TELEGRAM_ID phải là số.")

    plan_name = parts[2].upper()
    if plan_name not in PLANS:
        return await message.answer("Plan không hợp lệ. FREE|BASIC|PRO|PREMIUM")

    set_user_plan(target_id, plan_name)
    await message.answer(f"✅ Đã set plan {plan_name} cho user {target_id}")


# ===== PDF HANDLER =====
@dp.message(F.document)
async def handle_pdf(message: Message):
    user_id = message.from_user.id
    ensure_user(user_id)

    document = message.document
    if not document.file_name.lower().endswith(".pdf"):
        return await message.answer("Please send a PDF file.")

    current_plan = get_user_plan(user_id)
    limit = PLANS.get(current_plan, PLANS["FREE"])
    used = get_usage_today(user_id)

    file = await bot.get_file(document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    pdf_data = file_bytes.read()

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    pages = len(doc)

    if used + pages > limit:
        doc.close()
        return await message.answer(
            f"⚠️ Daily limit exceeded.\nUsed: {used}/{limit} pages\n\n" + upgrade_text()
        )

    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()

    if not text.strip():
        return await message.answer("No text found in this PDF.")

    add_usage_today(user_id, pages)

    # TXT
    await message.answer_document(
        BufferedInputFile(text.encode("utf-8"), filename="converted.txt")
    )

    # DOCX for PRO/PREMIUM
    if current_plan in ["PRO", "PREMIUM"]:
        d = Document()
        d.add_paragraph(text)
        out_path = "converted.docx"
        d.save(out_path)
        with open(out_path, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="converted.docx")
            )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
