import os
import re
import time
import sqlite3
from contextlib import closing

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

# ---- FastAPI (webhook uchun) ----
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ================== .env ==================
load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_PHONE   = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ixtiyoriy (raqam bo‘lishi kerak)
WEB_APP_URL   = os.getenv("WEB_APP_URL")    # Render webhook uchun: https://<servis>.onrender.com

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN топилмади (.env ни текширинг).")

# WEB_APP_URL faqat webhook rejimi uchun kerak bo‘ladi
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = None
if WEB_APP_URL:
    WEBHOOK_URL = f"{WEB_APP_URL.rstrip('/')}{WEBHOOK_PATH}"

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

# ================== Bot / Dispatcher ==================
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# FastAPI ilovasi (faqat webhookda ishlatiladi)
app = FastAPI()

# ================== States ==================
class OrderForm(StatesGroup):
    phone            = State()
    route_from_city  = State()
    route_from_dist  = State()
    route_to_city    = State()
    route_to_dist    = State()
    choice           = State()   # odam soni yoki "pochta bor"
    note             = State()   # faqat odam tanlanganda

# ================== Keyboards ==================
BACK = "🔙 Орқага"

def kb_request_phone():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Телефонни улашиш", request_contact=True)],
                  [KeyboardButton(text=BACK)]],
        resize_keyboard=True
    )

# Faqat 2 shahar
CITIES = ["Тошкент шаҳри", "Қўқон шаҳри"]

def kb_cities():
    rows = [[KeyboardButton(text="Тошкент шаҳри"), KeyboardButton(text="Қўқон шаҳри")],
            [KeyboardButton(text=BACK)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def kb_choice():
    rows = [
        [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
        [KeyboardButton(text="4"), KeyboardButton(text="5+")],
        [KeyboardButton(text="📦 Почта бор")],
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

def looks_like_post_only(text: str) -> bool:
    t = (text or "").lower()
    return ("почта" in t) or ("pocta" in t) or ("pochta" in t) or (t == "📦 почта бор".lower())

def people_to_int(s: str):
    allowed = {"1", "2", "3", "4", "5+"}
    if s not in allowed:
        return None
    return 5 if s.endswith("+") else int(s)

def trim_free_text(s: str) -> str | None:
    if s is None:
        return None
    s = s.strip()
    if len(s) < 2 or len(s) > 60:
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
            f"📦 Почта: {cargo}\n"
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
        f"📦 Почта: {cargo}\n"
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
    await state.update_data(phone=ph, full_name=m.from_user.full_name or "-", username=m.from_user.username or "-")
    await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_from_city)

@dp.message(OrderForm.phone)
async def phone_from_text(m: Message, state: FSMContext):
    ph = normalize_phone(m.text)
    if not is_valid_phone(ph):
        await m.answer("❗️ Телефон нотўғри. +99890XXXXXXX кўринишида ёзинг ёки тугмадан фойдаланинг.",
                       reply_markup=kb_request_phone())
        return
    await state.update_data(phone=ph, full_name=m.from_user.full_name or "-", username=m.from_user.username or "-")
    await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_from_city)

# ================== 2) From city ==================
@dp.message(OrderForm.route_from_city)
async def select_from_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📱 Телефон рақамингизни юборинг.", reply_markup=kb_request_phone())
        await state.set_state(OrderForm.phone)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, қуйидаги 2 шаҳардан бирини танланг.", reply_markup=kb_cities())
        return
    await state.update_data(route_from_city=txt)
    await m.answer("🏘 Қайси тумани?", reply_markup=kb_back_only())
    await state.set_state(OrderForm.route_from_dist)

# ================== 3) From district ==================
@dp.message(OrderForm.route_from_dist)
async def from_district(m: Message, state: FSMContext):
    if (m.text or "") == BACK:
        await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_from_city)
        return
    dist = trim_free_text(m.text)
    if not dist:
        await m.answer("❗️ Туман номини қисқа ва аниқ ёзинг (2–60 белги).", reply_markup=kb_back_only())
        return
    await state.update_data(route_from_dist=dist)
    await m.answer("🏁 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_to_city)

# ================== 4) To city ==================
@dp.message(OrderForm.route_to_city)
async def select_to_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("🏘 Қайси тумани?", reply_markup=kb_back_only())
        await state.set_state(OrderForm.route_from_dist)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, қуйидаги 2 шаҳардан бирини танланг.", reply_markup=kb_cities())
        return
    data = await state.get_data()
    # bir shahar ichida (tuman->tuman) ga ham ruxsat
    await state.update_data(route_to_city=txt)
    await m.answer("🏘 Бориш тумани?", reply_markup=kb_back_only())
    await state.set_state(OrderForm.route_to_dist)

# ================== 5) To district ==================
@dp.message(OrderForm.route_to_dist)
async def to_district(m: Message, state: FSMContext):
    if (m.text or "") == BACK:
        await m.answer("🏁 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_to_city)
        return
    dist = trim_free_text(m.text)
    if not dist:
        await m.answer("❗️ Туман номини қисқа ва аниқ ёзинг (2–60 белги).", reply_markup=kb_back_only())
        return
    await state.update_data(route_to_dist=dist)
    await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# ================== 6) Танлов: одам ёки почта ==================
@dp.message(OrderForm.choice)
async def handle_choice(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer("🏘 Бориш тумани?", reply_markup=kb_back_only())
        await state.set_state(OrderForm.route_to_dist)
        return

    if looks_like_post_only(txt):
        data = await state.get_data()
        route = f"{data.get('route_from_city')} ({data.get('route_from_dist')}) → {data.get('route_to_city')} ({data.get('route_to_dist')})"
        await finalize(
            m, state,
            phone=data.get("phone"),
            route=route,
            people=0,
            cargo="Бор",   # Почта бор
            note="-"
        )
        return

    p = people_to_int(txt)
    if p is None:
        await m.answer("❗️ Фақат тугмалардан фойдаланинг: 1, 2, 3, 4, 5+ ёки 📦 Почта бор.", reply_markup=kb_choice())
        return

    await state.update_data(people=p)
    await m.answer("📝 Қўшимча изоҳ (вақт, манзил...). Агар йўқ бўлса, «-» деб ёзинг.", reply_markup=kb_back_only())
    await state.set_state(OrderForm.note)

# ================== 7) Изоҳ (фақат одам танланганда) ==================
@dp.message(OrderForm.note)
async def step_note(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
        await state.set_state(OrderForm.choice)
        return

    note = "-" if txt == "-" else (trim_free_text(txt) or "-")

    data = await state.get_data()
    route = f"{data.get('route_from_city')} ({data.get('route_from_dist')}) → {data.get('route_to_city')} ({data.get('route_to_dist')})"
    await finalize(
        m, state,
        phone=data.get("phone"),
        route=route,
        people=int(data.get("people", 1)),
        cargo="Йўқ",
        note=note
    )

# ================== FastAPI / Webhook (Render Web Service) ==================
@app.on_event("startup")
async def on_startup():
    # Webhook rejimi faqat WEB_APP_URL bo‘lsa yoqiladi
    init_db()
    if WEBHOOK_URL:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        await bot.set_webhook(WEBHOOK_URL)

@app.post(WEBHOOK_PATH)
async def tg_webhook(request: Request):
    # FastAPI webhook qabul qilganda shu ishlaydi
    payload = await request.json()
    update = types.Update.model_validate(payload, context={"bot": bot})  # aiogram v3
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

@app.get("/")
async def root():
    return {"status": "ok", "service": "davon-taksi-bot"}

# ================== Lokal sinov (polling) ==================
if __name__ == "__main__":
    import asyncio
    async def _local():
        # Lokal rejimda webhookni o‘chirib, polling bilan ishga tushiramiz
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        init_db()
        print("🚖 DAVON EXPRESS TAXI боти локалда ишга тушди…")
        await dp.start_polling(bot)

    asyncio.run(_local())
