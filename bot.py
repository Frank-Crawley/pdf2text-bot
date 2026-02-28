import os
import asyncio
import fitz
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from docx import Document

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS").split(",")]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== PLAN CONFIG =====
PLANS = {
    "FREE": 10,
    "BASIC": 30,
    "PRO": 200,
    "PREMIUM": 1000,
}

user_plans = {}
user_usage = {}

def get_user_plan(user_id):
    return user_plans.get(user_id, "FREE")

def add_usage(user_id, pages):
    user_usage[user_id] = user_usage.get(user_id, 0) + pages

def get_usage(user_id):
    return user_usage.get(user_id, 0)

# ===== COMMANDS =====

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "📄 PDF to Text Bot\n\n"
        "Send me a PDF file and I will convert it.\n"
        "Use /plan to see your current plan."
    )

@dp.message(Command("plan"))
async def plan(message: Message):
    user_id = message.from_user.id
    current_plan = get_user_plan(user_id)
    usage = get_usage(user_id)
    limit = PLANS[current_plan]

    await message.answer(
        f"📊 Your Plan: {current_plan}\n"
        f"Daily Limit: {limit} pages\n"
        f"Used Today: {usage} pages"
    )

@dp.message(Command("setplan"))
async def setplan(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Not allowed.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("Usage: /setplan USER_ID PLAN")

    target_id = int(parts[1])
    plan = parts[2].upper()

    if plan not in PLANS:
        return await message.answer("Invalid plan.")

    user_plans[target_id] = plan
    await message.answer(f"Plan {plan} set for user {target_id}")

# ===== PDF HANDLER =====

@dp.message(F.document)
async def handle_pdf(message: Message):
    user_id = message.from_user.id
    document = message.document

    if not document.file_name.lower().endswith(".pdf"):
        return await message.answer("Please send a PDF file.")

    current_plan = get_user_plan(user_id)
    limit = PLANS[current_plan]

    file = await bot.get_file(document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    pdf_data = file_bytes.read()

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    pages = len(doc)

    if get_usage(user_id) + pages > limit:
        doc.close()
        return await message.answer("Daily limit exceeded.")

    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()

    if not text.strip():
        return await message.answer("No text found.")

    add_usage(user_id, pages)

    # Send TXT
    await message.answer_document(
        BufferedInputFile(text.encode(), filename="converted.txt")
    )

    # Send DOCX if PRO or PREMIUM
    if current_plan in ["PRO", "PREMIUM"]:
        document_docx = Document()
        document_docx.add_paragraph(text)
        document_docx.save("temp.docx")

        with open("temp.docx", "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename="converted.docx")
            )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())