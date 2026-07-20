import os
import json
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ================= НАСТРОЙКИ =================
TOKEN = os.environ.get('TOKEN', '8982534262:AAGRHPxIN50Q5PSbTkfrymYG9PjktECzgB8')
ADMIN_ID = 914930076 # <-- ВСТАВЬТЕ СЮДА ВАШ ID ИЗ @userinfobot
DATA_FILE = "shift_data.json"

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
operators_db = {}
schedule_db = {}
skip_db = {}
swap_requests = {}
attendance_db = {}
GROUP_CHAT_ID = None  # ID группового чата для отправки графика

# ================= РАБОТА С ФАЙЛАМИ =================
def load_data():
    global operators_db, schedule_db, skip_db, swap_requests, attendance_db, GROUP_CHAT_ID
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            operators_db = data.get('operators', {})
            schedule_db = data.get('schedule', {})
            skip_db = data.get('skips', {})
            swap_requests = data.get('swaps', {})
            attendance_db = data.get('attendance', {})
            GROUP_CHAT_ID = data.get('group_chat_id', None)
    else:
        operators_db, schedule_db, skip_db, swap_requests, attendance_db = {}, {}, {}, {}, {}
        GROUP_CHAT_ID = None

def save_data():
    data = {
        'operators': operators_db,
        'schedule': schedule_db,
        'skips': skip_db,
        'swaps': swap_requests,
        'attendance': attendance_db,
        'group_chat_id': GROUP_CHAT_ID
    }
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_ru_day(date_obj):
    ru_days = {"Monday": "Пн", "Tuesday": "Вт", "Wednesday": "Ср", "Thursday": "Чт",
               "Friday": "Пт", "Saturday": "Сб", "Sunday": "Вс"}
    return ru_days.get(date_obj.strftime('%A'), date_obj.strftime('%A'))

def parse_date(date_str):
    try:
        day, month = date_str.split('.')
        return datetime(2026, int(month), int(day)).strftime('%Y-%m-%d')
    except:
        return None

def find_replacement(exclude_user_id, date_str):
    active_operators = [uid for uid in operators_db.keys() if uid != exclude_user_id]
    if not active_operators:
        return None
    
    today = datetime.strptime(date_str, '%Y-%m-%d').date()
    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    day_before = (today - timedelta(days=2)).strftime('%Y-%m-%d')
    
    worked_recently = set()
    if yesterday in schedule_db:
        for s in schedule_db[yesterday]: worked_recently.add(s['user_id'])
    if day_before in schedule_db:
        for s in schedule_db[day_before]: worked_recently.add(s['user_id'])
    if date_str in skip_db:
        worked_recently.add(skip_db[date_str]['user_id'])
    
    available = [uid for uid in active_operators if uid not in worked_recently]
    if not available:
        available = active_operators[:]
    
    available.sort(key=lambda uid: operators_db[uid]['shifts_count'])
    return available[0] if available else None

def is_admin(user_id):
    return user_id == ADMIN_ID

async def safe_send(bot, chat_id, text, parse_mode=None):
    try:
        if parse_mode:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        else:
            await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"Ошибка отправки в {chat_id}: {e}")

# ================= КОМАНДЫ: ОБЩИЕ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    role = "Администратор" if is_admin(user_id) else "Оператор"
    
    text = (f"👋 Привет, {name}! Я бот Veda для управления сменами.\n"
            f"Твоя роль: {role}\n\n"
            f"Напиши /help чтобы увидеть все команды.")
    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin = is_admin(user_id)
    
    text = "📋 <b>Команды для всех:</b>\n"
    text += "/register — зарегистрироваться\n"
    text += "/my_shifts — мои смены на 2 недели\n"
    text += "/next — моя следующая смена\n"
    text += "/schedule — общий график на неделю\n"
    text += "/skip ДД.ММ причина — пропустить смену\n"
    text += "/swap ДД.ММ @username — поменяться сменой\n"
    text += "/checkin — отметиться на смене\n"
    text += "/checkout — отметиться об уходе\n\n"
    
    if admin:
        text += "👑 <b>Команды Админа:</b>\n"
        text += "/operators — список операторов\n"
        text += "/add_operator @username Имя — добавить оператора\n"
        text += "/remove_operator @username — удалить оператора\n"
        text += "/assign @username ДД.ММ — назначить на смену\n"
        text += "/remove @username ДД.ММ — убрать со смены\n"
        text += "/operator_stats — статистика по операторам\n"
        text += "/monthly_stats — статистика за месяц\n"
        text += "/generate_week — график на 7 дней\n"
        text += "/generate_month — график на 30 дней\n"
        text += "/set_group — установить чат для графика\n"
        text += "/clear_schedule — очистить график\n"
        text += "/attendance ДД.ММ — кто вышел на смену\n\n"
        text += "💡 <b>Быстрое назначение (без команд):</b>\n"
        text += "Просто напишите: <i>Анна 15.07</i> или <i>15.07 Анна Иван</i>\n"
    
    await update.message.reply_text(text, parse_mode='HTML')

# ================= РЕГИСТРАЦИЯ =================
async def register_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name
    username = update.effective_user.username or "без_username"
    
    if user_id in operators_db:
        await update.message.reply_text(f"Вы уже зарегистрированы как {operators_db[user_id]['name']}! ✅")
        return
    
    operators_db[user_id] = {
        "name": name,
        "username": f"@{username}",
        "shifts_count": 0,
        "chat_id": chat_id
    }
    save_data()
    await update.message.reply_text(f"✅ {name}, вы успешно зарегистрированы!")

async def add_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /add_operator @username Имя")
        return
    
    username = context.args[0].lstrip('@')
    name = " ".join(context.args[1:])
    
    user_id = hash(username) % 1000000000
    
    operators_db[user_id] = {
        "name": name,
        "username": f"@{username}",
        "shifts_count": 0,
        "chat_id": None
    }
    save_data()
    await update.message.reply_text(f"✅ Оператор {name} (@{username}) добавлен в систему!")

async def remove_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полное удаление оператора из системы и всех графиков"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /remove_operator @username")
        return
    
    username = context.args[0].lstrip('@')
    target_uid = None
    target_name = None
    
    for uid, data in list(operators_db.items()):
        if data['username'].lstrip('@') == username:
            target_uid = uid
            target_name = data['name']
            break
    
    if not target_uid:
        await update.message.reply_text(f"Оператор @{username} не найден.")
        return
    
    del operators_db[target_uid]
    
    for day in schedule_db:
        schedule_db[day] = [s for s in schedule_db[day] if s['user_id'] != target_uid]
        
    days_to_clean = [day for day, data in skip_db.items() if data.get('user_id') == target_uid]
    for day in days_to_clean:
        del skip_db[day]
        
    for day in attendance_db:
        if target_uid in attendance_db[day]:
            del attendance_db[day][target_uid]
            
    keys_to_delete = [k for k, v in swap_requests.items() if v.get('from') == target_uid or v.get('to') == target_uid]
    for k in keys_to_delete:
        del swap_requests[k]
        
    save_data()
    await update.message.reply_text(f"🗑️ Оператор {target_name} (@{username}) полностью удален из системы, включая все его смены и историю.")

# ================= ПРОСМОТР ГРАФИКА =================
async def my_shifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in operators_db:
        await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        return
    
    name = operators_db[user_id]['name']
    today = datetime.now().date()
    my_shifts_list = []
    
    for i in range(14):
        day = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        if day in schedule_db:
            for shift in schedule_db[day]:
                if shift['user_id'] == user_id:
                    my_shifts_list.append((day, shift))
    
    if not my_shifts_list:
        await update.message.reply_text(f"{name}, на ближайшие 2 недели у вас нет назначенных смен. 🌴")
        return
    
    msg = f"📅 Ваши смены, {name} (09:00 - 20:00):\n\n"
    for day, shift in my_shifts_list:
        date_obj = datetime.strptime(day, '%Y-%m-%d')
        day_ru = get_ru_day(date_obj)
        skip_mark = " ⚠️ ПРОПУСК" if day in skip_db and skip_db[day].get('user_id') == user_id else ""
        msg += f"📅 {date_obj.strftime('%d.%m')} ({day_ru}){skip_mark}\n"
    
    await update.message.reply_text(msg)

async def next_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in operators_db:
        await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        return
    
    name = operators_db[user_id]['name']
    today = datetime.now().date()
    
    for i in range(30):
        day = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        if day in schedule_db:
            for shift in schedule_db[day]:
                if shift['user_id'] == user_id:
                    if day in skip_db and skip_db[day].get('user_id') == user_id:
                        continue
                    date_obj = datetime.strptime(day, '%Y-%m-%d')
                    day_ru = get_ru_day(date_obj)
                    days_left = i
                    if days_left == 0:
                        when = "сегодня"
                    elif days_left == 1:
                        when = "завтра"
                    else:
                        when = f"через {days_left} дня"
                    
                    msg = f"📅 {name}, твоя следующая смена: <b>{date_obj.strftime('%d.%m')} ({day_ru})</b> — {when}\n 09:00 - 20:00"
                    await update.message.reply_text(msg, parse_mode='HTML')
                    return
    
    await update.message.reply_text(f"{name}, у вас нет предстоящих смен. 🌴")

async def schedule_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    msg = "📋 <b>График смен на неделю (09:00 - 20:00):</b>\n\n"
    
    has_shifts = False
    for i in range(7):
        day = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        day_obj = today + timedelta(days=i)
        
        if day in schedule_db and schedule_db[day]:
            has_shifts = True
            day_ru = get_ru_day(day_obj)
            skip_info = ""
            if day in skip_db:
                skip_name = operators_db.get(skip_db[day]['user_id'], {}).get('name', 'Неизвестно')
                skip_info = f" ⚠️ {skip_name} не выйдет ({skip_db[day]['reason']})"
            
            msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>{skip_info}\n"
            for shift in schedule_db[day]:
                if day in skip_db and skip_db[day].get('user_id') == shift['user_id']:
                    msg += f"  ❌ <s>{shift['name']}</s> (пропуск)\n"
                else:
                    msg += f"  👤 {shift['name']}\n"
            
            if day in skip_db and 'replacement' in skip_db[day]:
                repl_name = operators_db.get(skip_db[day]['replacement'], {}).get('name', 'Неизвестно')
                msg += f"  🔄 Замена: {repl_name}\n"
            msg += "\n"
    
    if not has_shifts:
        msg = "📭 На ближайшую неделю график ещё не составлен."
    
    await update.message.reply_text(msg, parse_mode='HTML')

# ================= УПРАВЛЕНИЕ ОПЕРАТОРАМИ (АДМИН) =================
async def list_operators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    if not operators_db:
        await update.message.reply_text("Пока никто не зарегистрирован.")
        return
    
    msg = "👥 Список операторов:\n\n"
    for uid, data in operators_db.items():
        msg += f"• {data['name']} ({data['username']}) — смен: {data['shifts_count']}\n"
    
    await update.message.reply_text(msg)

async def operator_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    if not operators_db:
        await update.message.reply_text("Пока нет данных.")
        return
    
    skip_counts = {}
    for day, data in skip_db.items():
        uid = data.get('user_id')
        if uid:
            skip_counts[uid] = skip_counts.get(uid, 0) + 1
    
    sorted_operators = sorted(operators_db.items(), key=lambda x: x[1]['shifts_count'], reverse=True)
    
    msg = "📊 <b>Статистика операторов:</b>\n\n"
    for uid, data in sorted_operators:
        skips = skip_counts.get(uid, 0)
        reliability = "✅" if skips == 0 else ("⚠️" if skips <= 2 else "❌")
        msg += f"{reliability} <b>{data['name']}</b> — {data['shifts_count']} смен, {skips} пропусков\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def monthly_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    today = datetime.now().date()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    
    msg = f"📊 <b>Статистика за {month_start.strftime('%B %Y')}:</b>\n\n"
    
    operator_monthly = {uid: 0 for uid in operators_db}
    for day_str, shifts in schedule_db.items():
        day = datetime.strptime(day_str, '%Y-%m-%d').date()
        if month_start <= day <= month_end:
            for shift in shifts:
                uid = shift['user_id']
                if uid in operator_monthly:
                    operator_monthly[uid] += 1
    
    sorted_ops = sorted(operator_monthly.items(), key=lambda x: x[1], reverse=True)
    for uid, count in sorted_ops:
        name = operators_db[uid]['name']
        msg += f"• {name}: {count} смен\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

# ================= УСТАНОВКА ГРУППОВОГО ЧАТА =================
async def set_group_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить chat_id группового чата для отправки графика"""
    global GROUP_CHAT_ID
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    GROUP_CHAT_ID = update.effective_chat.id
    save_data()
    
    await update.message.reply_text(f"✅ Групповой чат установлен! График будет дублироваться сюда.\nChat ID: {GROUP_CHAT_ID}")

# ================= ГЕНЕРАЦИЯ ГРАФИКА =================
async def generate_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    active_operators = list(operators_db.keys())
    if len(active_operators) < 2:
        await update.message.reply_text("Нужно минимум 2 оператора!")
        return
    
    today = datetime.now().date()
    msg = "️ <b>График смен на неделю (09:00 - 20:00):</b>\n\n"
    group_msg = "📋 <b>График смен на неделю (09:00 - 20:00):</b>\n\n"
    notifications = []
    manually_assigned_count = 0
    
    for i in range(7):
        day = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        day_obj = today + timedelta(days=i)
        yesterday = (today + timedelta(days=i-1)).strftime('%Y-%m-%d')
        day_before = (today + timedelta(days=i-2)).strftime('%Y-%m-%d')
        
        existing_shifts = schedule_db.get(day, [])
        existing_user_ids = {s['user_id'] for s in existing_shifts}
        
        worked_recently = set(existing_user_ids)
        if yesterday in schedule_db:
            for s in schedule_db[yesterday]: worked_recently.add(s['user_id'])
        if day_before in schedule_db:
            for s in schedule_db[day_before]: worked_recently.add(s['user_id'])
        if day in skip_db:
            worked_recently.add(skip_db[day]['user_id'])
        
        if len(existing_shifts) >= 2:
            manually_assigned_count += 1
            day_ru = get_ru_day(day_obj)
            msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b> ✅ (уже назначено)\n"
            group_msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
            for shift in existing_shifts:
                name = operators_db.get(shift['user_id'], {}).get('name', 'Неизвестно')
                msg += f"  👤 {name} (вручную)\n"
                group_msg += f"  👤 {name}\n"
            msg += "\n"
            group_msg += "\n"
            continue
        
        available = [uid for uid in active_operators if uid not in worked_recently]
        if len(available) < 2:
            available = active_operators[:]
        
        available.sort(key=lambda uid: operators_db[uid]['shifts_count'])
        
        needed = 2 - len(existing_shifts)
        chosen_ids = available[:needed]
        
        for uid in chosen_ids:
            operators_db[uid]['shifts_count'] += 1
            existing_shifts.append({"user_id": uid, "name": operators_db[uid]['name']})
        
        schedule_db[day] = existing_shifts
        
        day_ru = get_ru_day(day_obj)
        msg += f" <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
        group_msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
        
        for shift in existing_shifts:
            uid = shift['user_id']
            name = shift['name']
            msg += f"  👤 {name}\n"
            group_msg += f"  👤 {name}\n"
            
            existing = [n for n in notifications if n['uid'] == uid]
            if not existing:
                notifications.append({'uid': uid, 'days': []})
            for n in notifications:
                if n['uid'] == uid:
                    n['days'].append(f"{day_obj.strftime('%d.%m')} ({day_ru})")
        
        msg += "\n"
        group_msg += "\n"
        
        if len(existing_shifts) < 2:
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ На {day_obj.strftime('%d.%m')} не удалось назначить 2 операторов!")
            except: pass
    
    save_data()
    
    if manually_assigned_count > 0:
        msg = f"ℹ️ Учтено {manually_assigned_count} дней с ручными назначениями.\n\n" + msg
    
    await update.message.reply_text(msg, parse_mode='HTML')
    
    if GROUP_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_msg, parse_mode='HTML')
        except Exception as e:
            print(f"Не удалось отправить в группу: {e}")
    
    for notif in notifications:
        uid = notif['uid']
        if uid in operators_db and operators_db[uid].get('chat_id'):
            days_str = ", ".join(notif['days'])
            try:
                await context.bot.send_message(
                    chat_id=operators_db[uid]['chat_id'],
                    text=f"📅 {operators_db[uid]['name']}, ваш график на неделю:\n{days_str}\n⏰ 09:00 - 20:00"
                )
            except: pass

async def generate_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    active_operators = list(operators_db.keys())
    if len(active_operators) < 2:
        await update.message.reply_text("Нужно минимум 2 оператора!")
        return
    
    today = datetime.now().date()
    msg = "🗓️ <b>График смен на месяц (09:00 - 20:00):</b>\n\n"
    group_msg = "📋 <b>График смен на месяц (09:00 - 20:00):</b>\n\n"
    manually_assigned_count = 0
    
    for i in range(30):
        day = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        day_obj = today + timedelta(days=i)
        yesterday = (today + timedelta(days=i-1)).strftime('%Y-%m-%d')
        day_before = (today + timedelta(days=i-2)).strftime('%Y-%m-%d')
        
        existing_shifts = schedule_db.get(day, [])
        existing_user_ids = {s['user_id'] for s in existing_shifts}
        
        worked_recently = set(existing_user_ids)
        if yesterday in schedule_db:
            for s in schedule_db[yesterday]: worked_recently.add(s['user_id'])
        if day_before in schedule_db:
            for s in schedule_db[day_before]: worked_recently.add(s['user_id'])
        
        if len(existing_shifts) >= 2:
            manually_assigned_count += 1
            day_ru = get_ru_day(day_obj)
            msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b> ✅ (уже назначено)\n"
            group_msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
            for shift in existing_shifts:
                name = operators_db.get(shift['user_id'], {}).get('name', 'Неизвестно')
                msg += f"  👤 {name} (вручную)\n"
                group_msg += f"  👤 {name}\n"
            msg += "\n"
            group_msg += "\n"
            continue
        
        available = [uid for uid in active_operators if uid not in worked_recently]
        if len(available) < 2:
            available = active_operators[:]
        
        available.sort(key=lambda uid: operators_db[uid]['shifts_count'])
        
        needed = 2 - len(existing_shifts)
        chosen_ids = available[:needed]
        
        for uid in chosen_ids:
            operators_db[uid]['shifts_count'] += 1
            existing_shifts.append({"user_id": uid, "name": operators_db[uid]['name']})
        
        schedule_db[day] = existing_shifts
        
        day_ru = get_ru_day(day_obj)
        msg += f" <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
        group_msg += f"📅 <b>{day_obj.strftime('%d.%m')} ({day_ru})</b>\n"
        
        for shift in existing_shifts:
            name = shift['name']
            msg += f"  👤 {name}\n"
            group_msg += f"  👤 {name}\n"
        
        msg += "\n"
        group_msg += "\n"
    
    save_data()
    
    if manually_assigned_count > 0:
        msg = f"ℹ️ Учтено {manually_assigned_count} дней с ручными назначениями.\n\n" + msg
    
    await update.message.reply_text(msg, parse_mode='HTML')
    
    if GROUP_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_msg, parse_mode='HTML')
        except Exception as e:
            print(f"Не удалось отправить в группу: {e}")

# ================= РУЧНОЕ УПРАВЛЕНИЕ СМЕНАМИ =================
async def assign_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ❌")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /assign @username ДД.ММ")
        return
    
    username = context.args[0].lstrip('@')
    date_str = context.args[1]
    date_formatted = parse_date(date_str)
    
    if not date_formatted:
        await update.message.reply_text("Неверный формат даты. Используйте ДД.ММ")
        return
    
    target_uid = None
    target_name = None
    for uid, data in operators_db.items():
        if data['username'].lstrip('@') == username:
            target_uid = uid
            target_name = data['name']
            break
    
    if not target_uid:
        await update.message.reply_text(f"Оператор @{username} не найден.")
        return
    
    if date_formatted not in schedule_db:
        schedule_db[date_formatted] = []
    
    if any(s['user_id'] == target_uid for s in schedule_db[date_formatted]):
        await update.message.reply_text(f"{target_name} уже назначен на {date_str}.")
        return
    
    schedule_db[date_formatted].append({"user_id": target_uid, "name": target_name})
    operators_db[target_uid]['shifts_count'] += 1
    save_data()
    
    date_obj = datetime.strptime(date_formatted, '%Y-%m-%d')
    day_ru = get_ru_day(date_obj)
    await update.message.reply_text(f"✅ {target_name} назначен на {date_obj.strftime('%d.%m')} ({day_ru})")

async def remove_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только для администратора! ")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /remove @username ДД.ММ")
        return
    
    username = context.args[0].lstrip('@')
    date_str = context.args[1]
    date_formatted = parse_date(date_str)
    
    if not date_formatted:
        await update.message.reply_text("Неверный формат даты.")
        return
    
    if date_formatted not in schedule_db:
        await update.message.reply_text(f"На {date_str} нет графика.")
        return
    
    for i, shift in enumerate(schedule_db[date_formatted]):
        uid = shift['user_id']
        if uid in operators_db and operators_db[uid]['username'].lstrip('@') == username:
            name = operators_db[uid]['name']
            schedule_db[date_formatted].pop(i)
            operators_db[uid]['shifts_count'] = max(0, operators_db[uid]['shifts_count'] - 1)
            save_data()
            await update.message.reply_text(f"️ {name} убран со смены {date_str}")
            return
    
    await update.message.reply_text(f"Оператор @{username} не найден в графике на {date_str}.")

# ================= ПРОПУСК И ЗАМЕНА =================
async def skip_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in operators_db:
        await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Использование: /skip ДД.ММ причина\nПример: /skip 15.07 болезнь")
        return
    
    date_str = context.args[0]
    reason = " ".join(context.args[1:])
    date_formatted = parse_date(date_str)
    
    if not date_formatted:
        await update.message.reply_text("Неверный формат даты.")
        return
    
    if date_formatted not in schedule_db:
        await update.message.reply_text(f"На {date_str} у вас нет смены.")
        return
    
    user_has_shift = any(s['user_id'] == user_id for s in schedule_db[date_formatted])
    if not user_has_shift:
        await update.message.reply_text(f"На {date_str} у вас нет смены.")
        return
    
    replacement_id = find_replacement(user_id, date_formatted)
    
    skip_db[date_formatted] = {
        'user_id': user_id,
        'reason': reason,
        'replacement': replacement_id
    }
    
    name = operators_db[user_id]['name']
    msg = f"⚠️ {name}, ваша смена {date_str} отмечена как пропуск.\nПричина: {reason}\n"
    
    if replacement_id:
        repl_name = operators_db[replacement_id]['name']
        schedule_db[date_formatted].append({"user_id": replacement_id, "name": repl_name})
        operators_db[replacement_id]['shifts_count'] += 1
        msg += f"🔄 Автоматически назначена замена: {repl_name}"
        
        repl_chat_id = operators_db[replacement_id].get('chat_id')
        if repl_chat_id:
            await safe_send(
                context.bot,
                repl_chat_id,
                f"🔄 {repl_name}, вам назначена замена! Смена {date_str} (09:00-20:00). Причина: {reason}"
            )
    else:
        msg += "❗ Не удалось найти замену. Администратор уведомлён."
        await safe_send(
            context.bot,
            ADMIN_ID,
            f"⚠️ {name} пропускает смену {date_str} (причина: {reason}). Замена не найдена!"
        )
    
    save_data()
    await update.message.reply_text(msg)

async def swap_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in operators_db:
        await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /swap ДД.ММ @username")
        return
    
    date_str = context.args[0]
    target_username = context.args[1].lstrip('@')
    date_formatted = parse_date(date_str)
    
    if not date_formatted:
        await update.message.reply_text("Неверный формат даты.")
        return
    
    target_user = None
    for uid, data in operators_db.items():
        if data['username'].lstrip('@') == target_username:
            target_user = {'id': uid, 'data': data}
            break
    
    if not target_user:
        await update.message.reply_text(f"Оператор @{target_username} не найден.")
        return
    
    if date_formatted not in schedule_db:
        await update.message.reply_text(f"На {date_str} нет графика.")
        return
    
    requester_has = any(s['user_id'] == user_id for s in schedule_db[date_formatted])
    target_has = any(s['user_id'] == target_user['id'] for s in schedule_db[date_formatted])
    
    if not requester_has or not target_has:
        await update.message.reply_text("У одного из вас нет смены на эту дату.")
        return
    
    request_id = f"{user_id}_{target_user['id']}_{date_formatted}"
    swap_requests[request_id] = {
        'from': user_id,
        'to': target_user['id'],
        'date': date_formatted,
        'status': 'pending'
    }
    save_data()
    
    target_chat_id = target_user['data'].get('chat_id')
    requester_name = operators_db[user_id]['name']
    
    keyboard = [
        [InlineKeyboardButton("✅ Согласен", callback_data=f"swap_accept_{request_id}"),
         InlineKeyboardButton(" Отказ", callback_data=f"swap_decline_{request_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if target_chat_id:
        await safe_send(
            context.bot,
            target_chat_id,
            f" {requester_name} хочет поменяться с вами сменой {date_str}. Согласны?",
        )
        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text="Выберите:",
                reply_markup=reply_markup
            )
        except:
            pass
    
    await update.message.reply_text(f"✅ Запрос на замену отправлен {target_user['data']['name']}.")

async def handle_swap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith('swap_'):
        return
    
    action, request_id = data.split('_', 1)
    if request_id not in swap_requests:
        await query.edit_message_text("Запрос не найден.")
        return
    
    swap = swap_requests[request_id]
    from_user = operators_db.get(swap['from'])
    to_user = operators_db.get(swap['to'])
    date_formatted = swap['date']
    
    if action == 'accept':
        if date_formatted in schedule_db:
            new_schedule = []
            for s in schedule_db[date_formatted]:
                if s['user_id'] == swap['from']:
                    new_schedule.append({"user_id": swap['to'], "name": to_user['name']})
                elif s['user_id'] == swap['to']:
                    new_schedule.append({"user_id": swap['from'], "name": from_user['name']})
                else:
                    new_schedule.append(s)
            schedule_db[date_formatted] = new_schedule
        
        swap['status'] = 'accepted'
        save_data()
        await query.edit_message_text(f"✅ {to_user['name']} согласился! Смены поменяны.")
        
        from_chat_id = from_user.get('chat_id')
        if from_chat_id:
            await safe_send(
                context.bot,
                from_chat_id,
                f"✅ {to_user['name']} согласился поменяться сменой {date_formatted}!"
            )
    
    elif action == 'decline':
        swap['status'] = 'declined'
        save_data()
        await query.edit_message_text(f"❌ {to_user['name']} отказался.")
        
        from_chat_id = from_user.get('chat_id')
        if from_chat_id:
            await safe_send(
                context.bot,
                from_chat_id,
                f"❌ {to_user['name']} отказался меняться сменой {date_formatted}."
            )

# ================= ОТМЕТКИ ПОСЕЩАЕМОСТИ =================
async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in operators_db:
        await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        return
    
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now().strftime('%H:%M')
    
    if today not in schedule_db or not any(s['user_id'] == user_id for s in schedule_db[today]):
        await update.message.reply_text("У вас нет смены сегодня!")
        return
    
    if today not in attendance_db:
        attendance_db[today] = {}
    
    attendance_db[today][user_id] = {'checkin': now, 'checkout': None}
    save_data()
    
    name = operators_db[user_id]['name']
    await update.message.reply_text(f"✅ {name}, вы отметились на смене в {now}")

async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
   
