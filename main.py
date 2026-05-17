import asyncio
import logging
import random
import calendar
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite
import google.generativeai as genai

# ---------- НАСТРОЙКИ ----------
TOKEN = "8354719198:AAG3ZYYPnsYpoG_sRCYsBSJiApxr_VrsPAU"
ADMIN_ID = 8291571909
GEMINI_API_KEY = "AIzaSyCggG7gK1EsTaOf4mh9Hc4iE7_tg2CwjiA"
DB_NAME = "bot_schedule.db"

MSK = timezone(timedelta(hours=3))

# Инициализация Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
logging.basicConfig(level=logging.INFO)

EMOJI_POOL = [
    "😀", "😎", "🥳", "🤖", "👻", "🐱", "🦊", "🐼", "🐨", "🐸",
    "🍎", "🍌", "🍒", "🍓", "🥑", "🍕", "🍔", "🌮", "🍩", "🍪",
    "⚽", "🏀", "🎱", "🎮", "🎲", "🎸", "🎺", "🎻", "🎯", "🧩"
]

# ---------- БАЗА ДАННЫХ ----------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                date TEXT,
                start_time TEXT,
                end_time TEXT,
                subject TEXT,
                teacher TEXT,
                link TEXT,
                notified INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                subject TEXT,
                keywords TEXT,
                response TEXT,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграция для колонки notified
        cursor = await db.execute("PRAGMA table_info(schedule)")
        columns = [col[1] for col in await cursor.fetchall()]
        if "notified" not in columns:
            await db.execute("ALTER TABLE schedule ADD COLUMN notified INTEGER DEFAULT 0")
        await db.commit()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
async def add_group(chat_id: int, title: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO groups (chat_id, title) VALUES (?, ?)",
            (chat_id, title)
        )
        await db.commit()

async def get_groups():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chat_id, title FROM groups")
        return await cursor.fetchall()

async def add_chat_member(chat_id: int, user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chat_members (chat_id, user_id, username, first_name) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, username, first_name)
        )
        await db.commit()

async def get_chat_members(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id, username, first_name FROM chat_members WHERE chat_id = ?",
            (chat_id,)
        )
        return await cursor.fetchall()

async def add_schedule(chat_id: int, date: str, start: str, end: str, subject: str, teacher: str, link: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO schedule (chat_id, date, start_time, end_time, subject, teacher, link) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, date, start, end, subject, teacher, link)
        )
        await db.commit()

async def get_schedule(chat_id: int, date: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        if date:
            cursor = await db.execute(
                "SELECT * FROM schedule WHERE chat_id = ? AND date = ? ORDER BY start_time",
                (chat_id, date)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM schedule WHERE chat_id = ? ORDER BY date, start_time",
                (chat_id,)
            )
        return await cursor.fetchall()

async def get_occupied_dates(chat_id: int, year: int, month: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT DISTINCT date FROM schedule WHERE chat_id = ? AND date LIKE ?",
            (chat_id, f"{year}-{month:02d}-%")
        )
        rows = await cursor.fetchall()
        return {row[0] for row in rows}

async def delete_schedule(chat_id: int, date: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        if date:
            await db.execute("DELETE FROM schedule WHERE chat_id = ? AND date = ?", (chat_id, date))
        else:
            await db.execute("DELETE FROM schedule WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def delete_lesson(lesson_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule WHERE id = ?", (lesson_id,))
        await db.commit()

# ---------- БАЗА ЗНАНИЙ ----------
async def add_knowledge(chat_id: int, subject: str, keywords: str, response: str, added_by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO knowledge_base (chat_id, subject, keywords, response, added_by) VALUES (?, ?, ?, ?, ?)",
            (chat_id, subject, keywords, response, added_by)
        )
        await db.commit()

async def get_all_knowledge(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT id, subject, keywords, response FROM knowledge_base WHERE chat_id = ?",
            (chat_id,)
        )
        return await cursor.fetchall()

async def find_knowledge_with_ai(chat_id: int, user_question: str):
    """ИИ определяет, какой записи из базы знаний соответствует вопрос."""
    entries = await get_all_knowledge(chat_id)
    if not entries:
        return None

    # Строим описание для промпта
    items_desc = []
    for eid, subject, keywords, response in entries:
        kw = keywords if keywords else subject
        items_desc.append(f"ID:{eid} | Предмет: {subject} | Ключевые фразы: {kw}")

    prompt = (
        "Ты — помощник для поиска учебной информации. Ниже перечислены доступные записи в базе знаний. "
        "Пользователь задал вопрос. Верни ТОЛЬКО ID записи, которая лучше всего подходит для ответа, "
        "и число от 0 до 100 — твоя уверенность (0 — совсем не подходит, 100 — абсолютно точно). "
        "Формат ответа строго: ID:число,Уверенность:число (без пробелов, например: ID:3,Уверенность:85). "
        "Если ни одна запись не подходит, верни ID:0,Уверенность:0.\n\n"
        f"Записи:\n{chr(10).join(items_desc)}\n\n"
        f"Вопрос: {user_question}"
    )

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Парсим ответ ИИ
        if text.startswith("ID:") and ",Уверенность:" in text:
            parts = text.split(",")
            id_part = parts[0].split(":")[1]
            conf_part = parts[1].split(":")[1]
            entry_id = int(id_part)
            confidence = int(conf_part)
            if entry_id > 0 and confidence >= 70:
                # Ищем ответ в локальной базе
                for eid, subject, keywords, resp in entries:
                    if eid == entry_id:
                        return resp
        return None
    except Exception as e:
        logging.error(f"AI search error: {e}")
        return None

async def delete_knowledge(knowledge_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM knowledge_base WHERE id = ?", (knowledge_id,))
        await db.commit()

# ---------- СОСТОЯНИЯ FSM ----------
class AddSchedule(StatesGroup):
    choosing_group = State()
    choosing_year = State()
    choosing_month = State()
    choosing_day = State()
    entering_start_time = State()
    entering_end_time = State()
    entering_subject = State()
    entering_teacher = State()
    entering_link = State()

class ViewCalendar(StatesGroup):
    choosing_group = State()
    viewing = State()

class DeleteDate(StatesGroup):
    choosing_group = State()
    entering_date = State()

class DeleteAll(StatesGroup):
    choosing_group = State()

class AddKnowledge(StatesGroup):
    choosing_group = State()
    entering_subject = State()
    entering_keywords = State()
    entering_response = State()

class ViewKnowledge(StatesGroup):
    choosing_group = State()

# ---------- КЛАВИАТУРЫ ----------
def admin_main_keyboard():
    kb = [
        [
            InlineKeyboardButton(text="Добавить", callback_data="add_schedule"),
            InlineKeyboardButton(text="Расписание", callback_data="view_schedule")
        ],
        [InlineKeyboardButton(text="Очистить занятие по дате", callback_data="del_date")],
        [InlineKeyboardButton(text="Очистить всё расписание", callback_data="del_all")],
        [InlineKeyboardButton(text="📚 База знаний", callback_data="knowledge_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def knowledge_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="➕ Добавить запись", callback_data="add_knowledge")],
        [InlineKeyboardButton(text="📋 Просмотреть записи", callback_data="view_knowledge")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def groups_keyboard(groups):
    kb = []
    for chat_id, title in groups:
        short = title if len(title) < 30 else title[:27] + "..."
        kb.append([InlineKeyboardButton(text=short, callback_data=f"group_{chat_id}")])
    kb.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def year_keyboard():
    now = datetime.now(MSK)
    kb = [
        [
            InlineKeyboardButton(text=str(now.year), callback_data=f"year_{now.year}"),
            InlineKeyboardButton(text=str(now.year + 1), callback_data=f"year_{now.year + 1}")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def month_keyboard():
    kb = []
    row = []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(text=str(m).zfill(2), callback_data=f"month_{m}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def day_keyboard(year: int, month: int):
    days = calendar.monthrange(year, month)[1]
    kb = []
    row = []
    for d in range(1, days + 1):
        row.append(InlineKeyboardButton(text=str(d).zfill(2), callback_data=f"day_{d}"))
        if len(row) == 7:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def calendar_keyboard(year: int, month: int, occupied_dates: set):
    kb = []
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    nav_row = [
        InlineKeyboardButton(text="◀️", callback_data=f"cal_nav_{prev_year}_{prev_month}"),
        InlineKeyboardButton(text=f"{calendar.month_name[month]} {year}", callback_data="ignore"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal_nav_{next_year}_{next_month}"),
    ]
    kb.append(nav_row)

    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    kb.append([InlineKeyboardButton(text=w, callback_data="ignore") for w in weekdays])

    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                marker = "🔴" if date_str in occupied_dates else "🟢"
                row.append(InlineKeyboardButton(
                    text=f"{marker} {day}",
                    callback_data=f"cal_day_{year}_{month:02d}_{day:02d}"
                ))
        kb.append(row)

    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def schedule_day_keyboard(date_str: str, lessons: list):
    kb = []
    for lesson in lessons:
        lesson_id = lesson[0]
        subject = lesson[5]
        start = lesson[3]
        end = lesson[4]
        btn = InlineKeyboardButton(
            text=f"❌ {start}-{end} {subject}",
            callback_data=f"delete_lesson_{lesson_id}"
        )
        kb.append([btn])
    kb.append([InlineKeyboardButton(text="🔙 Назад к календарю", callback_data=f"cal_nav_{date_str[:4]}_{int(date_str[5:7])}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ---------- АВТОТРЕКИНГ ГРУПП И УЧАСТНИКОВ ----------
@router.message(F.chat.type.in_({"group", "supergroup"}), ~F.text.startswith("/call"))
async def track_chat_and_member(message: types.Message):
    await add_group(message.chat.id, message.chat.title or "Без названия")
    await add_chat_member(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

# ---------- /call В ГРУППАХ (только для админов) ----------
@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.startswith("/call "))
async def cmd_call(message: types.Message):
    chat_id = message.chat.id
    text = message.text[6:].strip()

    user_id = message.from_user.id
    member = await bot.get_chat_member(chat_id, user_id)
    if member.status not in ('administrator', 'creator'):
        await message.reply("❌ Только администраторы могут использовать /call.")
        return

    members = await get_chat_members(chat_id)
    if not members:
        await message.reply("❌ Нет данных об участниках. Пусть кто-нибудь напишет в чат, чтобы бот их запомнил.")
        return

    try:
        await message.delete()
    except:
        pass

    emojis = random.sample(EMOJI_POOL, min(len(members), len(EMOJI_POOL)))
    if len(members) > len(EMOJI_POOL):
        emojis.extend(random.choices(EMOJI_POOL, k=len(members) - len(EMOJI_POOL)))

    mention_parts = []
    for (uid, uname, fname), emoji in zip(members, emojis):
        mention_parts.append(f'<a href="tg://user?id={uid}">{emoji}</a>')

    await message.answer(f"{text}\n\n" + " ".join(mention_parts), parse_mode="HTML")

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text == "/call")
async def cmd_call_no_text(message: types.Message):
    await message.reply("Для использования: /call ваш текст")

# ---------- АДМИНКА ----------
@router.message(F.chat.type == "private", F.from_user.id == ADMIN_ID, Command("start"))
async def admin_start(message: types.Message):
    await message.answer("👋 Привет, админ! Выбери действие:", reply_markup=admin_main_keyboard())

@router.callback_query(F.data == "cancel", F.from_user.id == ADMIN_ID)
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state():
        await state.clear()
    await callback.message.edit_text("❌ Отменено.", reply_markup=admin_main_keyboard())
    await callback.answer()

# ========== ДОБАВЛЕНИЕ ЗАНЯТИЯ ==========
# (весь блок остаётся без изменений)
@router.callback_query(F.data == "add_schedule", F.from_user.id == ADMIN_ID)
async def add_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет доступных групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(AddSchedule.choosing_group)
    await callback.message.edit_text("Выберите группу:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(AddSchedule.choosing_group, F.data.startswith("group_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    await state.update_data(chat_id=chat_id)
    await state.set_state(AddSchedule.choosing_year)
    await callback.message.edit_text("Выберите год:", reply_markup=year_keyboard())
    await callback.answer()

@router.callback_query(AddSchedule.choosing_year, F.data.startswith("year_"))
async def year_chosen(callback: types.CallbackQuery, state: FSMContext):
    year = int(callback.data.split("_")[1])
    await state.update_data(year=year)
    await state.set_state(AddSchedule.choosing_month)
    await callback.message.edit_text("Выберите месяц:", reply_markup=month_keyboard())
    await callback.answer()

@router.callback_query(AddSchedule.choosing_month, F.data.startswith("month_"))
async def month_chosen(callback: types.CallbackQuery, state: FSMContext):
    month = int(callback.data.split("_")[1])
    data = await state.get_data()
    year = data['year']
    await state.update_data(month=month)
    await state.set_state(AddSchedule.choosing_day)
    await callback.message.edit_text("Выберите число:", reply_markup=day_keyboard(year, month))
    await callback.answer()

@router.callback_query(AddSchedule.choosing_day, F.data.startswith("day_"))
async def day_chosen(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.split("_")[1])
    data = await state.get_data()
    year = data['year']
    month = data['month']
    date_str = f"{year}-{month:02d}-{day:02d}"
    await state.update_data(date=date_str)
    await state.set_state(AddSchedule.entering_start_time)
    await callback.message.edit_text("⌚ Введите время начала занятия (МСК) в формате ЧЧ:ММ (например, 10:00):")
    await callback.answer()

@router.message(AddSchedule.entering_start_time, F.text.regexp(r'^\d{2}:\d{2}$'))
async def start_time_entered(message: types.Message, state: FSMContext):
    await state.update_data(start_time=message.text)
    await state.set_state(AddSchedule.entering_end_time)
    await message.answer("⌛ Введите время окончания занятия (МСК) в формате ЧЧ:ММ:")

@router.message(AddSchedule.entering_end_time, F.text.regexp(r'^\d{2}:\d{2}$'))
async def end_time_entered(message: types.Message, state: FSMContext):
    await state.update_data(end_time=message.text)
    await state.set_state(AddSchedule.entering_subject)
    await message.answer("📖 Введите название предмета:")

@router.message(AddSchedule.entering_subject)
async def subject_entered(message: types.Message, state: FSMContext):
    await state.update_data(subject=message.text)
    await state.set_state(AddSchedule.entering_teacher)
    await message.answer("👨‍🏫 Введите ФИО преподавателя:")

@router.message(AddSchedule.entering_teacher)
async def teacher_entered(message: types.Message, state: FSMContext):
    await state.update_data(teacher=message.text)
    await state.set_state(AddSchedule.entering_link)
    await message.answer("🔗 Введите ссылку на онлайн-созвон:")

@router.message(AddSchedule.entering_link)
async def link_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_schedule(
        chat_id=data['chat_id'],
        date=data['date'],
        start=data['start_time'],
        end=data['end_time'],
        subject=data['subject'],
        teacher=data['teacher'],
        link=message.text
    )
    await message.answer("✅ Занятие добавлено!", reply_markup=admin_main_keyboard())
    await state.clear()

# ========== КАЛЕНДАРЬ РАСПИСАНИЯ ==========
@router.callback_query(F.data == "view_schedule", F.from_user.id == ADMIN_ID)
async def view_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(ViewCalendar.choosing_group)
    await callback.message.edit_text("Выберите группу:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(ViewCalendar.choosing_group, F.data.startswith("group_"))
async def view_group(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    now = datetime.now(MSK)
    await state.update_data(chat_id=chat_id, year=now.year, month=now.month)
    await state.set_state(ViewCalendar.viewing)
    await show_calendar(callback.message, chat_id, now.year, now.month)
    await callback.answer()

async def show_calendar(message: types.Message, chat_id: int, year: int, month: int):
    occupied = await get_occupied_dates(chat_id, year, month)
    kb = calendar_keyboard(year, month, occupied)
    await message.edit_text("📅 Выберите день:", reply_markup=kb)

@router.callback_query(ViewCalendar.viewing, F.data.startswith("cal_nav_"))
async def cal_nav(callback: types.CallbackQuery, state: FSMContext):
    _, _, y, m = callback.data.split("_")
    year, month = int(y), int(m)
    await state.update_data(year=year, month=month)
    data = await state.get_data()
    await show_calendar(callback.message, data['chat_id'], year, month)
    await callback.answer()

@router.callback_query(ViewCalendar.viewing, F.data.startswith("cal_day_"))
async def cal_day(callback: types.CallbackQuery, state: FSMContext):
    _, _, y, m, d = callback.data.split("_")
    date_str = f"{y}-{m}-{d}"
    data = await state.get_data()
    chat_id = data['chat_id']
    rows = await get_schedule(chat_id, date=date_str)

    if rows:
        text = f"📋 Занятия на {date_str}:\n\n"
        for row in rows:
            text += f"🕒 {row[3]} – {row[4]}\n📖 {row[5]}\n👨‍🏫 {row[6]}\n🔗 {row[7]}\n\n"
        kb = schedule_day_keyboard(date_str, rows)
    else:
        text = f"📭 На {date_str} занятий нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к календарю", callback_data=f"cal_nav_{y}_{int(m)}")]
        ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

# Удаление одного урока
@router.callback_query(lambda c: c.data and c.data.startswith("delete_lesson_"))
async def delete_lesson_handler(callback: types.CallbackQuery):
    lesson_id = int(callback.data.split("_")[2])
    await delete_lesson(lesson_id)
    # Перестраиваем сообщение
    text = callback.message.text
    date_str = None
    for line in text.split('\n'):
        if line.startswith("📋 Занятия на "):
            date_str = line[14:24].strip()
            break
    if not date_str:
        await callback.answer("Не удалось определить дату.")
        return
    # Получаем chat_id из удалённого урока
    chat_id = None
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chat_id FROM schedule WHERE id = ?", (lesson_id,))
        row = await cursor.fetchone()
        if row:
            chat_id = row[0]
    if not chat_id:
        await callback.answer("Ошибка: группа не найдена.")
        return

    rows = await get_schedule(chat_id, date=date_str)
    if rows:
        new_text = f"📋 Занятия на {date_str}:\n\n"
        for row in rows:
            new_text += f"🕒 {row[3]} – {row[4]}\n📖 {row[5]}\n👨‍🏫 {row[6]}\n🔗 {row[7]}\n\n"
        kb = schedule_day_keyboard(date_str, rows)
    else:
        new_text = f"📭 На {date_str} занятий нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к календарю", callback_data=f"cal_nav_{date_str[:4]}_{int(date_str[5:7])}")]
        ])
    await callback.message.edit_text(new_text, reply_markup=kb)
    await callback.answer("Занятие удалено.")

@router.callback_query(ViewCalendar.viewing, F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("👋 Главное меню:", reply_markup=admin_main_keyboard())
    await callback.answer()

# ========== УДАЛЕНИЕ ПО ДАТЕ ==========
@router.callback_query(F.data == "del_date", F.from_user.id == ADMIN_ID)
async def del_date_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(DeleteDate.choosing_group)
    await callback.message.edit_text("Выберите группу:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(DeleteDate.choosing_group, F.data.startswith("group_"))
async def del_date_group(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    await state.update_data(chat_id=chat_id)
    await state.set_state(DeleteDate.entering_date)
    await callback.message.edit_text("Введите дату (ГГГГ-ММ-ДД):")
    await callback.answer()

@router.message(DeleteDate.entering_date, F.text.regexp(r'^\d{4}-\d{2}-\d{2}$'))
async def del_date_execute(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await delete_schedule(data['chat_id'], date=message.text)
    await message.answer(f"🗑 Занятия на {message.text} удалены.", reply_markup=admin_main_keyboard())
    await state.clear()

# ========== ПОЛНАЯ ОЧИСТКА ==========
@router.callback_query(F.data == "del_all", F.from_user.id == ADMIN_ID)
async def del_all_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(DeleteAll.choosing_group)
    await callback.message.edit_text("Выберите группу:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(DeleteAll.choosing_group, F.data.startswith("group_"))
async def del_all_group(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    await delete_schedule(chat_id)
    await callback.message.edit_text("✅ Расписание полностью удалено.", reply_markup=admin_main_keyboard())
    await state.clear()
    await callback.answer()

# ========== БАЗА ЗНАНИЙ (АДМИНКА) ==========
@router.callback_query(F.data == "knowledge_menu", F.from_user.id == ADMIN_ID)
async def knowledge_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("📚 База знаний:", reply_markup=knowledge_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "add_knowledge", F.from_user.id == ADMIN_ID)
async def add_knowledge_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(AddKnowledge.choosing_group)
    await callback.message.edit_text("Выберите группу, для которой добавляется правило:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(AddKnowledge.choosing_group, F.data.startswith("group_"))
async def knowledge_group_chosen(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    await state.update_data(chat_id=chat_id)
    await state.set_state(AddKnowledge.entering_subject)
    await callback.message.edit_text("Введите название предмета (например: Английский):")
    await callback.answer()

@router.message(AddKnowledge.entering_subject)
async def knowledge_subject_entered(message: types.Message, state: FSMContext):
    await state.update_data(subject=message.text)
    await state.set_state(AddKnowledge.entering_keywords)
    await message.answer("Введите ключевые слова через запятую (по ним студенты будут находить это правило):")

@router.message(AddKnowledge.entering_keywords)
async def knowledge_keywords_entered(message: types.Message, state: FSMContext):
    await state.update_data(keywords=message.text)
    await state.set_state(AddKnowledge.entering_response)
    await message.answer("Введите текст, который бот будет отправлять в ответ:")

@router.message(AddKnowledge.entering_response)
async def knowledge_response_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_knowledge(
        chat_id=data['chat_id'],
        subject=data['subject'],
        keywords=data['keywords'],
        response=message.text,
        added_by=message.from_user.id
    )
    await message.answer("✅ Правило добавлено в базу знаний!", reply_markup=admin_main_keyboard())
    await state.clear()

@router.callback_query(F.data == "view_knowledge", F.from_user.id == ADMIN_ID)
async def view_knowledge_start(callback: types.CallbackQuery, state: FSMContext):
    groups = await get_groups()
    if not groups:
        await callback.message.edit_text("Нет групп.", reply_markup=admin_main_keyboard())
        await callback.answer()
        return
    await state.set_state(ViewKnowledge.choosing_group)
    await callback.message.edit_text("Выберите группу для просмотра базы знаний:", reply_markup=groups_keyboard(groups))
    await callback.answer()

@router.callback_query(ViewKnowledge.choosing_group, F.data.startswith("group_"))
async def view_knowledge_group(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[1])
    await state.clear()
    entries = await get_all_knowledge(chat_id)
    if not entries:
        text = "📭 В базе знаний этой группы пока нет записей."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="knowledge_menu")]
        ])
    else:
        text = "📋 Записи в базе знаний:\n\n"
        kb = []
        for entry in entries:
            eid, subject, keywords, response = entry
            text += f"📌 {subject} | Ключ.слова: {keywords}\nОтвет: {response}\n\n"
            kb.append([InlineKeyboardButton(text=f"🗑 Удалить: {subject}", callback_data=f"del_knowledge_{eid}")])
        kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="knowledge_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("del_knowledge_"))
async def del_knowledge_handler(callback: types.CallbackQuery):
    knowledge_id = int(callback.data.split("_")[2])
    await delete_knowledge(knowledge_id)
    await callback.message.edit_text("🗑 Запись удалена.", reply_markup=knowledge_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text("👋 Главное меню:", reply_markup=admin_main_keyboard())
    await callback.answer()

# ---------- ОБРАБОТКА ВОПРОСОВ СТУДЕНТОВ С ИИ ----------
@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def smart_answer(message: types.Message):
    chat_id = message.chat.id
    text = message.text.strip()

    # Игнорируем команды и слишком короткие сообщения
    if len(text) < 5 or text.startswith("/"):
        return

    answer = await find_knowledge_with_ai(chat_id, text)
    if answer:
        await message.reply(answer, disable_web_page_preview=True)

# ---------- ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ ----------
async def scheduler():
    while True:
        await asyncio.sleep(30)
        now = datetime.now(MSK)
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(
                """SELECT id, chat_id, date, start_time, end_time, subject, teacher, link
                   FROM schedule
                   WHERE date = ? AND start_time <= ? AND end_time >= ? AND notified = 0""",
                (today, current_time, current_time)
            )
            rows = await cursor.fetchall()
            for row in rows:
                lesson_id, chat_id, date, start, end, subject, teacher, link = row
                text = (
                    "🔔 Напоминание! Необходимо подключиться\n\n"
                    f"Предмет: {subject}\n"
                    f"Преподаватель: {teacher}\n\n"
                    f"⌛️ Продолжительность {start} – {end} МСК"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Подключиться", url=link)]
                ])
                try:
                    await bot.send_message(chat_id, text, reply_markup=kb)
                    await db.execute("UPDATE schedule SET notified = 1 WHERE id = ?", (lesson_id,))
                    await db.commit()
                    logging.info(f"Уведомление: {subject} -> {chat_id}")
                except Exception as e:
                    logging.error(f"Ошибка отправки в {chat_id}: {e}")

# ---------- ЗАПУСК ----------
async def main():
    await init_db()
    asyncio.create_task(scheduler())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())