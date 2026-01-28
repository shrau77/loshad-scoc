import os
import asyncio
import aiohttp
import re
import logging
import random

# --- CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Cleaner")

INPUT_FILE = "verified_ru.txt"
BACKUP_FILE = "verified_ru_backup.txt"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

# --- PRE-FILTERS (Чтобы не качать мусор) ---
# Расширения, которые мы игнорируем сразу
SKIP_EXTENSIONS = {'.sh', '.md', '.py', '.jpg', '.png', '.gif', '.svg', '.zip', '.tar.gz'}
# Слова в имени файла, которые нам не нужны
SKIP_KEYWORDS = {'readme', 'install', 'tutorial', 'instruction', 'changelog', 'license'}

def get_random_header():
    return {"User-Agent": random.choice(USER_AGENTS)}

def should_skip_url(url):
    """Проверяет URL перед скачиванием."""
    # 1. Проверка расширения
    parsed = url.lower()
    for ext in SKIP_EXTENSIONS:
        if parsed.endswith(ext):
            return True, f"Skipped extension: {ext}"
    
    # 2. Проверка ключевых слов в имени файла
    # Берем последнюю часть URL (имя файла)
    filename = url.split('/')[-1]
    for kw in SKIP_KEYWORDS:
        if kw in filename.lower():
            return True, f"Skipped keyword: {kw}"
            
    return False, ""

def get_md5_head(content):
    import hashlib
    head = content[:500].encode('utf-8', errors='ignore')
    return hashlib.md5(head).hexdigest()

def is_valid_content(content):
    """Строгая проверка содержимого."""
    # 1. Проверка на HTML (404 страницы)
    if "<!DOCTYPE html" in content or "<html>" in content.lower():
        return False, "HTML Page (likely 404)"
        
    # 2. Проверка на мусорные домены
    BAD_DOMAINS = ['.ir', 'zula.ir']
    if any(d in content for d in BAD_DOMAINS): 
        return False, "Bad Domain found"
        
    # 3. Поиск VLESS ссылок
    vless_links = re.findall(r'vless://[^\s<>"]+', content)
    if not vless_links:
        return False, "No VLESS links found"
    
    valid_count = 0
    BLACK_SNI = ['google.com', 'youtube.com', 'pornhub', 'bet', 'casino']
    
    for link in vless_links:
        if "security=reality" not in link and "type=grpc" not in link: continue
        if any(b in link for b in BLACK_SNI): continue
        
        # Простая проверка на заглушки
        if any(ph in link for ph in ['uuid', 'server', 'example.com', '1.1.1.1']): continue
        
        valid_count += 1

    if valid_count == 0:
        return False, "No valid Reality configs"
    
    return True, f"Found {valid_count} nodes"

# --- CLEANER CORE ---

async def check_url(session, url):
    # Предварительная фильтрация
    skip, reason = should_skip_url(url)
    if skip:
        return False, reason

    try:
        async with session.get(url, headers=get_random_header(), timeout=8) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            
            content = await resp.text(errors='ignore')
            
            if len(content) < 50:
                return False, "Too small content"
                
            return is_valid_content(content)
            
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)

async def main():
    if not os.path.exists(INPUT_FILE):
        logger.error(f"File {INPUT_FILE} not found!")
        return

    # 1. Чтение
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
    
    logger.info(f"🛁 Starting genocide for {len(urls)} URLs...")
    
    # Бэкап
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(urls))
    logger.info(f"📦 Backup saved to {BACKUP_FILE}")

    survivors = []
    seen_hashes = set()
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i, url in enumerate(urls):
            # Проверяем URL до запроса (экономия времени)
            if should_skip_url(url)[0]:
                logger.info(f"  ⚡ [SKIP] {url.split('/')[-1]}...")
                continue

            task = check_url(session, url)
            tasks.append((i, url, task))
            
            # Пачки
            if len(tasks) >= 20 or i == len(urls) - 1:
                results = await asyncio.gather(*[t[2] for t in tasks])
                
                for idx, (orig_i, url, _) in enumerate(tasks):
                    is_alive, reason = results[idx]
                    
                    if is_alive:
                        # Дедупликация по хешу (чтобы не держать 5 версий одного файла)
                        # Но для этого нужен контент, а мы его уже скачали внутри is_valid_content...
                        # Чтобы не качать 2 раза, is_valid_content должна вернуть хеш или контент.
                        # Упростим: просто проверяем живость. 
                        # Если нужна дедупликация содержимого, надо переписать логику сохранения контента.
                        
                        # Считаем, что если alive - он уникальный по URL
                        survivors.append(url)
                    else:
                        logger.info(f"  ❌ [{orig_i+1}] KILLED: {url[:50]}... ({reason})")
                
                tasks = []
                await asyncio.sleep(1)

    # 3. Запись
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        for url in survivors:
            f.write(url + "\n")

    killed = len(urls) - len(survivors)
    logger.info("="*40)
    logger.info(f"🪦 GENOCIDE COMPLETED:")
    logger.info(f"  Before: {len(urls)}")
    logger.info(f"  Killed:  {killed}")
    logger.info(f"  Alive:   {len(survivors)}")
    logger.info("="*40)

if __name__ == "__main__":
    asyncio.run(main()) 
