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
import urllib.parse
from datetime import datetime

# --- CONFIGURATION & LOGGING ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VPNScout")

# API Keys
GITHUB_TOKENS = [t.strip() for t in os.getenv("GITHUB_TOKEN", "").split(",") if t.strip()]
HF_TOKEN = os.getenv("HF_TOKEN")

# Headers & API
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Limits
CONCURRENCY_LIMIT = 40       # Количество потоков
RECURSION_DEPTH = 1          # Глубина "матрешки"
AI_LIMIT = 3                 # Лимит запросов к ИИ
MAX_RETRIES = 3

# User Agents (Rotation)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
]

# --- DORKS (HYBRID COLLECTION) ---
SEARCH_QUERIES = [
    # --- GOLDEN (High Value Targets) ---
    'filename:whitelist.txt "vless" "reality"',
    'filename:config.txt "fingerprint: chrome" "publicKey"',
    'filename:nodes.txt "vless" "reality" -iran',
    'filename:sub.txt "vless" "reality"',
    'path:**/githubmirror/** "vless-secure"',
    'path:**/subscriptions/** "main-sub.txt"',
    '"XTLS-Reality" "WHITE-LIST" extension:txt',
    '"goida" OR "kerosin" OR "kizyak" OR "rjsxrd" vless',
    '"s3c3.001.gpucloud.ru" OR "storage.yandexcloud.net" OR "vkcloud-storage.ru"',
    
    # --- STANDARD (Volume Search) ---
    "vless reality whitelist extension:txt",
    "vless reality whitelist extension:json",
    "filename:nodes.txt vless reality",
    "filename:config.json security=reality",
    "vless reality gosuslugi",
    "vless reality yandex",
    "security=reality fp=chrome",
    "vless sub RU extension:txt"
]

# --- DATA LISTS (FULL SET) ---

# 1. S3 & Cloud Storage Logic
S3_DOMAINS = ["storage.yandexcloud.net", "vkcloud-storage.ru", "gpucloud.ru", "object.pscloud.io"]
S3_COMMON_FILES = ["config.txt", "sub.txt", "vless.txt", "nodes.txt", "list.txt", "vpn.txt", "reality.txt", "1.txt", "sub"]

# 2. Hard Block (Arabs, Iran, Spam)
BAD_DOMAINS = ['.ir', 'zula.ir', 'mci.ir', 'arvancloud', 'derp', 'mobinnet', 'shatel', '.cn', '.pk', '.af', '.sy', '.sa']
ARABIC_REGEX = re.compile(r'[\u0600-\u06FF]')

# 3. Trash SNI (Реклама, Порно, Трекеры) - ПОЛНЫЙ СПИСОК
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
    'test', 'localhost', '127.0.0.1', 'workers.dev'
]

# 4. White List (RU Boost) - ВЕРНУЛ СПИСОК
WHITE_SNI = [
    "gosuslugi.ru", "yandex.ru", "vk.com", "mail.ru", "ozon.ru", "wildberries.ru",
    "tbank.ru", "sberbank.ru", "mos.ru", "rutube.ru", "dzen.ru", "avito.ru",
    "kinopoisk.ru", "dns-shop.ru", "rzd.ru", "pochta.ru", "nalog.ru", "ru_target"
]

# Global Caches
CONTENT_HASHES = set()    # MD5 файлов (чтобы не качать одно и то же)
SEEN_FINGERPRINTS = set() # VLESS Fingerprints (чтобы не дублировать конфиги)
VISITED_URLS = set()      # URL (чтобы не ходить кругами)
RESULTS_BUFFER = []       # Итоговый список источников

# --- HELPER FUNCTIONS ---

def get_random_header():
    return {"User-Agent": random.choice(USER_AGENTS)}

def get_github_header():
    if not GITHUB_TOKENS: return {}
    token = random.choice(GITHUB_TOKENS)
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

def get_md5_head(content):
    """Хеш первых 500 байт для быстрой дедупликации файлов."""
    head = content[:500].encode('utf-8', errors='ignore')
    return hashlib.md5(head).hexdigest()

def extract_vless_fingerprint(vless_link):
    """Уникальный отпечаток конфига (UUID + Key)."""
    try:
        pattern = r'vless://(?P<uuid>[a-zA-Z0-9\-]+)@.*?(?:\?|&)(?:pbk|publickey)=(?P<pbk>[a-zA-Z0-9%\-\_]+)'
        match = re.search(pattern, vless_link, re.IGNORECASE)
        if match:
            return f"{match.group('uuid')}:{match.group('pbk')}"
        match_simple = re.search(r'vless://(?P<uuid>[a-zA-Z0-9\-]+)@(?P<host>[^:]+)', vless_link)
        if match_simple:
            return f"{match_simple.group('uuid')}:{match_simple.group('host')}"
    except: pass
    return None

def generate_variations(url):
    """Генерация инкрементальных ссылок и S3 путей (Auto-Discovery)."""
    variations = set()
    
    # 1. Numeric Increment (node1.txt -> node2.txt)
    match = re.search(r'(\d+)\.(txt|json|yaml|conf|sub)$', url)
    if match:
        base_num = int(match.group(1))
        ext = match.group(2)
        prefix = url[:match.start(1)]
        start = 1
        end = 50 
        if base_num > 50: end = base_num + 10
        for i in range(start, end + 1):
            if i == base_num: continue
            variations.add(f"{prefix}{i}.{ext}")

    # 2. S3 Brute-force
    if any(d in url for d in S3_DOMAINS):
        parts = url.split('/')
        if len(parts) > 3:
            base_path = "/".join(parts[:-1])
            for filename in S3_COMMON_FILES:
                variations.add(f"{base_path}/{filename}")
    return list(variations)

def convert_to_raw(url):
    if "raw.githubusercontent.com" in url or "gist.githubusercontent.com" in url: return url
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    if "gist.github.com" in url: return url + "/raw"
    return url

# --- SEARCH ENGINES ---

async def search_github_safe(session):
    found = set()
    logger.info(f"🔍 [GitHub] Запуск поиска по {len(SEARCH_QUERIES)} запросам...")
    
    for i, query in enumerate(SEARCH_QUERIES):
        page = 1
        while page <= 2:
            url = f"https://api.github.com/search/code?q={query}&sort=indexed&order=desc&per_page=30&page={page}"
            try:
                async with session.get(url, headers=get_github_header()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("items", [])
                        for item in items:
                            found.add((convert_to_raw(item['html_url']), f"dork: {query[:20]}..."))
                        logger.info(f"   [{resp.status}] Query '{query[:30]}...' (p{page}): +{len(items)} файлов")
                        page += 1
                        await asyncio.sleep(5)
                    elif resp.status == 403 or resp.status == 429:
                        reset_time = resp.headers.get("X-RateLimit-Reset")
                        wait_time = 60
                        if reset_time: wait_time = max(10, int(reset_time) - int(time.time()))
                        logger.warning(f"🛑 GitHub Rate Limit. Cooling down for {wait_time}s...")
                        await asyncio.sleep(wait_time + 5)
                        break
                    else:
                        logger.error(f"   GitHub Error {resp.status}")
                        break
            except Exception as e:
                logger.error(f"   Exception: {e}")
                break
    return list(found)

async def search_gists(session):
    found = set()
    logger.info("🔍 [Gist] Сканирование ленты...")
    try:
        url = "https://api.github.com/gists/public?per_page=60"
        async with session.get(url, headers=get_github_header()) as resp:
            if resp.status == 200:
                gists = await resp.json()
                keywords = ["vless", "reality", "sub", "free", "nodes", "v2ray"]
                for gist in gists:
                    files = gist.get("files", {})
                    desc = (gist.get("description") or "").lower()
                    if any(k in desc for k in keywords) or any(k in str(files).lower() for k in keywords):
                        for fname, fcal in files.items():
                            if fcal.get("raw_url"):
                                found.add((fcal["raw_url"], "source: gist"))
    except Exception: pass
    return list(found)

# --- AI ANALYSIS ---

async def ask_huggingface_async(session, snippet):
    if not HF_TOKEN: return "unknown"
    prompt = f"Analyze this VLESS config. Does it look like a Russian (RU) targeted VPN, a Global proxy, or Spam? Return one word: RU, Global, or Spam. Content: {snippet[:500]}"
    try:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": prompt, "parameters": {"max_new_tokens": 10, "return_full_text": False}}
        async with session.post(HF_API_URL, headers=headers, json=payload, timeout=5) as resp:
            if resp.status == 200:
                res = await resp.json()
                text = res[0]['generated_text'].lower() if isinstance(res, list) else ""
                if "ru" in text: return "ru"
                if "spam" in text: return "spam"
                if "global" in text: return "global"
    except: pass
    return "unknown"

# --- CORE LOGIC (Restored Function) ---

async def fetch_and_analyze(session, url, depth, ai_semaphore):
    """
    Возвращает: (status, valid_count, extra_data)
    Status: 'clean', 'aggregator', 'trash', 'duplicate'
    """
    if url in VISITED_URLS: return "duplicate", 0, None
    VISITED_URLS.add(url)

    try:
        headers = get_random_header()
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status != 200: return "dead", 0, None
            content = await resp.text(errors='ignore')

    except Exception:
        return "error", 0, None

    # 1. Dedup MD5 (File Level)
    content_hash = get_md5_head(content)
    if content_hash in CONTENT_HASHES: return "duplicate", 0, None
    CONTENT_HASHES.add(content_hash)

    # 2. Base64 Auto-Decode
    if "vless://" not in content and len(content) > 50:
        try:
            decoded = base64.b64decode(content).decode('utf-8', errors='ignore')
            if "vless://" in decoded: content = decoded
        except: pass

    # 3. Hard Block Filters
    if ARABIC_REGEX.search(content): return "trash", 0, "Arabic"
    if any(d in content for d in BAD_DOMAINS): return "trash", 0, "Bad Domain"

    # 4. Matryoshka Check (Aggregator)
    links_raw = re.findall(r'(https?://[^\s<>"]+)', content)
    subs = [l for l in links_raw if any(x in l for x in ['.txt', '.json', '.yaml', 'raw', 'gist'])]
    if len(subs) >= 3 and "vless://" not in content:
        if depth < RECURSION_DEPTH:
            return "aggregator", 0, subs
        return "trash", 0, "Max recursion"

    # 5. VLESS Parsing & Scoring
    vless_links = re.findall(r'vless://[^\s<>"]+', content)
    valid_count = 0
    white_hits = 0
    
    for link in vless_links:
        # Strict Reality Check
        if "security=reality" not in link and "type=grpc" not in link: continue
        
        # Blacklist SNI Check
        if any(b in link for b in BLACK_SNI): continue
        
        # Whitelist SNI Check (RU Boost)
        if any(w in link for w in WHITE_SNI): white_hits += 1
        
        # Fingerprint Dedup (Config Level)
        fp = extract_vless_fingerprint(link)
        if fp and fp not in SEEN_FINGERPRINTS:
            SEEN_FINGERPRINTS.add(fp)
            valid_count += 1

    if valid_count == 0:
        return "trash", 0, "No valid VLESS"

    # 6. Classification Logic
    is_ru = False
    if white_hits > 0 or "Russia" in content or "ru_" in content:
        is_ru = True
    
    # AI Check for ambiguous cases
    if not is_ru and valid_count < 5:
        async with ai_semaphore:
            ai_verdict = await ask_huggingface_async(session, content)
            if ai_verdict == "ru": is_ru = True
            elif ai_verdict == "spam": return "trash", 0, "AI-Spam"

    tag = "RU" if is_ru else "GLOBAL"
    
    # 7. Auto-Discovery (Return variations)
    variations = generate_variations(url)
    
    return "clean", valid_count, (tag, variations)

# --- WORKER ---

async def worker(queue, session, ai_sem):
    while True:
        item = await queue.get()
        url, source_tag, depth = item
        
        status, count, data = await fetch_and_analyze(session, url, depth, ai_sem)
        
        if status == "clean":
            tag, variations = data
            
            # Save Source URL
            RESULTS_BUFFER.append(f"{url}")
            logger.info(f"✅ Found {count} nodes in {url} [{tag}]")
            
            # Spawn Variations (Auto-Discovery)
            if variations:
                for v_url in variations:
                    if v_url not in VISITED_URLS:
                        await queue.put((v_url, "source: brute-force", depth))
                        
        elif status == "aggregator":
            # Spawn Recursion
            for sub_url in data:
                if sub_url not in VISITED_URLS:
                    await queue.put((sub_url, "source: recursion", depth + 1))
                    
        elif status == "trash":
            # logger.debug(f"🗑️ Trash: {url} ({data})")
            pass
            
        queue.task_done()

# --- SMART MERGE ---

def smart_merge_and_save(new_urls):
    """Читает старый файл, добавляет новые уникальные, сохраняет."""
    filename = "verified_ru.txt"
    existing = set()
    
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): existing.add(line.strip())
    
    initial_count = len(existing)
    for url in new_urls:
        existing.add(url)
        
    added_count = len(existing) - initial_count
    
    with open(filename, "w", encoding="utf-8") as f:
        for url in sorted(list(existing)):
            f.write(url + "\n")
            
    return added_count, len(existing)

# --- MAIN ---

async def main():
    async with aiohttp.ClientSession() as session:
        queue = asyncio.Queue()
        ai_sem = asyncio.Semaphore(AI_LIMIT)

        # 1. Harvest
        gh_results = await search_github_safe(session)
        gist_results = await search_gists(session)
        
        for url, tag in gh_results + gist_results:
            queue.put_nowait((url, tag, 0))
            
        if queue.empty():
            logger.warning("No seeds found.")
            return

        # 2. Process
        workers = [asyncio.create_task(worker(queue, session, ai_sem)) for _ in range(CONCURRENCY_LIMIT)]
        await queue.join()
        for w in workers: w.cancel()

    # 3. Save & Merge
    if RESULTS_BUFFER:
        added, total = smart_merge_and_save(RESULTS_BUFFER)
        logger.info(f"🔥 DONE. Added: {added}, Total in Base: {total}")
        
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                 f.write(f"FOUND_COUNT={added}\n")

if __name__ == "__main__":
    start_ts = time.time()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    logger.info(f"Execution time: {round(time.time() - start_ts, 2)}s")
