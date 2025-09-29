import os
import re
import time
import sqlite3
import asyncio
import logging
from contextlib import closing

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, Update
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from fastapi import FastAPI, Request
import uvicorn


# ======================= ENV / CONFIG =======================
load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_PHONE   = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")          # ixtiyoriy (user ID yoki kanal ID)
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")            # masalan: https://davontaksibot-3.onrender.com

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo'q. Render > Environment’da BOT_TOKEN qo‘shing.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("davon-taksi-bot")


# ======================= DATABASE ===========================
DB_PATH = "orders.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_user_id INTEGER,
            full_name TEXT,
            username TEXT,
            phone TEXT,
            route_from TEXT,
            from_district TEXT,
            route_to TEXT,
            to_district TEXT,
            people INTEGER,
            cargo TEXT,
            note TEXT,
            created_at INTEGER
        );
        """)
init_db()


# ======================= BOT / FSM ==========================
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

class OrderForm(StatesGroup):
    phone         = State()
    route_from    = State()
    from_district = State()
    route_to      = State()
    to_district   = State()
    choice        = State()
    note          = State()


# ======================= KEYBOARDS ==========================
BACK = "🔙 Орқага"

def kb_request_phone():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефонни улашиш", request_contact=True)],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

CITIES = ["Тошкент", "Қўқон"]  # faqat shu ikkitasi

def kb_cities():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Тошкент")],
            [KeyboardButton(text="Қўқон")],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

def kb_choice():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
            [KeyboardButton(text="4"), KeyboardButton(text="5+")],
            [KeyboardButton(text="📦 Почта бор")],   # "Фақат юк" o‘rniga
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

def kb_back_only():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BACK)]], resize_keyboard=True)


# ======================= VALIDATION =========================
PHONE_RE = re.compile(r"^\+?\d{7,15}$")

def normalize_phone(s: str) -> str:
    s = (s or "").strip().replace(" ", "")
    if s.startswith("00"): s = "+" + s[2:]
    if s.startswith("998") and len(s) == 12: s = "+" + s
    if not s.startswith("+") and s.isdigit(): s = "+" + s
    return s

def is_valid_phone(s: str) -> bool:
    return bool(PHONE_RE.match(s or ""))

def looks_like_cargo_only(text: str) -> bool:
    t = (text or "").lower()
    return ("почта" in t) or ("pocta" in t) or (t == "📦 почта бор".lower())

def people_to_int(s: str):
    allowed = {"1", "2", "3", "4", "5+"}
    if s not in allowed:
        return None
    return 5 if s.endswith("+") else int(s)

def trim_note(s: str) -> str | None:
    if s is None: return None
    s = s.strip()
    if not s: return None
    if len(s) > 350: return None
    return s

def trim_district(s: str) -> str | None:
    if s is None: return None
    s = s.strip()
    return s if 2 <= len(s) <= 60 else None


# ======================= SAVE / NOTIFY ======================
async def save_order_safe(m: Message, data: dict):
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute(
                """INSERT INTO orders
                (tg_user_id, full_name, username, phone,
                 route_from, from_district, route_to, to_district,
                 people, cargo, note, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m.from_user.id, m.from_user.full_name, m.from_user.username,
                    data.get("phone"),
                    data.get("route_from"), data.get("from_district"),
                    data.get("route_to"), data.get("to_district"),
                    int(data.get("people", 0)),
                    data.get("cargo", "-"),
                    data.get("note", "-"),
                    int(time.time()),
                )
            )
    except Exception as e:
        log.exception("[DB] Save failed: %s", e)

async def notify_operator_safe(m: Message, data: dict):
    if not ADMIN_CHAT_ID:
        return
    try:
        txt = (
            "🆕 *Янги буюртма*\n"
            f"👤 {m.from_user.full_name} @{m.from_user.username or '-'} (ID: {m.from_user.id})\n"
            f"📞 {data.get('phone')}\n"
            f"🚖 Йўналиш: {data.get('route_from')} ({data.get('from_district')}) → "
            f"{data.get('route_to')} ({data.get('to_district')})\n"
            f"👥 Одам: {data.get('people') or '-'}\n"
            f"📦 Почта: {data.get('cargo')}\n"
            f"📝 Изоҳ: {data.get('note', '-')}"
        )
        await bot.send_message(int(ADMIN_CHAT_ID), txt, parse_mode="Markdown")
    except Exception as e:
        log.exception("[ADMIN] Notify failed: %s", e)

async def finalize(m: Message, state: FSMContext):
    data = await state.get_data()
    await save_order_safe(m, data)
    await notify_operator_safe(m, data)

    confirm = (
        "✅ Буюртма қабул қилинди!\n\n"
        f"📞 Телефон: {data.get('phone')}\n"
        f"🚖 Йўналиш: {data.get('route_from')} ({data.get('from_district')}) → "
        f"{data.get('route_to')} ({data.get('to_district')})\n"
        f"👥 Одам: {data.get('people') or '-'}\n"
        f"📦 Почта: {data.get('cargo')}\n"
        f"📝 Изоҳ: {data.get('note', '-')}\n\n"
        f"🧑‍💼 Оператор телефон рақами: {ADMIN_PHONE}\n"
        "Янги буюртма учун /start ни босинг."
    )
    await m.answer(confirm, reply_markup=ReplyKeyboardRemove())
    await state.clear()


# ======================= HANDLERS ===========================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Ассалому алайкум 🙌\n"
        "🚖 *DAVON EXPRESS TAXI* хизматига хуш келибсиз!\n\n"
        "Буюртма бериш учун пастдаги тугма орқали телефон рақамингизни юборинг.",
        reply_markup=kb_request_phone(),
        parse_mode="Markdown",
    )
    await state.set_state(OrderForm.phone)

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Бекор қилинди. Бошидан /start ни босинг.", reply_markup=ReplyKeyboardRemove())

# 1) Telefon
@dp.message(OrderForm.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext):
    ph = normalize_phone(m.contact.phone_number)
    if not is_valid_phone(ph):
        await m.answer("❗️ Контактдан келган телефон нотўғри. Қайта улашинг ёки қўлда ёзинг.",
                       reply_markup=kb_request_phone())
        return
    await state.update_data(phone=ph)
    await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_from)

@dp.message(OrderForm.phone)
async def phone_from_text(m: Message, state: FSMContext):
    ph = normalize_phone(m.text)
    if not is_valid_phone(ph):
        await m.answer("❗️ Телефон нотўғри. +99890XXXXXXX кўринишида ёзинг ёки тугмадан фойдаланинг.",
                       reply_markup=kb_request_phone())
        return
    await state.update_data(phone=ph)
    await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_from)

# 2) From City
@dp.message(OrderForm.route_from)
async def select_from_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📱 Телефон рақамингизни юборинг.", reply_markup=kb_request_phone())
        await state.set_state(OrderForm.phone)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, рўйхатдан танланг.", reply_markup=kb_cities())
        return
    await state.update_data(route_from=txt)
    await m.answer("🏙 Тошкент/Қўқоннинг қайси тумани? (матн билан ёзинг)", reply_markup=kb_back_only())
    await state.set_state(OrderForm.from_district)

# 3) From District
@dp.message(OrderForm.from_district)
async def from_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_from)
        return
    dist = trim_district(txt)
    if dist is None:
        await m.answer("❗️ Туман номи 2–60 белгидан иборат бўлсин. Қайта киритинг.", reply_markup=kb_back_only())
        return
    await state.update_data(from_district=dist)
    await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_to)

# 4) To City
@dp.message(OrderForm.route_to)
async def select_to_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("🏙 Қайси тумандан жўнайсиз? (матн)", reply_markup=kb_back_only())
        await state.set_state(OrderForm.from_district)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, рўйхатдан танланг.", reply_markup=kb_cities())
        return
    data = await state.get_data()
    if data.get("route_from") == txt:
        await m.answer("❗️ Жўнаш ва бориш шаҳари бир хил бўлмасин. Бошқа шаҳарни танланг.", reply_markup=kb_cities())
        return
    await state.update_data(route_to=txt)
    await m.answer("🏙 Бориш тумани қайси? (матн билан ёзинг)", reply_markup=kb_back_only())
    await state.set_state(OrderForm.to_district)

# 5) To District
@dp.message(OrderForm.to_district)
async def to_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_to)
        return
    dist = trim_district(txt)
    if dist is None:
        await m.answer("❗️ Туман номи 2–60 белгидан иборат бўлсин. Қайта киритинг.", reply_markup=kb_back_only())
        return
    await state.update_data(to_district=dist)
    await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# 6) Choice
@dp.message(OrderForm.choice)
async def choice_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("🏙 Бориш тумани қайси? (матн)", reply_markup=kb_back_only())
        await state.set_state(OrderForm.to_district)
        return

    if looks_like_cargo_only(txt):
        await state.update_data(people=0, cargo="Бор", note="-")
        await finalize(m, state)
        return

    p = people_to_int(txt)
    if p is None:
        await m.answer("❗️ Тугмалардан фойдаланинг: 1,2,3,4,5+ ёки «📦 Почта бор».", reply_markup=kb_choice())
        return

    await state.update_data(people=p, cargo="Йўқ")
    await m.answer("📝 Қўшимча изоҳ (вақт, манзил...). Агар йўқ бўлса, «-» деб ёзинг.", reply_markup=kb_back_only())
    await state.set_state(OrderForm.note)

# 7) Note
@dp.message(OrderForm.note)
async def note_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
        await state.set_state(OrderForm.choice)
        return

    if looks_like_cargo_only(txt):
        await state.update_data(people=0, cargo="Бор", note="-")
        await finalize(m, state)
        return

    note = "-" if txt == "-" else trim_note(txt)
    if note is None:
        await m.answer("❗️ Изоҳ жуда қисқа/узун. «-» деб ёзсангиз ҳам бўлади.", reply_markup=kb_back_only())
        return

    await state.update_data(note=note)
    await finalize(m, state)


# ======================= FASTAPI (WEBHOOK) ==================
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok", "service": "davon-taksi-bot"}

@app.get("/health")
def health():
    return {"ok": True}

# GET test va POST real webhook uchun; trailing slash bilan ham ishlasin
@app.api_route("/webhook", methods=["GET", "POST"])
@app.api_route("/webhook/", methods=["GET", "POST"])
async def telegram_webhook(request: Request):
    if request.method == "GET":
        return {"ok": True}
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# Webhookni qo'lda o‘rnatish uchun qulay endpoint
@app.post("/set-webhook")
async def set_webhook():
    if not WEBHOOK_URL:
        return {"ok": False, "error": "WEBHOOK_URL yo'q"}
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook")
    return {"ok": True, "url": f"{WEBHOOK_URL.rstrip('/')}/webhook"}

@app.on_event("startup")
async def on_startup():
    # Render ishga tushganda webhookni avtomatik o‘rnatamiz
    if WEBHOOK_URL:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook")
            log.info("Webhook set: %s/webhook", WEBHOOK_URL.rstrip('/'))
        except Exception as e:
            log.exception("Webhook set failed: %s", e)


# ======================= LOCAL RUN (ixtiyoriy) ==============
# Lokal sinov: uvicorn main:app --host 0.0.0.0 --port 8000
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
