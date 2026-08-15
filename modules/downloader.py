import instaloader
from instaloader import Post
import requests
# from pybalt import download
from asyncio import run

import yt_dlp
import os
import uuid
import subprocess
import re
import time
import shutil
import glob
import logging
from pathlib import Path
import configparser

config = configparser.ConfigParser()
config.read('config.ini')

ARTIFACTS_DIR = Path("artifacts")



# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('artifacts/bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def render_progressbar(percent, length=10):
    """Малює смужку: [████░░░░░░] 40%"""
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {int(percent)}%"

def compress_video(input_path, total_duration, progress_callback=None):
    output_path = f"{os.path.splitext(input_path)[0]}_compressed.mp4"
    
    # 1. Цільовий розмір: 48.5 МБ (залишаємо 1.5 МБ запасу до ліміту Телеграму)
    target_size_mb = 48.5
    
    # Якщо тривалість невідома або 0, ставимо дефолтну стратегію (CRF 26 - хороша якість)
    if total_duration <= 0:
        video_bitrate_str = None
    else:
        # 2. Розрахунок бітрейту
        # Формула: (Розмір_в_бітах) / Тривалість
        target_total_bitrate_kbit = (target_size_mb * 8 * 1024) / total_duration
        
        # Віднімаємо 128 кбіт/с на аудіо
        target_video_bitrate_kbit = target_total_bitrate_kbit - 128
        
        # Робимо перевірку, щоб не ставити занадто низький бітрейт (менше 500кбіт - це мило)
        if target_video_bitrate_kbit < 500:
            target_video_bitrate_kbit = 500 
            # Якщо вийде більше 50МБ - бот просто видасть помилку, 
            # але ми не хочемо надсилати "кашу" з пікселів.
            
        video_bitrate_str = f"{int(target_video_bitrate_kbit)}k"

    command = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-c:v', 'libx264',
        '-preset', 'fast', 
    ]

    if video_bitrate_str:
        # СТРАТЕГІЯ 1: Розрахований бітрейт (найкраще під 50МБ)
        command.extend([
            '-b:v', video_bitrate_str,      # Цільовий середній бітрейт
            '-maxrate', video_bitrate_str,  # Не перевищувати цей поріг
            '-bufsize', f"{int(target_video_bitrate_kbit * 2)}k" # Розмір буфера
        ])
    else:
        # СТРАТЕГІЯ 2: CRF (якщо не знаємо тривалості)
        # 26 - це "золота середина" для H.264 (менше число = краща якість)
        command.extend(['-crf', '26'])

    # Масштабування
    # Змінюємо ліміт з 720p на 1080p (ширина 1920)
    # Якщо відео 4K -> стане 1080p. Якщо 1080p -> залишиться як є.
    command.extend(['-vf', "scale='min(1920,iw)':-2"])

    # Аудіо
    command.extend(['-c:a', 'aac', '-b:a', '128k'])
    
    command.append(output_path)

    try:
        process = subprocess.Popen(
            command, 
            stderr=subprocess.PIPE, 
            universal_newlines=True
        )

        # ... (КОД ПРОГРЕС БАРУ ТУТ - ТАКИЙ ЖЕ ЯК БУВ) ...
        # Обов'язково скопіюйте сюди цикл читання process.stderr з минулої відповіді
        # ...
        
        # --- ТИМЧАСОВА КОПІЯ ДЛЯ ЗРУЧНОСТІ, ЯКЩО ЗАБУЛИ: ---
        import re, time
        last_update_time = 0
        time_pattern = re.compile(r'time=(\d{2}):(\d{2}):(\d{2})')
        for line in process.stderr:
            match = time_pattern.search(line)
            if match and total_duration > 0:
                h, m, s = map(int, match.groups())
                curr_s = h * 3600 + m * 60 + s
                percent = (curr_s / total_duration) * 100
                if time.time() - last_update_time > 2 and progress_callback:
                    # Функція render_progressbar має бути десь оголошена
                    bar = "█" * int(percent/10) + "░" * (10 - int(percent/10))
                    progress_callback(f"[{bar} {str(int(percent))}%]") 
                    last_update_time = time.time()
        # ---------------------------------------------------

        process.wait()

        if process.returncode == 0 and os.path.exists(output_path):
            return output_path
        return None

    except Exception as e:
        logger.error(f"Помилка стиснення: {e}")
        return None
def instagram_download(id):
    L = instaloader.Instaloader()
    post = Post.from_shortcode(L.context, id)
    url = post.video_url
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open('video.mp4', 'wb') as file:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    file.write(chunk)

def download_instagram_post(url):
    # Ініціалізація
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True, 
        download_video_thumbnails=False,
        download_geotags=False, 
        download_comments=False,
        save_metadata=False,
        compress_json=False
    )

    # Витягуємо shortcode (ID поста)
    try:
        if "/reel/" in url:
            shortcode = url.split("/reel/")[1].split("/")[0]
        elif "/p/" in url:
            shortcode = url.split("/p/")[1].split("/")[0]
        elif "/tv/" in url:
            shortcode = url.split("/tv/")[1].split("/")[0]
        else:
            shortcode = url.strip("/").split("/")[-1]
    except:
        return {"error": "Не вдалося знайти ID поста в посиланні"}

    target_dir = ARTIFACTS_DIR / f"insta_{shortcode}"

    # Helper function for yt-dlp fallback
    def try_ytdl():
        logger.info(f"Trying to download Instagram post via yt-dlp: {url}")
        os.makedirs(target_dir, exist_ok=True)
        filename = f"video_{uuid.uuid4().hex}"
        ydl_opts = {
            'format': 'best[ext=mp4]/bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[vcodec^=avc1]/bestvideo+bestaudio/best',
            'outtmpl': str(target_dir / f"{filename}.%(ext)s"),
            'quiet': True,
            'noplaylist': True,
            'merge_output_format': 'mp4',
            'socket_timeout': 30,
            'retries': 5,
        }
        if config.has_section('Downloader'):
            cookies_from_browser = config.get('Downloader', 'instagram_cookies_from_browser', fallback=None)
            cookies_file = config.get('Downloader', 'instagram_cookies_file', fallback=None)
            if cookies_from_browser:
                ydl_opts['cookiesfrombrowser'] = cookies_from_browser
            elif cookies_file:
                ydl_opts['cookiefile'] = cookies_file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info)
            base, ext = os.path.splitext(downloaded_file)
            if not os.path.exists(downloaded_file) and os.path.exists(base + ".mp4"):
                downloaded_file = base + ".mp4"
            
            caption = info.get('description') or info.get('title') or "Instagram Post"
            author = info.get('uploader') or info.get('uploader_id') or 'Unknown'
            
            return {
                "type": "video",
                "file_path": downloaded_file,
                "caption": caption,
                "author": author,
                "folder_to_delete": target_dir
            }

    # Helper function for instaloader
    def try_instaloader():
        logger.info(f"Trying to download Instagram post via Instaloader: {shortcode}")
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=target_dir)
        
        caption = post.caption or "Instagram Post"
        author = post.owner_username
        
        all_files = glob.glob(os.path.join(target_dir, "*"))
        video_files = [f for f in all_files if f.endswith(".mp4")]
        image_files = [f for f in all_files if f.endswith(".jpg") or f.endswith(".png")]

        if video_files:
            main_video = max(video_files, key=os.path.getsize)
            return {
                "type": "video",
                "file_path": main_video,
                "caption": caption,
                "author": author,
                "folder_to_delete": target_dir
            }
        elif image_files:
            valid_images = [img for img in image_files if "_video_thumb" not in img]
            if not valid_images and image_files:
                return {
                    "type": "video",
                    "file_path": image_files[0],
                    "caption": caption,
                    "author": author,
                    "folder_to_delete": target_dir
                }
            else:
                return {
                    "type": "photo",
                    "media_group": valid_images,
                    "caption": caption,
                    "author": author,
                    "folder_to_delete": target_dir
                }
        else:
            raise Exception("No media files found after Instaloader download.")

    # Determine order based on URL type
    is_reel = "/reel/" in url or "/reels/" in url
    
    if is_reel:
        try:
            return try_ytdl()
        except Exception as e_ytdl:
            logger.warning(f"yt-dlp failed for reel: {e_ytdl}. Trying Instaloader...")
            try:
                return try_instaloader()
            except Exception as e_insta:
                logger.error(f"Both methods failed. Instaloader error: {e_insta}")
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                return {"error": f"Insta Error: {str(e_insta)}"}
    else:
        try:
            return try_instaloader()
        except Exception as e_insta:
            logger.warning(f"Instaloader failed: {e_insta}. Trying yt-dlp fallback...")
            try:
                return try_ytdl()
            except Exception as e_ytdl:
                logger.error(f"Both methods failed. yt-dlp error: {e_ytdl}")
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                return {"error": f"Insta Error: {str(e_insta)}"}


def cleanup_insta_folder(folder_path):
    """Видаляє папку з файлами після відправки"""
    if folder_path and os.path.exists(folder_path):
        # Спочатку видаляємо всі .txt файли у папці
        try:
            txt_files = glob.glob(os.path.join(folder_path, "*.txt"))
            logger.info(f"Видаляємо .txt файли: {txt_files}")
            for txt_file in txt_files:
                logger.info(f"Видаляємо файл: {txt_file}")
                os.remove(txt_file)
        except Exception as e:
            logger.warning(f"Не вдалося видалити .txt файли: {e}")
        
        # Потім видаляємо саму папку
        shutil.rmtree(folder_path, ignore_errors=False)

def download_video_local(url: str):
    """
    Скачує відео локально за допомогою yt-dlp.
    Повертає словник з шляхом до файлу та описом.
    """

    # Генеруємо унікальне ім'я файлу, щоб уникнути конфліктів при одночасному скачуванні
    filename = f"download_{uuid.uuid4().hex}"
    
    ydl_opts = {
        'format': 'best[ext=mp4]/bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[vcodec^=avc1]/bestvideo+bestaudio/best',  # Найкраща якість
        'outtmpl': f'{ARTIFACTS_DIR}/{filename}.%(ext)s',     # Шаблон імені файлу
        'quiet': True,                         # Менше сміття в логах
        'noplaylist': True,                    # Тільки одне відео, не плейлист
        'merge_output_format': 'mp4',          # Завжди намагатися робити mp4
        'socket_timeout': 30,    # Чекати відповіді від сервера до 30 секунд
        'retries': 10,           # Кількість спроб при помилці мережі
        'fragment_retries': 10,
        # Для TikTok/Insta іноді потрібні хедери, yt-dlp зазвичай справляється,
        # але іноді краще додати user-agent (опціонально)
    }
    if config.has_section('Downloader'):
        cookies_from_browser = config.get('Downloader', 'instagram_cookies_from_browser', fallback=None)
        cookies_file = config.get('Downloader', 'instagram_cookies_file', fallback=None)
        if cookies_from_browser:
            ydl_opts['cookiesfrombrowser'] = cookies_from_browser
        elif cookies_file:
            ydl_opts['cookiefile'] = cookies_file

    try:
        logger.info(f"Починаємо завантаження відео з URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. Отримуємо інфо (щоб знати автора і опис)
            info = ydl.extract_info(url, download=True) # download=True зразу качає
            logger.info(f"Відео успішно завантажено: {info.get('title', 'Без назви')}")
            
            # Отримуємо шлях до скачаного файлу
            # yt-dlp може змінити розширення, тому шукаємо файл
            downloaded_file = ydl.prepare_filename(info)
            
            # Якщо було merge (відео+аудіо), файл може мати .mp4, а prepare повертає .webm
            # Перевіряємо фактичний файл
            base, ext = os.path.splitext(downloaded_file)
            if not os.path.exists(downloaded_file) and os.path.exists(base + ".mp4"):
                downloaded_file = base + ".mp4"

            full_desc = info.get('description') or info.get('title') or "Без опису"
            author = info.get('uploader') or info.get('uploader_id') or 'Unknown'
            duration = info.get('duration', 0)
            result = {
                "type": "video",
                "file_path": downloaded_file,
                "caption": full_desc,
                "author": author,
                "duration": duration,
                "error": None
            }
            logger.info(f"Підготовлено результат для відео: {downloaded_file}")
            return result

    except Exception as e:
        logger.error(f"Помилка при завантаженні відео: {str(e)}")
        return {"error": str(e)}

def cleanup_file(path):
    """Видаляє файл після відправки"""
    if os.path.exists(path):
        os.remove(path)
