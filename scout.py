import os
import re
import time
import json
import logging
import asyncio
import aiohttp
import base64
import hashlib
import random
from datetime import datetime

# --- CONFIGURATION & LOGGING ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VPNScout")

# API Keys
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

# Headers
HEADERS_GITHUB = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"} if GITHUB_TOKEN else {}
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Limits
CONCURRENCY_LIMIT = 20  # Потоков
RECURSION_DEPTH = 1     # Глубина матрешки
AI_LIMIT = 2            # Ограничение для HuggingFace

# User Agents (Rotation)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:121.0) Gecko/121.0 Firefox/121.0"
]

# --- DORKS (Search Queries) ---
SEARCH_QUERIES = [
    "vless reality whitelist extension:txt",
    "vless reality whitelist extension:json",
    "vless reality whitelist extension:yaml",
    "filename:nodes.txt vless reality",
    "filename:sub.txt vless reality",
    "filename:config.json security=reality",
    "vless reality gosuslugi",
    "vless reality yandex",
    "security=reality fp=chrome",
    "vless sub RU extension:txt"
]

# --- DATA LISTS (Full Set) ---

# 1. Жесткий бан (Арабы, Иран, Спам домены)
BAD_DOMAINS = ['.ir', 'zula.ir', 'mci.ir', 'arvancloud', 'derp', 'mobinnet', 'shatel', '.cn', '.pk', '.af', '.sy', '.sa']
ARABIC_REGEX = re.compile(r'[\u0600-\u06FF]')

# 2. Мусор (Реклама, Трекеры, Порно) - Вернул полный список
BLACK_SNI = [
    'google.com', 'youtube.com', 'facebook.com', 'instagram.com', 'twitter.com',
    'cloudflare', 'amazon', 'microsoft', 'oracle', 'amazon.com', '147135001195.sec22org.com',
    'fuck.rkn', 'microsoft.com', 'iran', 'cloud', 'doubleclick', 'adservice', 'analytics',
    'osl-no-01.fromblancwithlove.com', 'pornhub', 'xvideos', 'iryiccyne.wwtraveler.com',
    'bet', 'casino', 'cdnjs.com', 'yahoo.com', 'azure.com', 'vpn', 'proxy', 'tunnel',
    'cloudflare.com', 'ams1.fromblancwithlove.com', 'chatgpt.com', 'github.com',
    'gos9.portal-guard.com', 'worker', 'pages.dev', 'herokuapp', 'excoino.com',
    'pizza', 'paypal.com', 'apple.com', 'tradingview.com', 'mynoderu.nodesecure.ru',
    'free', 'EbraSha', 'whatsapp.com', 'fonts', 'dl1-uk-cdn.easy-upload.org',
    'test', 'localhost', '127.0.0.1'
]

# 3. Белый список (RU буст)
WHITE_SNI = [
    "gosuslugi.ru", "yandex.ru", "vk.com", "mail.ru", "ozon.ru", "wildberries.ru",
    "tbank.ru", "sberbank.ru", "mos.ru", "rutube.ru", "dzen.ru", "avito.ru",
    "kinopoisk.ru", "dns-shop.ru", "rzd.ru", "pochta.ru", "nalog.ru"
]

# Глобальные кэши
CONTENT_HASHES = set()
VISITED_URLS = set()

# --- HELPER FUNCTIONS ---

def get_random_header():
    return {"User-Agent": random.choice(USER_AGENTS)}

def get_md5_head(content):
    """Хеш первых 500 байт для дедупликации."""
    head = content[:500].encode('utf-8', errors='ignore')
    return hashlib.md5(head).hexdigest()

def convert_to_raw(url):
    """Нормализация ссылок GitHub/Gist в Raw."""
    if "raw.githubusercontent.com" in url or "gist.githubusercontent.com" in url:
        return url
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    if "gist.github.com" in url:
        return url + "/raw"
    return url

# --- SEARCH ENGINES ---

async def search_github(session):
    found = set()
    logger.info("🔍 [GitHub] Запуск поиска...")
    for query in SEARCH_QUERIES:
        try:
            url = f"https://api.github.com/search/code?q={query}&sort=indexed&order=desc&per_page=15"
            async with session.get(url, headers=HEADERS_GITHUB) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    for item in items:
                        found.add(convert_to_raw(item['html_url']))
                    logger.info(f"   Query '{query}': +{len(items)} файлов")
                elif resp.status == 403:
                    logger.warning("   GitHub API Rate Limit. Sleep 30s...")
                    await asyncio.sleep(30)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"   GitHub Search Error: {e}")
    return list(found)

async def search_gists(session):
    found = set()
    logger.info("🔍 [Gist] Сканирование публичной ленты...")
    try:
        url = "https://api.github.com/gists/public?per_page=50"
        async with session.get(url, headers=HEADERS_GITHUB) as resp:
            if resp.status == 200:
                gists = await resp.json()
                keywords = ["vless", "reality", "sub", "nodes", "free", "v2ray"]
                
                for gist in gists:
                    desc = (gist.get("description") or "").lower()
                    files = gist.get("files", {})
                    
                    is_relevant = any(k in desc for k in keywords) or \
                                  any(any(k in fname.lower() for k in keywords) for fname in files)
                    
                    if is_relevant:
                        for fname, fcal in files.items():
                            raw_url = fcal.get("raw_url")
                            if raw_url: found.add(raw_url)
                logger.info(f"   Gist Scan: найдено {len(found)} кандидатов")
    except Exception as e:
        logger.error(f"   Gist Error: {e}")
    return list(found)

# --- AI ANALYSIS (Вернул) ---

async def ask_huggingface_async(session, snippet):
    """Спрашивает ИИ, если эвристика не уверена."""
    if not HF_TOKEN: return "unknown"
    
    prompt = f"""
    Analyze this VPN config list. 
    Does it contain mostly Russian services (RU), Global services, or Spam/Junk?
    Answer one word: 'Global', 'RU', or 'Spam'.
    Snippet: {snippet[:800]}
    """
    
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 20, "return_full_text": False}}
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    try:
        async with session.post(HF_API_URL, headers=headers, json=payload, timeout=10) as resp:
            if resp.status == 200:
                result = await resp.json()
                if isinstance(result, list) and 'generated_text' in result[0]:
                    ans = result[0]['generated_text'].lower()
                    if "spam" in ans: return "spam"
                    if "ru" in ans: return "ru"
                    if "global" in ans: return "global"
    except Exception:
        pass
    return "unknown"

# --- CORE LOGIC ---

async def fetch_and_analyze(session, url, depth, ai_semaphore):
    """
    Возвращает: (status, url, extra_data)
    Status: 'clean', 'aggregator', 'trash', 'duplicate', 'suspect'
    """
    if url in VISITED_URLS: return "duplicate", url, None
    VISITED_URLS.add(url)

    try:
        headers = get_random_header()
        headers['Range'] = 'bytes=0-20480' # 20KB
        
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status not in [200, 206]: return "dead", url, None
            raw_bytes = await resp.read()
            content = raw_bytes.decode('utf-8', errors='ignore')

    except Exception:
        return "error", url, None

    # 1. Dedup MD5
    content_hash = get_md5_head(content)
    if content_hash in CONTENT_HASHES: return "duplicate", url, None
    CONTENT_HASHES.add(content_hash)

    # 2. Base64 Auto-Decode
    if " " not in content.strip() and len(content) > 50:
        try:
            decoded = base64.b64decode(content).decode('utf-8', errors='ignore')
            if "vless://" in decoded:
                content = decoded
                # logger.info(f"🔓 Base64 Decoded: {url}")
        except: pass

    # 3. Matryoshka (Aggregator) Check
    links = re.findall(r'(https?://[^\s<>"]+|raw\.githubusercontent\.com[^\s<>"]+)', content)
    valid_links = [l for l in links if any(x in l for x in ['.txt', '.json', '.yaml', 'raw', 'gist'])]
    
    if len(valid_links) >= 5:
        if depth < RECURSION_DEPTH:
            return "aggregator", url, valid_links
        else:
            return "trash", url, "Max recursion"

    # 4. HARD BLOCK FILTERS
    
    # A. Arab/Iran
    if ARABIC_REGEX.search(content): return "trash", url, "Arabic text"
    for domain in BAD_DOMAINS:
        if domain in content: return "trash", url, f"Banned domain {domain}"

    # B. Cloudflare Workers strict check
    if 'workers.dev' in content and 'type=ws' in content and 'security=reality' not in content:
        return "trash", url, "Weak CF Worker"

    # 5. SCORING SYSTEM (The 3-Tier Filter)
    total_len = len(content)
    if total_len < 50: return "trash", url, "Empty"

    bad_count = 0
    white_count = 0
    
    # Считаем мусор (Blacklist SNI)
    for sni in BLACK_SNI:
        if sni in content: bad_count += 1
        
    # Считаем бонусы (White SNI + Cyrillic)
    for sni in WHITE_SNI:
        if sni in content: white_count += 1
    if re.search(r'[а-яА-Я]', content): white_count += 1
    
    # Эвристика
    est_lines = max(1, total_len / 150)
    bad_ratio = bad_count / est_lines
    
    # RU Boost
    if white_count > 0:
        bad_ratio *= 0.5 # Снижаем плохой рейтинг в 2 раза
        
    # LOGIC TREE
    final_decision = "trash"
    
    if "vless://" not in content:
        return "trash", url, "No VLESS"

    # TIER 1: CLEAN
    if bad_ratio < 0.3:
        if white_count > 0 or "Russia" in content:
            return "ru_targeted", url, None
        else:
            return "global", url, None
            
    # TIER 3: TRASH
    elif bad_ratio > 0.8:
        return "trash", url, f"High bad_ratio {round(bad_ratio, 2)}"
        
    # TIER 2: SUSPECT -> AI CHECK
    else:
        # logger.info(f"🤔 Suspect: {url}. Asking AI...")
        async with ai_semaphore:
            ai_verdict = await ask_huggingface_async(session, content)
            
        if ai_verdict == "ru": return "ru_targeted", url, "AI-Approved"
        if ai_verdict == "global": return "global", url, "AI-Approved"
        if ai_verdict == "spam": return "trash", url, "AI-Rejected"
        
        # Если ИИ тоже хз, кидаем в ручной
        return "manual", url, "AI-Unsure"

# --- WORKER ---

async def worker(queue, session, results, ai_semaphore):
    while True:
        item = await queue.get()
        url, depth = item
        
        status, proc_url, data = await fetch_and_analyze(session, url, depth, ai_semaphore)
        
        if status == "aggregator" and data:
            # logger.info(f"📦 Matryoshka: {proc_url}")
            for link in data:
                if link not in VISITED_URLS:
                    await queue.put((link, depth + 1))
                    
        elif status == "ru_targeted":
            logger.info(f"✅ [RU] {proc_url}")
            results.append(("ru", proc_url))
            
        elif status == "global":
            logger.info(f"🌍 [GL] {proc_url}")
            results.append(("global", proc_url))
            
        elif status == "manual":
            logger.info(f"⚠️ [Man] {proc_url}")
            results.append(("manual", proc_url))
            
        elif status == "trash":
            logger.info(f"🗑️ [Drop] {proc_url} -> {data}")
            
        queue.task_done()

# --- MAIN ---

async def main_async():
    async with aiohttp.ClientSession() as session:
        # Сбор
        g_urls = await search_github(session)
        gist_urls = await search_gists(session)
        all_urls = list(set(g_urls + gist_urls))
        
        if not all_urls:
            logger.info("Ссылки не найдены.")
            return []

        # Очередь
        queue = asyncio.Queue()
        for u in all_urls: queue.put_nowait((u, 0))
        
        results = []
        ai_sem = asyncio.Semaphore(AI_LIMIT)
        
        # Запуск воркеров
        tasks = [asyncio.create_task(worker(queue, session, results, ai_sem)) for _ in range(CONCURRENCY_LIMIT)]
        await queue.join()
        for t in tasks: t.cancel()
        
        return results

def smart_merge(new_results):
    """Слияние с существующими файлами."""
    files = {
        "verified_ru.txt": set(),
        "verified_global.txt": set(),
        "manual_review.txt": set()
    }
    
    # Чтение
    for fname in files:
        if os.path.exists(fname):
            with open(fname, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip(): files[fname].add(line.strip())
                    
    # Добавление
    added_ru, added_gl = 0, 0
    for tag, url in new_results:
        if tag == "ru":
            if url not in files["verified_ru.txt"]:
                files["verified_ru.txt"].add(url)
                added_ru += 1
        elif tag == "global":
            if url not in files["verified_global.txt"]:
                files["verified_global.txt"].add(url)
                added_gl += 1
        elif tag == "manual":
            files["manual_review.txt"].add(url)
            
    # Запись
    for fname, urls in files.items():
        with open(fname, "w", encoding="utf-8") as f:
            for u in sorted(urls): f.write(u + "\n")
            
    return added_ru, added_gl

if __name__ == "__main__":
    start = time.time()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    res = loop.run_until_complete(main_async())
    
    new_ru, new_gl = smart_merge(res)
    
    # Output для GitHub Actions
    now = datetime.now().strftime("%d-%m %H:%M")
    msg = f"Update: {now} (RU: +{new_ru}, GL: +{new_gl})"
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"COMMIT_MSG={msg}\n")
            
    logger.info(f"DONE. Time: {round(time.time() - start, 2)}s")
