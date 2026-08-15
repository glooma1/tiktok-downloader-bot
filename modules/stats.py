import sqlite3
import time
import io
import re
from collections import Counter
import matplotlib
# Встановлюємо бекенд 'Agg' одразу, щоб уникнути помилок GUI в Docker
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from wordcloud import WordCloud
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='artifacts/bot.log', filemode='a')

# --- Налаштування ---
DB_NAME = "artifacts/bot_database.db"
STOP_WORDS = {
    # Українська
    'і', 'й', 'та', 'на', 'що', 'як', 'це', 'для', 'не', 'але', 'до', 'в', 'у', 'з', 'зі', 
    'він', 'вона', 'воно', 'вони', 'ми', 'ви', 'ти', 'я', 'про', 'за', 'по', 'так', 'ні',
    # Англійська
    'the', 'and', 'to', 'of', 'a', 'in', 'is', 'that', 'for', 'it', 'on', 'with', 'as', 'this', 'by', 'at', 'an', 'be', 'are', 'from', 'or', 'not'
}

def init_db():
    """Ініціалізує базу даних та проводить міграцію, якщо потрібно."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Створення таблиці (якщо її немає)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            username TEXT,
            first_name TEXT,
            message_text TEXT,
            timestamp REAL
        )
    ''')
    
    # 2. Міграція: перевіряємо, чи є колонка chat_id у старих базах
    cursor.execute("PRAGMA table_info(daily_stats)")
    columns = [info[1] for info in cursor.fetchall()]
    
    if 'chat_id' not in columns:
        logging.warning("⚠️ Виявлено стару схему БД. Додаю колонку chat_id...")
        try:
            cursor.execute("ALTER TABLE daily_stats ADD COLUMN chat_id INTEGER")
            conn.commit()
            logging.info("✅ БД успішно оновлено.")
        except Exception as e:
            logging.error(f"❌ Помилка міграції БД: {e}")

    conn.commit()
    conn.close()

def log_message_middleware(bot, message):
    """
    Мідлвар для збереження кожного текстового повідомлення.
    """
    if message.content_type != 'text':
        return

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Безпечне отримання даних
        u_id = message.from_user.id
        c_id = message.chat.id  # <--- Зберігаємо ID чату
        u_name = message.from_user.username or ""
        f_name = message.from_user.first_name or ""
        text = message.text or ""
        ts = time.time()

        cursor.execute('''
            INSERT INTO daily_stats (user_id, chat_id, username, first_name, message_text, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (u_id, c_id, u_name, f_name, text, ts))
        
        conn.commit()
    except Exception as e:
        logging.error(f"Помилка логування повідомлення: {e}")
    finally:
        if conn:
            conn.close()

def get_daily_stats(target_chat_id):
    """
    Аналізує повідомлення за останні 24 години ДЛЯ КОНКРЕТНОГО ЧАТУ.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cutoff_time = time.time() - (24 * 60 * 60)
    
    # Фільтруємо по timestamp ТА по chat_id
    cursor.execute('''
        SELECT user_id, username, first_name, message_text 
        FROM daily_stats 
        WHERE timestamp > ? AND chat_id = ?
    ''', (cutoff_time, target_chat_id))
    
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "📉 У цьому чаті за останні 24 години повідомлень не знайдено.", None

    # --- 1. Логіка Топ користувачів ---
    user_counts = Counter()
    user_names = {} 

    all_text = []

    for r in rows:
        uid, uname, fname, text = r
        user_counts[uid] += 1
        
        if uid not in user_names:
            user_names[uid] = f"@{uname}" if uname else fname
        
        all_text.append(text)

    top_3 = user_counts.most_common(3)
    
    stats_msg = "📊 <b>Статистика чату за 24 години:</b>\n\n"
    stats_msg += "🏆 <b>Найактивніші:</b>\n"
    for idx, (uid, count) in enumerate(top_3, 1):
        name = user_names.get(uid, "Unknown")
        stats_msg += f"{idx}. {name} — {count} повідомлень\n"

    # --- 2. Логіка Хмари Слів ---
    full_text = " ".join(all_text)
    full_text = re.sub(r'https?://\S+|www\.\S+', '', full_text)  # Видаляємо посилання
    full_text = re.sub(r'[@#]\S+', '', full_text)  # Видаляємо згадки та хештеги
    # Регулярка для слів (кирилиця + латиниця + цифри)
    tokens = re.findall(r'[a-zA-Zа-яА-ЯїієґЇІЄҐ]+', full_text.lower())
    
    cleaned_tokens = [w for w in tokens if w not in STOP_WORDS and len(w) > 2]
    
    img_buffer = None
    
    if len(cleaned_tokens) > 5:
        try:
            cleaned_text = " ".join(cleaned_tokens)
            
            wc = WordCloud(
                width=800, 
                height=400, 
                background_color='white',
                regexp=r"[a-zA-Zа-яА-ЯїієґЇІЄҐ]+" 
            ).generate(cleaned_text)

            plt.figure(figsize=(10, 5))
            plt.imshow(wc, interpolation='bilinear')
            plt.axis('off')
            
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight')
            img_buffer.seek(0)
            plt.close()
        except Exception as e:
            logging.error(f"WordCloud generation failed: {e}")
            stats_msg += "\n⚠️ Не вдалося створити хмару слів."
    else:
        stats_msg += "\n📝 Недостатньо слів для генерації хмари."

    return stats_msg, img_buffer

# --- Інтеграція ---
def register_stats_handlers(bot):
    init_db()

    @bot.middleware_handler(update_types=['message'])
    def middleware_logger(bot_instance, message):
        log_message_middleware(bot_instance, message)

    @bot.message_handler(commands=['stats'])
    def handle_stats(message):
        status_msg = bot.send_message(message.chat.id, "🔄 Рахую статистику чату...")
        
        try:
            # Передаємо message.chat.id, щоб отримати статистику саме цього чату
            text_response, photo_file = get_daily_stats(message.chat.id)
            
            bot.delete_message(message.chat.id, status_msg.message_id)
            
            if photo_file:
                bot.send_photo(
                    message.chat.id, 
                    photo_file, 
                    caption=text_response, 
                    parse_mode="HTML"
                )
            else:
                bot.send_message(
                    message.chat.id, 
                    text_response, 
                    parse_mode="HTML"
                )
        except Exception as e:
            bot.edit_message_text(f"Помилка: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)