import sqlite3
import asyncio
from datetime import datetime, date, timedelta
from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_storage import StateMemoryStorage
from telebot.asyncio_filters import AdvancedCustomFilter
from telebot.callback_data import CallbackData
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telebot.asyncio_handler_backends import State, StatesGroup
import logging
import ssl
from aiohttp import ClientSession, TCPConnector, ClientTimeout
from telebot import asyncio_helper

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_training_sessions():
    try:
        # Обновляем все записи без даты
        cursor.execute('''
        UPDATE training_sessions 
        SET date = CURRENT_DATE 
        WHERE date IS NULL
        ''')

        # Получаем количество обновленных записей
        updated_count = cursor.rowcount

        # Устанавливаем дефолтное значение для новых записей
        cursor.execute('''
        ALTER TABLE training_sessions 
        ALTER COLUMN date SET DEFAULT CURRENT_DATE
        ''')

        conn.commit()
        return updated_count
    except sqlite3.Error as e:
        logger.error(f"Ошибка при исправлении записей: {e}")
        conn.rollback()
        return 0

# Инициализация бота
API_TOKEN = '8141145566:AAGfUGgkp-pyWYlL_sJTx3gWXt-HydT52wY'
storage = StateMemoryStorage()
bot = AsyncTeleBot(API_TOKEN, state_storage=storage)

# Подключение к базе данных SQLite
conn = sqlite3.connect('training_bot.db', check_same_thread=False)
cursor = conn.cursor()

# Вызываем функцию
updated_records = fix_training_sessions()
if updated_records > 0:
    logger.info(f"Исправлено {updated_records} записей без даты")
# После создания всех таблиц

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_admin INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS trainers (
    trainer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    specialization TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS training_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    address TEXT NOT NULL,
    price INTEGER NOT NULL,
    max_participants INTEGER NOT NULL,
    trainer_id INTEGER,
    FOREIGN KEY (trainer_id) REFERENCES trainers(trainer_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS bookings (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    booking_date TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (session_id) REFERENCES training_sessions(session_id)
)
''')

cursor.execute('''
UPDATE users SET is_admin = 1 WHERE user_id = ?
''', ('7513623853',))
conn.commit()

# Добавляем тренера и тренировку
cursor.execute('''
INSERT OR IGNORE INTO trainers (trainer_id, name, phone, specialization)
VALUES (1, 'Иванов Иван Иванович', '+79999999999', 'Йога')
''')

conn.commit()



def initialize_test_data():
    today = date.today()
    trainer_ids = [1, 2]

    for i in range(14):
        training_date = today + timedelta(days=i)

        # Утренняя тренировка
        cursor.execute('''
        INSERT OR IGNORE INTO training_sessions 
        (date, time, address, price, max_participants, trainer_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (training_date.strftime('%Y-%m-%d'), "09:00", "ул. Спортивная, 10",
              1000, 10, trainer_ids[i % 2]))

        # Вечерняя тренировка
        cursor.execute('''
        INSERT OR IGNORE INTO training_sessions 
        (date, time, address, price, max_participants, trainer_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (training_date.strftime('%Y-%m-%d'), "18:00", "ул. Тренировочная, 5",
              1500, 15, trainer_ids[(i + 1) % 2]))

    conn.commit()


# Вызываем инициализацию тестовых данных
initialize_test_data()

# Callback data factory
date_factory = CallbackData('date', 'action', 'day', prefix='date')
training_factory = CallbackData('training', 'action', 'session_id', prefix='training')


# States
class TrainingStates(StatesGroup):
    select_date = State()
    select_time = State()
    select_address = State()
    confirm_booking = State()


# Admin filter
class AdminFilter(AdvancedCustomFilter):
    key = 'is_admin'

    async def check(self, message, text):
        user_id = message.from_user.id
        cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result and result[0] == 1


bot.add_custom_filter(AdminFilter())


# Helper functions
async def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    return cursor.fetchone()


async def create_user(user_id, username, full_name):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)',
                   (user_id, username, full_name))
    conn.commit()


async def get_available_dates():
    today = date.today()
    dates = []
    for i in range(14):  # 2 weeks ahead
        day = today + timedelta(days=i)
        dates.append(day.strftime('%Y-%m-%d'))
    return dates


async def get_sessions_by_date(selected_date):
    cursor.execute('''
    SELECT ts.session_id, ts.time, ts.address, ts.price, ts.max_participants, 
           t.name as trainer_name,
           COUNT(b.booking_id) as booked_count
    FROM training_sessions ts
    LEFT JOIN trainers t ON ts.trainer_id = t.trainer_id
    LEFT JOIN bookings b ON ts.session_id = b.session_id
    WHERE ts.date = ?
    GROUP BY ts.session_id
    ''', (selected_date,))
    return cursor.fetchall()


async def get_user_bookings(user_id):
    cursor.execute('''
    SELECT b.booking_id, ts.date, ts.time, ts.address, ts.price, t.name as trainer_name
    FROM bookings b
    JOIN training_sessions ts ON b.session_id = ts.session_id
    LEFT JOIN trainers t ON ts.trainer_id = t.trainer_id
    WHERE b.user_id = ?
    ORDER BY ts.date, ts.time
    ''', (user_id,))
    return cursor.fetchall()


async def book_training(user_id, session_id):
    booking_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO bookings (user_id, session_id, booking_date) VALUES (?, ?, ?)',
                   (user_id, session_id, booking_date))
    conn.commit()


async def cancel_booking(booking_id):
    cursor.execute('DELETE FROM bookings WHERE booking_id = ?', (booking_id,))
    conn.commit()


# Handlers
@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()

    await create_user(user_id, username, full_name)

    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('📝 Запись на тренировку'),
        KeyboardButton('📋 Мои записи'),
        KeyboardButton('👨‍🏫 Мой тренер'),
        KeyboardButton('ℹ️ Помощь')
    )

    with open('trainer_sergey.jpeg','rb') as trainer_photo:
        await bot.send_photo(
            message.chat.id, photo=trainer_photo, caption=
            "🏋️‍♂️ Добро пожаловать в систему записи на тренировки!\n\n"
            "Выберите действие на клавиатуре ниже:",
            reply_markup=markup
        )


@bot.message_handler(func=lambda message: message.text == 'ℹ️ Помощь')
async def show_help(message):
    await bot.send_message(
        message.chat.id,
        "ℹ️ Справка по боту:\n\n"
        "📝 Запись на тренировку - выбрать дату и записаться на тренировку\n"
        "📋 Мои записи - просмотреть свои активные записи\n"
        "👨‍🏫 Мой тренер - информация о вашем тренере\n\n"
        "Для начала работы просто выберите действие на клавиатуре."
    )


@bot.message_handler(func=lambda message: message.text == '📝 Запись на тренировку')
async def start_booking(message):
    dates = await get_available_dates()

    markup = InlineKeyboardMarkup()
    for day in dates:
        formatted_day = datetime.strptime(day, '%Y-%m-%d').strftime('%d.%m.%Y')
        callback_data = date_factory.new(action='select', day=day)
        logger.info(f"Создаём кнопку для даты {day} с callback_data: {callback_data}")
        markup.add(InlineKeyboardButton(
            text=formatted_day,
            callback_data=callback_data
        ))

    await bot.send_message(
        message.chat.id,
        "📅 Выберите дату тренировки:",
        reply_markup=markup
    )


@bot.callback_query_handler(func=None, date_config=date_factory.filter(action='select'))
async def select_date_callback(call):
    try:
        logger.info(f"Получен callback запрос: {call.data}")
        data = date_factory.parse(callback_data=call.data)
        logger.info(f"Разобранный callback: {data}")
        selected_date = data['day']

        # Проверяем, что дата корректная
        try:
            datetime.strptime(selected_date, '%Y-%m-%d')
            logger.info(f"Дата {selected_date} корректна")
        except ValueError:
            logger.error(f"Некорректный формат даты: {selected_date}")
            await bot.answer_callback_query(call.id, "Ошибка: некорректный формат даты")
            return

        # Получаем тренировки на выбранную дату
        cursor.execute('''
        SELECT ts.session_id, ts.time, ts.address, ts.price, ts.max_participants, 
               t.name as trainer_name
        FROM training_sessions ts
        LEFT JOIN trainers t ON ts.trainer_id = t.trainer_id
        WHERE ts.date = ? AND ts.date IS NOT NULL
        ''', (selected_date,))
        sessions = cursor.fetchall()

        if not sessions:
            await bot.answer_callback_query(call.id, "На выбранную дату нет доступных тренировок")
            return

        # Получаем количество записей на каждую тренировку
        session_ids = [str(session[0]) for session in sessions]
        placeholders = ','.join('?' for _ in session_ids)

        cursor.execute(f'''
        SELECT session_id, COUNT(*) as booked_count 
        FROM bookings 
        WHERE session_id IN ({placeholders})
        GROUP BY session_id
        ''', session_ids)
        bookings = {row[0]: row[1] for row in cursor.fetchall()}

        markup = InlineKeyboardMarkup()
        for session in sessions:
            session_id, time, address, price, max_participants, trainer_name = session
            booked_count = bookings.get(session_id, 0)
            available = max_participants - booked_count

            if available > 0:
                markup.add(InlineKeyboardButton(
                    text=f"{time} - {address} ({price}₽) - {available}/{max_participants} мест",
                    callback_data=training_factory.new(action='select', session_id=session_id)
                ))

        formatted_date = datetime.strptime(selected_date, '%Y-%m-%d').strftime('%d.%m.%Y')
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🏋️‍♂️ Доступные тренировки на {formatted_date}:",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Error in select_date_callback: {str(e)}", exc_info=True)
        await bot.answer_callback_query(call.id, "Ошибка при загрузке тренировок")


@bot.callback_query_handler(func=None, training_config=training_factory.filter(action='select'))
async def select_training_callback(call):
    try:
        data = training_factory.parse(callback_data=call.data)
        session_id = data['session_id']

        # Получаем полную информацию о тренировке
        cursor.execute('''
        SELECT ts.date, ts.time, ts.address, ts.price, ts.max_participants, 
               t.name as trainer_name
        FROM training_sessions ts
        LEFT JOIN trainers t ON ts.trainer_id = t.trainer_id
        WHERE ts.session_id = ?
        ''', (session_id,))
        session = cursor.fetchone()

        if not session:
            await bot.answer_callback_query(call.id, "Тренировка не найдена", show_alert=True)
            return

        date_str, time_str, address, price, max_participants, trainer_name = session

        # Проверяем что дата существует
        if not date_str:
            await bot.answer_callback_query(call.id, "Ошибка: дата тренировки не указана", show_alert=True)
            return

        # Проверяем количество доступных мест
        cursor.execute('''
        SELECT COUNT(*) FROM bookings WHERE session_id = ?
        ''', (session_id,))
        booked_count = cursor.fetchone()[0]
        available = max_participants - booked_count

        if available <= 0:
            await bot.answer_callback_query(call.id, "К сожалению, все места уже заняты", show_alert=True)
            return

        formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(
            text="✅ Подтвердить запись",
            callback_data=training_factory.new(action='confirm', session_id=session_id)
        ))
        markup.add(InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=training_factory.new(action='cancel', session_id=session_id)
        ))

        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🏋️‍♂️ Вы выбрали тренировку:\n\n"
                 f"📅 Дата: {formatted_date}\n"
                 f"⏰ Время: {time_str}\n"
                 f"📍 Адрес: {address}\n"
                 f"💵 Цена: {price}₽\n"
                 f"👨‍🏫 Тренер: {trainer_name}\n"
                 f"✅ Доступно мест: {available}\n\n"
                 f"Подтвердите запись:",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Error in select_training_callback: {str(e)}", exc_info=True)
        await bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса", show_alert=True)

@bot.callback_query_handler(func=None, training_config=training_factory.filter(action='confirm'))
async def confirm_booking_callback(call):
    try:
        data = training_factory.parse(callback_data=call.data)
        session_id = data['session_id']
        user_id = call.from_user.id

        # Проверяем существование тренировки и доступность мест
        cursor.execute('''
        SELECT ts.date, ts.time, ts.max_participants, 
               (SELECT COUNT(*) FROM bookings WHERE session_id = ?) as booked_count
        FROM training_sessions ts
        WHERE ts.session_id = ? AND ts.date IS NOT NULL
        ''', (session_id, session_id))
        result = cursor.fetchone()

        if not result:
            await bot.answer_callback_query(call.id, "Тренировка не найдена")
            return

        date_str, time_str, max_participants, booked_count = result

        if booked_count >= max_participants:
            await bot.answer_callback_query(call.id, "К сожалению, все места уже заняты")
            return

        # Проверяем, не записан ли уже пользователь
        cursor.execute('''
        SELECT 1 FROM bookings WHERE user_id = ? AND session_id = ?
        ''', (user_id, session_id))
        if cursor.fetchone():
            await bot.answer_callback_query(call.id, "Вы уже записаны на эту тренировку")
            return

        # Создаем запись
        booking_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
        INSERT INTO bookings (user_id, session_id, booking_date)
        VALUES (?, ?, ?)
        ''', (user_id, session_id, booking_date))
        conn.commit()

        await bot.answer_callback_query(call.id, "✅ Вы успешно записаны на тренировку!")
        await bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.error(f"Error in confirm_booking_callback: {e}")
        await bot.answer_callback_query(call.id, "Ошибка при записи на тренировку")

@bot.message_handler(func=lambda message: message.text == '📋 Мои записи')
async def show_my_bookings(message):
    user_id = message.from_user.id
    bookings = await get_user_bookings(user_id)

    if not bookings:
        await bot.send_message(message.chat.id, "📭 У вас нет активных записей на тренировки")
        return

    for booking in bookings:
        booking_id, date_str, time_str, address, price, trainer_name = booking
        formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(
            text="❌ Отписаться от тренировки",
            callback_data=training_factory.new(action='cancel_booking', session_id=booking_id)
        ))

        await bot.send_message(
            message.chat.id,
            f"🏋️‍♂️ Ваша тренировка:\n\n"
            f"📅 Дата: {formatted_date}\n"
            f"⏰ Время: {time_str}\n"
            f"📍 Адрес: {address}\n"
            f"💵 Цена: {price}₽\n"
            f"👨‍🏫 Тренер: {trainer_name}",
            reply_markup=markup
        )


@bot.callback_query_handler(func=None, training_config=training_factory.filter(action='cancel_booking'))
async def cancel_booking_callback(call):
    booking_id = training_factory.parse(callback_data=call.data)['session_id']

    await cancel_booking(booking_id)
    await bot.answer_callback_query(call.id, "❌ Вы отменили запись на тренировку")
    await bot.delete_message(call.message.chat.id, call.message.message_id)


@bot.message_handler(func=lambda message: message.text == '👨‍🏫 Мой тренер')
async def show_my_trainer(message):
    user_id = message.from_user.id

    cursor.execute('''
    SELECT t.name, t.phone, t.specialization
    FROM trainers t
    JOIN bookings b ON t.trainer_id = (
        SELECT ts.trainer_id 
        FROM training_sessions ts 
        JOIN bookings b ON ts.session_id = b.session_id 
        WHERE b.user_id = ? 
        ORDER BY ts.date DESC 
        LIMIT 1
    )
    WHERE b.user_id = ?
    GROUP BY t.trainer_id
    ''', (user_id, user_id))

    trainer = cursor.fetchone()

    if trainer:
        name, phone, specialization = trainer
        await bot.send_message(
            message.chat.id,
            f"👨‍🏫 Ваш тренер:\n\n"
            f"Имя: {name}\n"
            f"Телефон: {phone}\n"
            f"Специализация: {specialization}"
        )
    else:
        await bot.send_message(
            message.chat.id,
            "🤷‍♂️ У вас пока нет тренера. Запишитесь на тренировку, чтобы получить тренера."
        )


# Admin commands
@bot.message_handler(commands=['admin'], is_admin=True)
async def admin_panel(message):
    today = date.today().strftime('%Y-%m-%d')
    sessions = await get_sessions_by_date(today)

    if not sessions:
        await bot.send_message(message.chat.id, "На сегодня нет запланированных тренировок")
        return

    response = "📊 Статистика записей на сегодня:\n\n"
    for session in sessions:
        session_id, time, address, price, max_participants, trainer_name, booked_count = session
        response += (
            f"⏰ {time} - {address}\n"
            f"👨‍🏫 Тренер: {trainer_name}\n"
            f"📊 Записано: {booked_count}/{max_participants}\n"
            f"💵 Цена: {price}₽\n\n"
        )

    await bot.send_message(message.chat.id, response)


# Запуск бота
async def main():
    logger.info("Starting bot...")
    try:
        # Удаляем вебхук перед запуском polling
        await bot.remove_webhook()

        # Запускаем бота
        await bot.infinity_polling()
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        logger.info("Bot stopped")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")