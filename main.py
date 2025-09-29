import os
import re
import time
import sqlite3
import asyncio
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

# ================== .env ==================
# .envда:
# BOT_TOKEN=xxxxxxxxx:yyyyyyyy
# ADMIN_PHONE=+99890xxxxxxx
# (ихтиёрий) ADMIN_CHAT_ID=123456789  ёки канал ID: -100xxxx
load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_PHONE   = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ихтиёрий

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN топилмади (.env ни текширинг).")

# ================== SQLite ==================
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
            route TEXT,
            people INTEGER,
            cargo TEXT,
            note TEXT,
            created_at INTEGER
        );
        """)
init_db()

# ================== Bot / Dispatcher ==================
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ================== States ==================
class OrderForm(StatesGroup):
    phone      = State()
    route_from = State()
    route_to   = State()
    choice     = State()   # одам сони ёки "фақат юк"
    note       = State()   # фақат одам танланганда

# ================== Keyboards ==================
BACK = "🔙 Орқага"

def kb_request_phone():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Телефонни улашиш", request_contact=True)],
                  [KeyboardButton(text=BACK)]],
        resize_keyboard=True
    )

# Шаҳарлар рўйхати — истаганингизча ўзгартиринг/кўпайтиринг
CITIES = [
    "Тошкент", "Қўқон", "Наманган", "Андижон", "Фарғона",
    "Самарқанд", "Бухоро", "Навоий", "Қарши", "Термиз",
    "Нукус", "Хива"
]

def kb_cities():
    rows, row = [], []
    for i, name in enumerate(CITIES, start=1):
        row.append(KeyboardButton(text=name))
        if i % 3 == 0:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def kb_choice():
    rows = [
        [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
        [KeyboardButton(text="4"), KeyboardButton(text="5+")],
        [KeyboardButton(text="📦 Фақат юк")],
        [KeyboardButton(text=BACK)]
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def kb_back_only():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BACK)]], resize_keyboard=True)

# ================== Validators / helpers ==================
PHONE_RE = re.compile(r"^\+?\d{7,15}$")

def normalize_phone(s: str) -> str:
    s = (s or "").strip().replace(" ", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("998") and len(s) == 12:
        s = "+" + s
    if not s.startswith("+") and s.isdigit():
        s = "+" + s
    return s

def is_valid_phone(s: str) -> bool:
    return bool(PHONE_RE.match(s or ""))

def looks_like_cargo_only(text: str) -> bool:
    t = (text or "").lower()
    return ("юк" in t) or ("yuk" in t) or (t == "📦 фақат юк".lower())

def people_to_int(s: str):
    allowed = {"1", "2", "3", "4", "5+"}
    if s not in allowed:
        return None
    return 5 if s.endswith("+") else int(s)

def trim_note(s: str) -> str | None:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    if len(s) > 350:
        return None
    return s

async def save_order_safe(m: Message, phone: str, route: str, people: int, cargo: str, note: str):
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute(
                "INSERT INTO orders (tg_user_id, full_name, username, phone, route, people, cargo, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m.from_user.id, m.from_user.full_name, m.from_user.username,
                 phone, route, people, cargo, note, int(time.time()))
            )
    except Exception as e:
        print("[DB] Save failed:", e)

async def notify_operator_safe(m: Message, phone: str, route: str, people: int, cargo: str, note: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        txt = (
            "🆕 *Янги буюртма*\n"
            f"👤 {m.from_user.full_name} @{m.from_user.username or '-'} (ID: {m.from_user.id})\n"
            f"📞 {phone}\n"
            f"🚖 {route}\n"
            f"👥 Одам: {people if people>0 else '-'}\n"
            f"📦 Юк: {cargo}\n"
            f"📝 {note}"
        )
        await bot.send_message(int(ADMIN_CHAT_ID), txt, parse_mode="Markdown")
    except Exception as e:
        print("[ADMIN] Notify failed:", e)

async def finalize(m: Message, state: FSMContext, phone: str, route: str, people: int, cargo: str, note: str):
    await save_order_safe(m, phone, route, people, cargo, note)
    await notify_operator_safe(m, phone, route, people, cargo, note)
    confirm = (
        "✅ Буюртма қабул қилинди!\n\n"
        f"📞 Телефон: {phone}\n"
        f"🚖 Йўналиш: {route}\n"
        f"👥 Одам: {people if people>0 else '-'}\n"
        f"📦 Юк: {cargo}\n"
        f"📝 Изоҳ: {note}\n\n"
        f"🧑‍💼 Оператор телефон рақами: {ADMIN_PHONE}\n"
        "Янги буюртма учун /start ни босинг."
    )
    await m.answer(confirm, reply_markup=ReplyKeyboardRemove())
    await state.clear()

# ================== Commands ==================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Ассалому алайкум 🙌\n"
        "🚖 *DAVON EXPRESS TAXI* хизматига хуш келибсиз!\n\n"
        "Буюртма бериш учун пастдаги тугма орқали телефон рақамингизни юборинг.",
        reply_markup=kb_request_phone(),
        parse_mode="Markdown"
    )
    await state.set_state(OrderForm.phone)

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Бекор қилинди. Бошидан /start ни босинг.", reply_markup=ReplyKeyboardRemove())

# ================== 1) Телефон ==================
@dp.message(OrderForm.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext):
    ph = normalize_phone(m.contact.phone_number)
    if not is_valid_phone(ph):
        await m.answer("❗️ Контактдан келган телефон нотўғри. Қайта улашинг ёки қўлда ёзинг.", reply_markup=kb_request_phone())
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

# ================== 2) Қаердан ==================
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
    await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_to)

# ================== 3) Қаерга ==================
@dp.message(OrderForm.route_to)
async def select_to_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_from)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, рўйхатдан танланг.", reply_markup=kb_cities())
        return
    data = await state.get_data()
    from_city = data.get("route_from")
    if from_city == txt:
        await m.answer("❗️ Жўнаш ва бориш шаҳари бир хил бўлмаслиги керак. Бошқа шаҳарни танланг.", reply_markup=kb_cities())
        return
    normalized_route = f"{from_city} → {txt}"
    await state.update_data(route=normalized_route)
    await m.answer("👥 Қуйида одам сонини танланг ёки «📦 Фақат юк» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# ================== 4) Танлов: одам ёки фақат юк ==================
@dp.message(OrderForm.choice)
async def handle_choice(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_to)
        return

    if looks_like_cargo_only(txt):
        data = await state.get_data()
        await finalize(
            m, state,
            phone=data.get("phone"),
            route=data.get("route"),
            people=0,
            cargo="Бор",
            note="-"
        )
        return

    p = people_to_int(txt)
    if p is None:
        await m.answer("❗️ Фақат тугмалардан фойдаланинг: 1, 2, 3, 4, 5+ ёки 📦 Фақат юк.", reply_markup=kb_choice())
        return

    await state.update_data(people=p, cargo="Йўқ")
    await m.answer("📝 Қўшимча изоҳ (вақт, манзил...). Агар йўқ бўлса, «-» деб ёзинг.", reply_markup=kb_back_only())
    await state.set_state(OrderForm.note)

# ================== 5) Изоҳ (фақат одам танланганда) ==================
@dp.message(OrderForm.note)
async def step_note(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer("👥 Қуйида одам сонини танланг ёки «📦 Фақат юк» ни босинг:", reply_markup=kb_choice())
        await state.set_state(OrderForm.choice)
        return

    if looks_like_cargo_only(txt):
        data = await state.get_data()
        await finalize(
            m, state,
            phone=data.get("phone"),
            route=data.get("route"),
            people=0,
            cargo="Бор",
            note="-"
        )
        return

    note = "-" if txt == "-" else trim_note(txt)
    if note is None:
        await m.answer("❗️ Изоҳ жуда қисқа ёки жуда узун. «-» деб ёзсангиз ҳам бўлади.", reply_markup=kb_back_only())
        return

    data = await state.get_data()
    await finalize(
        m, state,
        phone=data.get("phone"),
        route=data.get("route"),
        people=int(data.get("people", 1)),
        cargo=data.get("cargo", "Йўқ"),
        note=note
    )

# ================== Run ==================
async def main():
    print("🚖 DAVON EXPRESS TAXI боти ишга тушди…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
