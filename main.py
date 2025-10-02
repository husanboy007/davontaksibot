# main.py
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
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ============== ENV & LOG ==============
load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_PHONE   = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ixtiyoriy (gruppa ID)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN йўқ. .env файлини тўлдиринг!")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("davon-taksi-bot")

# ============== DB ==============
DB_PATH = "orders.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        # foydalanuvchilar ro'yxati
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            tg_user_id INTEGER PRIMARY KEY,
            full_name  TEXT,
            username   TEXT,
            joined_at  INTEGER,
            last_seen  INTEGER
        );
        """)
        # buyurtmalar
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

# ============== BOT/DP ==============
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ============== STATES ==============
class OrderForm(StatesGroup):
    phone         = State()
    route_from    = State()   # juft yo'nalish tanlanadi
    from_district = State()
    to_district   = State()
    choice        = State()

# ============== TEXTS & KEYBOARDS ==============
BACK = "🔙 Орқага"
NEXT = "➡️ Кейинги"
PREV = "⬅️ Олдинги"

WELCOME_TEXT = (
    "🚖 *DAVON EXPRESS TAXI*\n"
    "Сизнинг ишончли ҳамроҳингиз!\n"
    "Ҳозироқ манзилни танланг ва ҳайдовчи билан боғланинг.\n\n"
    "бот @husan7006 томонидан ишлаб чиқилди"
)

PROMPT_ROUTE    = "🧭 *Yo'nalishni tanlang.*"
PROMPT_PICKUP   = "🚏 *Qaysi hududdan sizni olib ketamiz?*"
PROMPT_DROP     = "🏁 *Qaysi hududga borasiz?*"
PROMPT_DISTRICTS = "— ҳудудни танланг!"

def kb_inline_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚖 БОШЛАШ", callback_data="go_start")]
    ])

def kb_request_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефонни улашиш", request_contact=True)],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

# Yo'nalish tugmalari (ikki variant)
ROUTE_QQ_TO_T = "Қўқон ➡️ Тошкент"
ROUTE_T_TO_QQ = "Тошкент ➡️ Қўқон"

def kb_routes() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ROUTE_QQ_TO_T)],
            [KeyboardButton(text=ROUTE_T_TO_QQ)],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

CITIES = ["Тошкент", "Қўқон"]

def kb_choice() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
        [KeyboardButton(text="4"), KeyboardButton(text="5+")],
        [KeyboardButton(text="📦 Почта бор")],
        [KeyboardButton(text=BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ============== VALIDATION HELPERS ==============
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
    return "почта" in (text or "").lower()

def people_to_int(s: str):
    allowed = {"1", "2", "3", "4", "5+"}
    if s not in allowed:
        return None
    return 5 if s.endswith("+") else int(s)

def is_page_indicator(txt: str) -> bool:
    return bool(re.fullmatch(r"\d+/\d+", (txt or "").strip()))

def norm_city(txt: str) -> str:
    t = (txt or "").strip().lower()
    variants = {
        "ташкент": "Тошкент", "тошкент": "Тошкент", "toshkent": "Тошкент",
        "қўқон": "Қўқон", "кўкон": "Қўқон", "qo‘qon": "Қўқон", "qo'qon": "Қўқон",
    }
    return variants.get(t, txt.strip())

# ============== DISTRICTS ==============
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
    "Абу сахий","Авиасозлар 22","Авиасозлар 4","Аерапорт","Ахмад","Ахмад олтин жужа",
    "Алгаритим","Алмалик","Амир Темур сквер","Ангрен","Ашхабод боғи","Бек барака",
    "Беруний Метро","Битонка","Болалар миллий тиббиёт","Буюк ипак йули метро","ВОДНИК",
    "Ғишт кўприк чегара","Ғофур Ғулом метро","Ғунча","Дўстлик метро","Еркин мост","Жангох",
    "Жарарик","Зангота Зиёратгоҳ","Жоме масжид","Ибн сино 1","Ипадром","Камолон",
    "Кардиалогия маркази","Кафе квартал","Кафедра... (йўқ экан)","Келес","Корасув",
    "Косманавтлар метро","Кока кола завод","Куйлюк 1","Куйлюк 2","Куйлюк 4","Куйлюк 5",
    "Куйлюк 6","Курувчи","Миробод Бозори","Миробод тумани","Мирзо Улугбек","Минор метро",
    "Минг урик","Маъруф ота масжиди","Машинасозлар метро","Межик ситий","Миллий боғ метро",
    "Мустақиллик майдони","Навоий куча","Некст маал","Олмазор","Олмалик","Охангарон",
    "Олой бозори","Олим полвон","Панелний","Паркент Бозори","Паркент тумани","Перевал",
    "Рохат","Сағбон","Себзор","Сергили","Сергили 6","Северный вогзал","Солношка",
    "Собир Рахимов","Тахтапул","Ташкент ситий","ТТЗ бозор","Фаргона йули","Фарход бозори",
    "Фууд ситий","Хадра майдони","Халқлар дўстлиги","Хайвонот боги","Хумо Арена","Чигатой",
    "Чилонзор","Чилонзор","Чирчиқ","Чорсу","Чупон ота","Шайхон Тохур","Шаршара",
    "Шота Руставили","Янги бозор","Янги йул","Янги Чош Тепа","Янги обод бозор",
    "Янгиобод бозори","Яланғоч","Яшинобод тумани","Яккасарoy","Ёшлик метро","Юнусобод",
    "Южный вогзал","Қафе квартал","Қушбеги","Қўйлиқ 5","Центр Бешкозон","Центрланый парк",
]

def districts_for_city(city: str) -> List[str]:
    return TOSHKENT_DISTRICTS if city == "Тошкент" else QOQON_DISTRICTS

def chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def kb_districts(city: str, page: int = 1, per_page: int = 8, cols: int = 2) -> ReplyKeyboardMarkup:
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

async def render_from_page(m: Message, state: FSMContext, delta: int = 0):
    data = await state.get_data()
    city = data.get("route_from")
    page = int(data.get("from_page", 1)) + delta
    total = max(1, (len(districts_for_city(city)) + 7) // 8)
    page = max(1, min(page, total))
    await state.update_data(from_page=page)
    await m.answer(f"{PROMPT_PICKUP}\n🏙 {city} {PROMPT_DISTRICTS}", reply_markup=kb_districts(city, page), parse_mode="Markdown")

async def render_to_page(m: Message, state: FSMContext, delta: int = 0):
    data = await state.get_data()
    city = data.get("route_to")
    page = int(data.get("to_page", 1)) + delta
    total = max(1, (len(districts_for_city(city)) + 7) // 8)
    page = max(1, min(page, total))
    await state.update_data(to_page=page)
    await m.answer(f"{PROMPT_DROP}\n🏙 {city} {PROMPT_DISTRICTS}", reply_markup=kb_districts(city, page), parse_mode="Markdown")

# ============== SAVE/NOTIFY ==============
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
    """
    Operator (gruppa) xabarida foydalanuvchi username va ID ko'rinmaydi.
    """
    if not ADMIN_CHAT_ID:
        return
    try:
        txt = (
            "🆕 *Янги буюртма*\n"
            f"📞 Телефон: {data.get('phone')}\n"
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

# ============== HANDLERS ==============
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()

    # foydalanuvchini DBga yozish/yangilash (stats uchun)
    try:
        now = int(time.time())
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute("""
                INSERT INTO users(tg_user_id, full_name, username, joined_at, last_seen)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(tg_user_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    username=excluded.username,
                    last_seen=excluded.last_seen
            """, (m.from_user.id, m.from_user.full_name, m.from_user.username, now, now))
    except Exception as e:
        log.exception("[DB] users upsert failed: %s", e)

    await m.answer(WELCOME_TEXT, reply_markup=kb_inline_start(), parse_mode="Markdown")

@dp.callback_query(F.data == "go_start")
async def cb_go_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(OrderForm.phone)
    await c.message.answer(
        "📱 Телефон рақамингизни юборинг.\nҚулайлик учун қуйидаги тугмадан фойдаланинг:",
        reply_markup=kb_request_phone()
    )
    await c.answer()

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Бекор қилинди. /start", reply_markup=ReplyKeyboardRemove())

# 1) Телефон
@dp.message(OrderForm.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext):
    ph = normalize_phone(m.contact.phone_number)
    if not is_valid_phone(ph):
        await m.answer("❗️ Телефон нотўғри. Қайта улашинг ёки қўлда ёзинг.", reply_markup=kb_request_phone())
        return
    await state.update_data(phone=ph)
    await m.answer(PROMPT_ROUTE, reply_markup=kb_routes(), parse_mode="Markdown")
    await state.set_state(OrderForm.route_from)

@dp.message(OrderForm.phone)
async def phone_from_text(m: Message, state: FSMContext):
    ph = normalize_phone(m.text)
    if not is_valid_phone(ph):
        await m.answer("❗️ Телефон нотўғри. +99890XXXXXXX кўринишида ёзинг ёки тугмадан фойдаланинг.",
                       reply_markup=kb_request_phone())
        return
    await state.update_data(phone=ph)
    await m.answer(PROMPT_ROUTE, reply_markup=kb_routes(), parse_mode="Markdown")
    await state.set_state(OrderForm.route_from)

# 2) Yo'nalish (juft tanlash)
@dp.message(OrderForm.route_from)
async def select_route_pair(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("📱 Телефон рақамингизни юборинг.", reply_markup=kb_request_phone())
        await state.set_state(OrderForm.phone)
        return

    if txt not in (ROUTE_QQ_TO_T, ROUTE_T_TO_QQ):
        await m.answer("❗️ Илтимос, рўйхатдан танланг.", reply_markup=kb_routes())
        return

    if txt == ROUTE_QQ_TO_T:
        from_city, to_city = "Қўқон", "Тошкент"
    else:
        from_city, to_city = "Тошкент", "Қўқон"

    await state.update_data(route_from=from_city, route_to=to_city, from_page=1, to_page=1)
    await render_from_page(m, state, delta=0)
    await state.set_state(OrderForm.from_district)

# 3) From District
@dp.message(OrderForm.from_district)
async def from_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer(PROMPT_ROUTE, reply_markup=kb_routes(), parse_mode="Markdown")
        await state.set_state(OrderForm.route_from)
        return
    if txt == NEXT:
        await render_from_page(m, state, delta=1)
        return
    if txt == PREV:
        await render_from_page(m, state, delta=-1)
        return
    if is_page_indicator(txt):
        return

    data = await state.get_data()
    city = data.get("route_from")
    if txt not in districts_for_city(city):
        await render_from_page(m, state, delta=0)
        return

    await state.update_data(from_district=txt)
    await render_to_page(m, state, delta=0)
    await state.set_state(OrderForm.to_district)

# 4) To District
@dp.message(OrderForm.to_district)
async def to_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await render_from_page(m, state, delta=0)
        await state.set_state(OrderForm.from_district)
        return
    if txt == NEXT:
        await render_to_page(m, state, delta=1)
        return
    if txt == PREV:
        await render_to_page(m, state, delta=-1)
        return
    if is_page_indicator(txt):
        return

    data = await state.get_data()
    city = data.get("route_to")
    if txt not in districts_for_city(city):
        await render_to_page(m, state, delta=0)
        return

    await state.update_data(to_district=txt)
    await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# 5) Choice
@dp.message(OrderForm.choice)
async def choice_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await render_to_page(m, state, delta=0)
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

# ============== PUBLIC /stats ==============
@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]

            # bugungi qo'shilganlar (ixtiyoriy)
            now = int(time.time())
            start_of_day = now - (now % 86400)
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (start_of_day,))
            today = cur.fetchone()[0]

        await m.answer(
            f"📊 Bot statistikasi:\n"
            f"👥 Umumiy foydalanuvchilar: {total} ta\n"
            f"🆕 Bugun qo‘shilganlar: {today} ta"
        )
    except Exception as e:
        log.exception("[STATS] failed: %s", e)
        await m.answer("❗️ Statistika vaqtincha mavjud emas.")

# (ixtiyoriy) alias
@dp.message(Command("users"))
async def cmd_users(m: Message):
    await cmd_stats(m)

# ============== RUN (Polling) ==============
async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
