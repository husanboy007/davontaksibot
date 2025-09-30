import os
import re
import time
import sqlite3
import asyncio
import logging
from contextlib import closing
from typing import List

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State


# ================== ENV ==================
load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_PHONE   = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ixtiyoriy

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo'q. .env faylini to'ldiring!")

# ================== LOG ==================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("davon-taksi-bot")

# ================== DB ==================
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

# ================== BOT/DP ==================
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ================== STATES ==================
class OrderForm(StatesGroup):
    phone         = State()
    route_from    = State()
    from_district = State()
    route_to      = State()
    to_district   = State()
    choice        = State()   # odam soni yoki “pochta bor”

# ================== TEXT & KBs ==================
BACK = "🔙 Орқага"
NEXT = "➡️ Кейинги"
PREV = "⬅️ Олдинги"

def kb_request_phone():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефонни улашиш", request_contact=True)],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

CITIES = ["Тошкент", "Қўқон"]

def kb_cities():
    rows = [[KeyboardButton(text="Тошкент")],
            [KeyboardButton(text="Қўқон")],
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

# ================== VALIDATION ==================
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
    return ("почта" in t) or (t == "📦 почта бор".lower())

def people_to_int(s: str):
    allowed = {"1", "2", "3", "4", "5+"}
    if s not in allowed:
        return None
    return 5 if s.endswith("+") else int(s)

# ================== DISTRICTS ==================
QOQON_DISTRICTS: List[str] = [
    "Қўқон шахар","Янгибозор/Опт","Янгибозор 65","Навоий","Урганжибоғ","Янгичорсу","Чорсу",
    "Космонавт","Химик","Вокзал","Бабушкин","Тўҳлимерган","Дегрезлик","Гор/Ҳокимият",
    "Гор/Дилшод","Гор больница","Чархий","Ғозиёғлиқ","Романка","Азиз тепа","Ғишткўприк",
    "Спортивный","Водоканал","40 лет","Зелённый","ЧПК","Гор. отдель","Большевик",
    "Ғиштли масжид","Минг тут","Автовокзал","МЖК","Калвак","Арчазор","Горгаз","Шиша бозор",
    "Саодат масжиди","Тулабой","Данғара","Учкўприк","Бака чорсу","Динам","Сарботир",
    "Найманча","Мяс комбинат","Мел комбинат","Городской","Айрилиш","10 автобаза",
    "Пед колледж","Ипак йўли","Ярмарка","Авғонбоқ","Охак бозор","Автодарож","Городок",
    "Ойим қишлоқ","Аерапорт","Қўқонбой","Оқ жар",
]

TOSHKENT_DISTRICTS: List[str] = [
    "Абу сахий","Авиасозлар 22","Авиасозлар 4","Аерапорт","Ахмад","Ахмад олтин жужа","Алгаритим",
    "Алмалик","Амир Темур сквер","Ангрен","Ашхабод боғи","Бек барака","Беруний Метро","Битонка",
    "Болалар миллий тиббиёт","Буюк ипак йули метро","ВОДНИК","Ғишт кўприк чегара","Ғофур Ғулом метро",
    "Ғунча","Дўстлик метро","Еркин мост","Жангох","Жарарик","Зангота Зиёратгоҳ","Жоме масжид",
    "Ибн сино 1","Ипадром","Камолон","Кардиалогия маркази","Кафе квартал","Кафедра... (йўқ экан)",
    "Келес","Корасув","Косманавтлар метро","Кока кола завод","Куйлюк 1","Куйлюк 2","Куйлюк 4",
    "Куйлюк 5","Куйлюк 6","Курувчи","Миробод Бозори","Миробод тумани","Мирзо Улугбек","Минор метро",
    "Минг урик","Маъруф ота масжиди","Машинасозлар метро","Межик ситий","Миллий боғ метро",
    "Мустақиллик майдони","Навоий куча","Некст маал","Олмазор","Олмалик","Охангарон","Олой бозори",
    "Олим полвон","Панелний","Паркент Бозори","Паркент тумани","Перевал","Рохат","Сағбон","Себзор",
    "Сергили","Сергили 6","Северный вогзал","Солношка","Собир Рахимов","Тахтапул","Ташкент ситий",
    "ТТЗ бозор","Фаргона йули","Фарход бозори","Фууд ситий","Хадра майдони","Халқлар дўстлиги",
    "Хайвонот боги","Хумо Арена","Чигатой","Чилонзор","Чилонзор","Чирчиқ","Чорсу","Чупон ота",
    "Шайхон Тохур","Шаршара","Шота Руставили","Янги бозор","Янги йул","Янги Чош Тепа",
    "Янги обод бозор","Янгиобод бозори","Яланғоч","Яшинобод тумани","Яккасарой","Ёшлик метро",
    "Юнусобод","Южный вогзал","Қафе квартал","Қушбеги","Қўйлиқ 5","Центр Бешкозон","Центрланый парк"
]

def districts_for_city(city: str) -> List[str]:
    return TOSHKENT_DISTRICTS if city == "Тошкент" else QOQON_DISTRICTS

def chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def kb_districts(city: str, page: int = 1, per_page: int = 8, cols: int = 2):
    data = districts_for_city(city)
    total_pages = max(1, (len(data) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    items = data[start:start + per_page]
    rows = []
    for r in chunk(items, cols):
        rows.append([KeyboardButton(text=x) for x in r])

    nav = []
    if page > 1:
        nav.append(KeyboardButton(text=PREV))
    nav.append(KeyboardButton(text=f"{page}/{total_pages}"))
    if page < total_pages:
        nav.append(KeyboardButton(text=NEXT))
    rows.append(nav)
    rows.append([KeyboardButton(text=BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ================== SAVE / NOTIFY ==================
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
        f"📦 Почта: {data.get('cargo')}\n\n"
        f"🧑‍💼 Оператор рақами: {ADMIN_PHONE}\n"
        "Янги буюртма учун /start ни босинг."
    )
    await m.answer(confirm, reply_markup=ReplyKeyboardRemove())
    await state.clear()

# ================== HANDLERS ==================
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
    await m.answer("❌ Бекор қилинди. /start", reply_markup=ReplyKeyboardRemove())

# 1) Telefon
@dp.message(OrderForm.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext):
    ph = normalize_phone(m.contact.phone_number)
    if not is_valid_phone(ph):
        await m.answer("❗️ Телефон нотўғри. Қайта улашинг ёки қўлда ёзинг.", reply_markup=kb_request_phone())
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

    await state.update_data(route_from=txt, from_page=1)
    await m.answer(f"🏙 {txt} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(txt, page=1))
    await state.set_state(OrderForm.from_district)

# 3) From District (paging)
@dp.message(OrderForm.from_district)
async def from_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    data = await state.get_data()
    city = data.get("route_from")
    page = int(data.get("from_page", 1))

    if txt == BACK:
        await m.answer("📍 Қаердан жўнайсиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_from)
        return
    if txt == NEXT:
        page += 1
        await state.update_data(from_page=page)
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(city, page))
        return
    if txt == PREV:
        page = max(1, page - 1)
        await state.update_data(from_page=page)
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(city, page))
        return

    if txt not in districts_for_city(city):
        await m.answer("❗️ Илтимос, тугмалардан танланг.", reply_markup=kb_districts(city, page))
        return

    await state.update_data(from_district=txt)
    await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
    await state.set_state(OrderForm.route_to)

# 4) To City
@dp.message(OrderForm.route_to)
async def select_to_city(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    data = await state.get_data()
    if txt == BACK:
        city = data.get("route_from")
        page = int(data.get("from_page", 1))
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(city, page))
        await state.set_state(OrderForm.from_district)
        return
    if txt not in CITIES:
        await m.answer("❗️ Илтимос, рўйхатдан танланг.", reply_markup=kb_cities())
        return
    if data.get("route_from") == txt:
        await m.answer("❗️ Жўнаш ва бориш шаҳари бир хил бўлмасин. Бошқа шаҳарни танланг.", reply_markup=kb_cities())
        return

    await state.update_data(route_to=txt, to_page=1)
    await m.answer(f"🏙 {txt} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(txt, page=1))
    await state.set_state(OrderForm.to_district)

# 5) To District (paging)
@dp.message(OrderForm.to_district)
async def to_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    data = await state.get_data()
    city = data.get("route_to")
    page = int(data.get("to_page", 1))

    if txt == BACK:
        await m.answer("📍 Қаерга борасиз? Шаҳарни танланг.", reply_markup=kb_cities())
        await state.set_state(OrderForm.route_to)
        return
    if txt == NEXT:
        page += 1
        await state.update_data(to_page=page)
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(city, page))
        return
    if txt == PREV:
        page = max(1, page - 1)
        await state.update_data(to_page=page)
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANЛАНГ!", reply_markup=kb_districts(city, page))
        return

    if txt not in districts_for_city(city):
        await m.answer("❗️ Илтимос, тугмалардан танланг.", reply_markup=kb_districts(city, page))
        return

    await state.update_data(to_district=txt)
    await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# 6) Choice (izoh bosqichi yo‘q)
@dp.message(OrderForm.choice)
async def choice_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        data = await state.get_data()
        city = data.get("route_to")
        page = int(data.get("to_page", 1))
        await m.answer(f"🏙 {city} — ILTIMOS HUDUDNI TANLANG!", reply_markup=kb_districts(city, page))
        await state.set_state(OrderForm.to_district)
        return

    if looks_like_cargo_only(txt):
        await state.update_data(people=0, cargo="Бор", note="-")
        await finalize(m, state)
        return

    p = people_to_int(txt)
    if p is None:
        await m.answer("❗️ 1,2,3,4,5+ ёки «📦 Почта бор».", reply_markup=kb_choice())
        return

    await state.update_data(people=p, cargo="Йўқ", note="-")
    await finalize(m, state)

# ================== RUN (Polling) ==================
async def main():
    log.info("Starting polling…")
    # polling ishlashi uchun — eski webhook bo‘lsa olib tashlaymiz
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await dp.start_polling(bot, allowed_updates=["message"])

if __name__ == "__main__":
    asyncio.run(main())
