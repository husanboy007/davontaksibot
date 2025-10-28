# main.py — DAVON EXPRESS TAXI (lokatsiyasiz, to‘liq)
import os
import re
import time
import sqlite3
import asyncio
import logging
from contextlib import closing
from typing import List, Optional

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, BotCommand,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ================= ENV & LOG =================
load_dotenv()
BOT_TOKEN       = os.getenv("BOT_TOKEN")
ADMIN_PHONE     = os.getenv("ADMIN_PHONE", "+998901234567")
ADMIN_CHAT_ID   = os.getenv("ADMIN_CHAT_ID")        # -100... ham bo'lishi mumkin
ADMIN_USER_ID   = os.getenv("ADMIN_USER_ID")        # bitta admin user id (ixtiyoriy)
AUTO_ANNOUNCE   = os.getenv("AUTO_ANNOUNCE", "0")   # "1" bo'lsa restartda e'lon yuboradi
ANNOUNCE_TEXT   = os.getenv("ANNOUNCE_TEXT", "Davon Express Taxi yangilandi!")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi! .env faylni to‘ldiring.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("davon-taksi-bot")

# ================= DB =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "orders.db")

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            tg_user_id INTEGER PRIMARY KEY,
            full_name  TEXT,
            username   TEXT,
            joined_at  INTEGER,
            last_seen  INTEGER,
            phone      TEXT,
            registered_at INTEGER
        );
        """)
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

# ================= BOT/DP =================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp  = Dispatcher(storage=MemoryStorage())

async def setup_commands():
    cmds = [
        BotCommand(command="start", description="Boshlash"),
        BotCommand(command="new",   description="Yangi buyurtma"),
        BotCommand(command="stats", description="Statistika"),
        BotCommand(command="help",  description="Yordam"),
    ]
    if ADMIN_USER_ID:
        cmds += [
            BotCommand(command="broadcast", description="(Admin) Hammaga matn"),
            BotCommand(command="announce",  description="(Admin) E’lon yuborish"),
        ]
    await bot.set_my_commands(cmds)

# ================= STATES =================
class OrderForm(StatesGroup):
    phone         = State()
    route_from    = State()
    from_district = State()
    to_district   = State()
    choice        = State()

# ================= TEXTS & KEYS =================
BACK = "🔙 Орқага"
NEXT = "➡️ Кейинги"
PREV = "⬅️ Олдинги"

WELCOME_TEXT = (
    "🚖 *DAVON EXPRESS TAXI*\n"
    "Сизнинг ишончли ҳамроҳингиз!\n"
    "Ҳозироқ манзилни танланг ва ҳайдовчи билан боғланинг.\n\n"
    "бот @husan7006 томонидан ишлаб чиқилди"
)
PROMPT_PHONE_FORCE = "📱 *Ro‘yxatdan o‘tish uchun telefon raqamingizni yuboring.*"
PROMPT_PHONE_CHOICE= "📱 Telefon tanlang:\n— *📞 Mening raqamim*\n— *👤 Boshqa odam uchun* (raqam yuborasiz)"
PROMPT_ROUTE       = "🧭 *Yo'nalishni tanlang.*"
PROMPT_PICKUP      = "🚏 *Qaysi hududdan sizni olib ketamiz?*"
PROMPT_DROP        = "🏁 *Qaysi hududga borasiz?*"
PROMPT_DISTRICTS   = "— ҳудудни танланг!"

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

def kb_phone_choice() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Mening raqamim")],
            [KeyboardButton(text="👤 Boshqa odam uchun"), KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True
    )

def kb_choice() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="1"), KeyboardButton(text="2"), KeyboardButton(text="3")],
        [KeyboardButton(text="4"), KeyboardButton(text="5+")],
        [KeyboardButton(text="📦 Почта бор")],
        [KeyboardButton(text=BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ================= VALIDATION =================
PHONE_RE = re.compile(r"^\+?\d{7,15}$")

def normalize_phone(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[()\-\s]", "", s)
    if s.startswith("00"): s = "+" + s[2:]
    if s.startswith("998") and len(s) == 12: s = "+" + s
    if not s.startswith("+") and s.isdigit(): s = "+" + s
    return s

def is_valid_phone(s: str) -> bool:
    return bool(PHONE_RE.match(s or ""))

def looks_like_cargo_only(text: str) -> bool:
    t = (text or "").lower()
    return ("почта" in t) or ("pochta" in t) or ("📦" in t)

def people_to_int(s: str) -> Optional[int]:
    if s == "5+": return 5
    return int(s) if s in {"1","2","3","4"} else None

def is_page_indicator(txt: str) -> bool:
    return bool(re.fullmatch(r"\d+/\d+", (txt or "").strip()))

# ================= CITY & DISTRICTS =================
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
    "Алгаритим","Абу сахий","Авиасозлар 22","Авиасозлар 4","Аерапорт","Ахмад","Ахмад олтин жужа",
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
    "Хайвонот боги","Хумо Арена","Чигатой","Чилонзор","Чирчиқ","Чорсу","Чупон ота","Шайхон Тохур",
    "Шаршара","Шота Руставили","Янги бозор","Янги йул","Янги Чош Тепа","Янги обод бозор","Янгиобод бозори",
    "Яланғоч","Яшинобод тумани","Яккасaroy","Ёшлик метро","Юнусобод","Южный вогзал","Қафе квартал",
    "Қушбеги","Қўйлиқ 5","Центр Бешкозон","Центрланый парк",
]
def districts_for_city(city: str) -> List[str]:
    return TOSHKENT_DISTRICTS if city == "Тошкент" else QOQON_DISTRICTS

def chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

# ================= USER HELPERS =================
def upsert_user_basic(m: Message):
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

def set_user_phone(user_id: int, phone: str):
    now = int(time.time())
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            "UPDATE users SET phone=?, registered_at=COALESCE(registered_at, ?) WHERE tg_user_id=?",
            (phone, now, user_id)
        )

def get_user_phone(user_id: int) -> Optional[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT phone FROM users WHERE tg_user_id=?", (user_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def all_user_ids() -> List[int]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT tg_user_id FROM users")
        return [r[0] for r in cur.fetchall()]

# ================= LAST ORDER HELPERS =================
def get_last_order(tg_user_id: int) -> Optional[dict]:
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT route_from, from_district, route_to, to_district
                FROM orders
                WHERE tg_user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (tg_user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"route_from": row[0], "from_district": row[1], "route_to": row[2], "to_district": row[3]}
    except Exception as e:
        log.exception("[DB] get_last_order failed: %s", e)
        return None

def last_district_for_city(city: str, last: Optional[dict]) -> Optional[str]:
    if not last: return None
    data = districts_for_city(city)
    if last.get("route_from") == city and last.get("from_district") in data:
        return last["from_district"]
    if last.get("route_to") == city and last.get("to_district") in data:
        return last["to_district"]
    return None

def extract_last_choice(txt: str) -> Optional[str]:
    pref = "⭐ Oxirgi: "
    return txt[len(pref):].strip() if (txt or "").startswith(pref) else None

# ================= KEYBOARDS (district lists) =================
def kb_districts(city: str, page: int = 1, per_page: int = 8,
                 cols: int = 2, last_district: Optional[str] = None) -> ReplyKeyboardMarkup:
    data = districts_for_city(city)
    total_pages = max(1, (len(data)+per_page-1)//per_page)
    page = max(1, min(page, total_pages))
    start = (page-1)*per_page
    items = data[start:start+per_page]

    rows: List[List[KeyboardButton]] = []
    if last_district and last_district in data:
        rows.append([KeyboardButton(text=f"⭐ Oxirgi: {last_district}")])
    for r in chunk(items, cols):
        rows.append([KeyboardButton(text=x) for x in r])
    nav = []
    if page>1: nav.append(KeyboardButton(text=PREV))
    nav.append(KeyboardButton(text=f"{page}/{total_pages}"))
    if page<total_pages: nav.append(KeyboardButton(text=NEXT))
    rows.append(nav)
    rows.append([KeyboardButton(text=BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ================= SAVE/NOTIFY/FINALIZE =================
async def save_order_safe(m: Message, data: dict):
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute("""
                INSERT INTO orders(tg_user_id, full_name, username, phone,
                                   route_from, from_district, route_to, to_district,
                                   people, cargo, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                m.from_user.id, m.from_user.full_name, m.from_user.username,
                data.get("phone"),
                data.get("route_from"), data.get("from_district"),
                data.get("route_to"), data.get("to_district"),
                int(data.get("people", 0)),
                data.get("cargo", "Йўқ"), data.get("note", "-"),
                int(time.time())
            ))
    except Exception as e:
        log.exception("[DB] Save failed: %s", e)

async def notify_operator_safe(m: Message, data: dict):
    if not ADMIN_CHAT_ID:
        return
    try:
        txt = (
            "🆕 *Янги буюртма*\n"
            f"👤 {m.from_user.full_name} (@{m.from_user.username or '-'}, ID:`{m.from_user.id}`)\n"
            f"📞 Телефон: {data.get('phone')}\n"
            f"🚖 Йўналиш: {data.get('route_from')} ({data.get('from_district')}) → "
            f"{data.get('route_to')} ({data.get('to_district')})\n"
            f"👥 Одам: {data.get('people')}\n"
            f"📦 Почта: {data.get('cargo','Йўқ')}\n"
            f"📝 Изоҳ: {data.get('note','-')}"
        )
        await bot.send_message(int(ADMIN_CHAT_ID), txt)
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
        f"👥 Одам: {data.get('people')}\n"
        f"📦 Почта: {data.get('cargo','Йўқ')}\n\n"
        f"🧑‍💼 Оператор рақами: {ADMIN_PHONE}\n"
        "Янги буюртма учун /start ни босинг."
    )
    await m.answer(confirm, reply_markup=ReplyKeyboardRemove())
    await state.clear()

# ================= RENDER HELPERS =================
async def render_from_page(m: Message, state: FSMContext, delta: int = 0):
    data = await state.get_data()
    city = data.get("route_from")
    page = int(data.get("from_page", 1)) + delta
    total = max(1, (len(districts_for_city(city)) + 7) // 8)
    page = max(1, min(page, total))
    await state.update_data(from_page=page)
    star = last_district_for_city(city, get_last_order(m.from_user.id))
    await m.answer(
        f"{PROMPT_PICKUP}\n🏙 {city} {PROMPT_DISTRICTS}",
        reply_markup=kb_districts(city, page, last_district=star)
    )

async def render_to_page(m: Message, state: FSMContext, delta: int = 0):
    data = await state.get_data()
    city = data.get("route_to")
    page = int(data.get("to_page", 1)) + delta
    total = max(1, (len(districts_for_city(city)) + 7) // 8)
    page = max(1, min(page, total))
    await state.update_data(to_page=page)
    star = last_district_for_city(city, get_last_order(m.from_user.id))
    await m.answer(
        f"{PROMPT_DROP}\n🏙 {city} {PROMPT_DISTRICTS}",
        reply_markup=kb_districts(city, page, last_district=star)
    )

# ================= HANDLERS =================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    upsert_user_basic(m)
    await m.answer(WELCOME_TEXT, reply_markup=kb_inline_start())

@dp.callback_query(F.data == "go_start")
async def cb_go_start(c: CallbackQuery, state: FSMContext):
    phone = get_user_phone(c.from_user.id)
    if phone:
        await c.message.answer(PROMPT_PHONE_CHOICE, reply_markup=kb_phone_choice())
    else:
        await state.set_state(OrderForm.phone)
        await c.message.answer(PROMPT_PHONE_FORCE, reply_markup=kb_request_phone())
    await c.answer()

@dp.message(Command("new"))
async def cmd_new(m: Message, state: FSMContext):
    await state.clear()
    phone = get_user_phone(m.from_user.id)
    if phone:
        await m.answer(PROMPT_PHONE_CHOICE, reply_markup=kb_phone_choice())
    else:
        await state.set_state(OrderForm.phone)
        await m.answer(PROMPT_PHONE_FORCE, reply_markup=kb_request_phone())

@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Бекор қилинди. /start", reply_markup=ReplyKeyboardRemove())

# --- phone choice ---
@dp.message(F.text == "📞 Mening raqamim")
async def use_my_phone(m: Message, state: FSMContext):
    phone = get_user_phone(m.from_user.id)
    if not phone:
        await state.set_state(OrderForm.phone)
        await m.answer(PROMPT_PHONE_FORCE, reply_markup=kb_request_phone()); return
    await state.update_data(phone=phone)
    await m.answer(PROMPT_ROUTE, reply_markup=kb_routes())
    await state.set_state(OrderForm.route_from)

@dp.message(F.text == "👤 Boshqa odam uchun")
async def other_person_phone(m: Message, state: FSMContext):
    await state.set_state(OrderForm.phone)
    await m.answer("📱 Boshqa odamning telefonini yuboring yoki tugmadan foydalaning:",
                   reply_markup=kb_request_phone())

# --- phone collection ---
@dp.message(OrderForm.phone, F.text == BACK)
async def phone_back_to_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("↩️ Menyu: /start yoki /new", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderForm.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext):
    ph = normalize_phone(m.contact.phone_number)
    if not is_valid_phone(ph):
        await m.answer("❗️ Telefon noto‘g‘ri. Qayta yuboring yoki qo‘lda yozing.",
                       reply_markup=kb_request_phone()); return
    set_user_phone(m.from_user.id, ph)
    await state.update_data(phone=ph)
    await m.answer(PROMPT_ROUTE, reply_markup=kb_routes())
    await state.set_state(OrderForm.route_from)

@dp.message(OrderForm.phone)
async def phone_from_text(m: Message, state: FSMContext):
    ph = normalize_phone(m.text)
    if not is_valid_phone(ph):
        await m.answer(
            "❗️ Telefon noto‘g‘ri. +99890XXXXXXX ko‘rinishida yozing yoki tugmadan foydalaning.",
            reply_markup=kb_request_phone()
        ); return
    set_user_phone(m.from_user.id, ph)
    await state.update_data(phone=ph)
    await m.answer(PROMPT_ROUTE, reply_markup=kb_routes())
    await state.set_state(OrderForm.route_from)

# --- route pair ---
@dp.message(OrderForm.route_from)
async def select_route_pair(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await m.answer("↩️ Menyu: /start yoki /new", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    if txt not in (ROUTE_QQ_TO_T, ROUTE_T_TO_QQ):
        await m.answer("❗️ Ro‘yxatdan tanlang.", reply_markup=kb_routes()); return

    if txt == ROUTE_QQ_TO_T:
        from_city, to_city = "Қўқон", "Тошкент"
    else:
        from_city, to_city = "Тошкент", "Қўқон"

    await state.update_data(
        route_from=from_city, route_to=to_city,
        from_page=1, to_page=1,
        from_district=None, to_district=None,
        cargo="Йўқ", note="-"
    )
    await render_from_page(m, state, delta=0)
    await state.set_state(OrderForm.from_district)

# --- FROM district ---
@dp.message(OrderForm.from_district)
async def from_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await m.answer(PROMPT_ROUTE, reply_markup=kb_routes())
        await state.set_state(OrderForm.route_from); return
    if txt == NEXT: await render_from_page(m, state, delta=1); return
    if txt == PREV: await render_from_page(m, state, delta=-1); return
    if is_page_indicator(txt): return

    data = await state.get_data()
    city = data.get("route_from")
    pick = extract_last_choice(txt) or txt
    if pick not in districts_for_city(city):
        await render_from_page(m, state, 0); return

    await state.update_data(from_district=pick)
    await render_to_page(m, state, delta=0)
    await state.set_state(OrderForm.to_district)

# --- TO district ---
@dp.message(OrderForm.to_district)
async def to_district_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()

    if txt == BACK:
        await render_from_page(m, state, delta=0)
        await state.set_state(OrderForm.from_district); return
    if txt == NEXT: await render_to_page(m, state, delta=1); return
    if txt == PREV: await render_to_page(m, state, delta=-1); return
    if is_page_indicator(txt): return

    data = await state.get_data()
    city = data.get("route_to")
    pick = extract_last_choice(txt) or txt
    if pick not in districts_for_city(city):
        await render_to_page(m, state, 0); return

    await state.update_data(to_district=pick)
    await m.answer("👥 Одам сонини танланг ёки «📦 Почта бор» ни босинг:", reply_markup=kb_choice())
    await state.set_state(OrderForm.choice)

# --- people / cargo ---
@dp.message(OrderForm.choice)
async def choice_step(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt == BACK:
        await render_to_page(m, state, delta=0); await state.set_state(OrderForm.to_district); return

    if looks_like_cargo_only(txt):
        await state.update_data(people=0, cargo="Бор"); await finalize(m, state); return

    p = people_to_int(txt)
    if p is None:
        await m.answer("❗️ 1,2,3,4,5+ ёки «📦 Почта бор».", reply_markup=kb_choice()); return

    await state.update_data(people=p)
    await finalize(m, state)

# ================= PUBLIC COMMANDS =================
@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            now = int(time.time()); start_of_day = now - (now % 86400)
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (start_of_day,))
            today = cur.fetchone()[0]
        await m.answer(f"📊 Bot статистикаси:\n👥 Umumiy: {total} ta\n🆕 Bugun: {today} ta")
    except Exception as e:
        log.exception("[STATS] failed: %s", e)
        await m.answer("❗️ Statistika vaqtincha mavjud emas.")

@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer("Buyruqlar: /start, /new, /stats, /cancel")

# ================= ADMIN =================
def _is_admin(uid: int) -> bool:
    try:
        return bool(ADMIN_USER_ID) and int(ADMIN_USER_ID) == int(uid)
    except Exception:
        return False

@dp.message(Command("broadcast"))
async def cmd_broadcast(m: Message):
    if not _is_admin(m.from_user.id): return
    text = m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("Foydalanish: `/broadcast matn`", parse_mode=ParseMode.MARKDOWN); return
    sent = fail = 0
    for uid in all_user_ids():
        try:
            await bot.send_message(uid, text); sent += 1
        except Exception:
            fail += 1
    await m.answer(f"Yuborildi: {sent}  |  Xato: {fail}")

@dp.message(Command("announce"))
async def cmd_announce(m: Message):
    if not _is_admin(m.from_user.id): return
    text = ANNOUNCE_TEXT
    sent = fail = 0
    for uid in all_user_ids():
        try:
            await bot.send_message(uid, text); sent += 1
        except Exception:
            fail += 1
    await m.answer(f"ANNOUNCE yuborildi: {sent}  |  Xato: {fail}")

# ================= RUN =================
async def main():
    # Webhook ↔ Polling konflikti bo‘lmasligi uchun webhookni o‘chirib qo‘yamiz:
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    await setup_commands()

    if AUTO_ANNOUNCE == "1":
        try:
            for uid in all_user_ids():
                try:
                    await bot.send_message(uid, ANNOUNCE_TEXT)
                except Exception:
                    pass
        except Exception as e:
            log.exception("[AUTO_ANNOUNCE] failed: %s", e)

    # Faqat polling ishlatiladi
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
