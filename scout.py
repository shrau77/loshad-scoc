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
from datetime import datetime, timedelta

# --- CONFIGURATION & LOGGING ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VPNScout")

# API Keys
# Поддерживаемые форматы ввода:
#   - Через запятую: "token1,token2,token3"
#   - Через пробел: "token1 token2 token3"
#   - Через новую строку: "token1\ntoken2\ntoken3"
#   - Через точку с запятой: "token1;token2;token3"
# Также подхватывается GITHUB_TOKEN из GitHub Actions
_raw_tokens = os.getenv("GTA_TOKEN", "") or os.getenv("GITHUB_TOKEN", "")

# Универсальный парсер - разбиваем по любому разделителю
GITHUB_TOKENS = []
if _raw_tokens:
    # Заменяем все возможные разделители на запятую
    for sep in ['\n', '\r', ';', ' ', '\t']:
        _raw_tokens = _raw_tokens.replace(sep, ',')
    # Парсим и чистим
    GITHUB_TOKENS = [t.strip() for t in _raw_tokens.split(',') if t.strip()]

HF_TOKEN = os.getenv("HF_TOKEN")

# Headers & API
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Limits
CONCURRENCY_LIMIT = 40
RECURSION_DEPTH = 1
AI_LIMIT = 3
MAX_RETRIES = 3

# GitHub Anti-Ban Settings
# GitHub Search API ~30 req/min с токеном, ~10 req/min без токена
GITHUB_SEMAPHORE = asyncio.Semaphore(1)
GITHUB_DELAY = 3 if GITHUB_TOKENS else 7  # 3s с токеном, 7s без (безопасный режим)

# User Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
]

# --- DORKS (FULL MEAT LIST) ---
SEARCH_QUERIES = [
    # 1. Целевые имена файлов
    'filename:whitelist.txt OR filename:white.txt OR filename:wl.txt OR filename:white-list.txt',
    'filename:configs.txt OR filename:config.txt OR filename:conf.txt',
    'filename:sub.txt OR filename:subscription.txt OR filename:subs.txt',
    'filename:nodes.txt OR filename:v2ray.txt OR filename:vless.txt OR filename:reality.txt',
    'filename:ru.txt OR filename:russia.txt OR filename:ru_nodes.txt OR filename:runodes.txt',
    'filename:bypass.txt OR filename:antifilter.txt OR filename:antizapret.txt',
    'filename:vpn.txt OR filename:proxy.txt OR filename:proxies.txt',
    'filename:clients.txt OR filename:client.txt',
    'filename:nekoray.txt OR filename:nekobox.txt OR filename:v2rayng.txt',
    'filename:list.txt OR filename:links.txt OR filename:urls.txt',
    'filename:server.txt OR filename:servers.txt',
    'filename:good.txt OR filename:checked.txt OR filename:valid.txt',
    # NEW: Дополнительные имена файлов
    'filename:share.txt OR filename:shared.txt OR filename:public.txt',
    'filename:free_nodes.txt OR filename:freenodes.txt OR filename:free-nodes.txt',
    'filename:telegraf.txt OR filename:tg.txt OR filename:telegram.txt',
    'filename:output.txt OR filename:result.txt OR filename:results.txt',
    'filename:updated.txt OR filename:latest.txt OR filename:new.txt',
    'filename:db.txt OR filename:database.txt OR filename:data.txt',
    'filename:clash.txt OR filename:clash_meta.txt OR filename:singbox.txt OR filename:sing-box.txt',
    'filename:shadowsocks.txt OR filename:ss.txt OR filename:ssr.txt',
    'filename:port.txt OR filename:ports.txt',
    'filename:test.txt OR filename:testing.txt',

    # 2. Пути и расширения
    'extension:txt vless reality path:subscriptions',
    'extension:json security=reality',
    'extension:yaml reality',
    'path:**/v2ray/** reality',
    'path:**/vpn/** vless',
    'path:**/configs/** vless reality',
    'path:**/subscriptions/** vless',
    'path:**/bypass/** whitelist',
    'path:**/ru/** vless',
    'path:**/russia/** reality',
    'path:**/free/** vless',
    'path:**/proxy/** vless',
    'path:**/server/** vless',
    'path:**/nodes/** vless',
    'path:**/sub/** vless',
    # NEW: Дополнительные пути
    'path:**/xray/** reality',
    'path:**/sing-box/** vless',
    'path:**/singbox/** reality',
    'path:**/nekoray/** vless',
    'path:**/hiddify/** reality',
    'path:**/clash/** vless',
    'path:**/outline/** vless',
    'path:**/sharing/** vless',
    'path:**/backup/** vless',
    'path:**/backup/** reality',

    # 3. Контент (сигнатуры)
    '"security=reality" "fingerprint: chrome" "shortId"',
    '"security=reality" "serverNames" "publicKey"',
    '"vless://" reality "sni:"',
    '"v2ray" "outbounds" "realitySettings"',
    '"xtls" "flow: xtls-rprx-vision" "reality"',
    '"streamSettings" "realitySettings" "publicKey"',
    '"type": "vless" "security": "reality" "encryption": "none"',
    # NEW: Дополнительные сигнатуры
    '"vless://" "flow=xtls-rprx-vision"',
    '"vless://" "packetEncoding=xudp"',
    '"reality" "shortId" "spiderX"',
    '"inbounds" "reality" "listen"',
    '"vless" "grpc" "reality"',

    # 4. Популярные SNI (RU Targets)
    '"gosuslugi.ru" reality',
    '"yandex.ru" reality',
    '"vk.com" reality',
    '"mail.ru" reality',
    '"ozon.ru" reality',
    '"wildberries.ru" reality',
    '"sberbank.ru" reality',
    '"tinkoff.ru" reality',
    '"rzd.ru" reality',
    '"nalog.ru" reality',
    '"mos.ru" reality',
    '"rutube.ru" reality',
    '"dzen.ru" reality',
    '"kinopoisk.ru" reality',
    '"avito.ru" reality',
    # NEW: Расширенный список RU SNI
    '"leroymerlin.ru" reality',
    '"petrovich.ru" reality',
    '"detmir.ru" reality',
    '"mvideo.ru" reality',
    '"eldorado.ru" reality',
    '"citilink.ru" reality',
    '"dns-shop.ru" reality',
    '"sportmaster.ru" reality',
    '"lamoda.ru" reality',
    '"aliexpress.ru" reality',
    '"tbank.ru" reality',
    '"alfa.ru" OR "alfabank.ru" reality',
    '"vtb.ru" reality',
    '"gazprombank.ru" reality',
    '"raiffeisen.ru" reality',
    '"pochta.ru" reality',
    '"sber.ru" reality',
    '"taxi.yandex.ru" reality',
    '"ya.ru" reality',
    '"habr.ru" reality',
    '"hh.ru" reality',
    '"autoru.ru" OR "auto.ru" reality',
    '"cian.ru" reality',
    '"aviasales.ru" reality',
    '"tutu.ru" reality',
    '"gosuslugi" reality',
    '"edu.ru" reality',
    '"mytracker.ru" reality',
    '"kp.ru" reality',
    '"rbc.ru" reality',

    # 5. Облака (поиск упоминаний доменов)
    '"storage.yandexcloud.net" vless',
    '"vkcloud-storage.ru" vless',
    '"hb.bizmrg.com" vless',
    '"s3.timeweb.com" vless',
    '"digitaloceanspaces.com" vless',
    '"backblazeb2.com" vless',
    '"amazonaws.com" vless',
    # NEW: Дополнительные облака
    '"cloud.yandex.net" vless',
    '"object.pscloud.io" vless',
    '"s3pointer.ru" vless',
    '"selectel.ru" vless',
    '"mcs.mail.ru" vless',
    '"cloud.ru" vless',
    '"sbercloud.ru" vless',
    '"yandexcloud.net" reality',

    # 6. Темы и Keywords
    '"anti-rkn" OR "antirkn" OR "antizapret" vless',
    '"anti-filter" OR "antifilter" vless',
    '"bypass" "russia" "whitelist"',
    '"dpi bypass" OR "dpi evasion" vless',
    '"nekoray" "reality" extension:json',
    '"hiddify" "reality" extension:txt',
    '"v2rayng" "reality"',
    '"xray" "reality" "whitelist"',
    # NEW: Дополнительные темы
    '"vless" "free" "russia"',
    '"reality" "free" "ru"',
    '"подписка" "vless" extension:txt',
    '"vpn" "бесплатно" "reality"',
    '"ноды" "vless" "txt"',
    '"обход" "блокировок" vless',
    '"proxyrussia" OR "proxy-russia" vless',
    '"vpn4ru" OR "vpn-for-russia" vless',
    '"unblock" "russia" vless',

    # 7. NEW: Telegram-связанные дорки
    '"t.me/" "vless" extension:txt',
    '"telegram" "reality" "config"',
    '"tg://" "proxy" OR "vless"',
    '"t.me/" "proxy" "reality"',

    # 8. NEW: CI/CD и Docker (часто содержат конфиги)
    'filename:docker-compose.yml vless',
    'filename:Dockerfile vless',
    'path:.github/workflows vless',
    'extension:env vless reality',

    # 9. NEW: Base64 и закодированные конфиги
    'extension:b64 vless',
    'extension:base64 reality',
    '"eyJ" "vless" OR "reality"',  # Base64开头

    # 10. NEW: Специфичные форматы
    'extension:conf vless reality',
    'extension:cfg vless',
    'extension:ini vless reality'
]

# --- DATA LISTS ---

# 1. S3 Domains & Patterns - Расширенный список
S3_DOMAINS = [
    # Российские облака
    "storage.yandexcloud.net", "yandexcloud.net", "cloud.yandex.net",
    "vkcloud-storage.ru", "mcs.mail.ru",
    "hb.bizmrg.com", "object.pscloud.io", "s3pointer.ru",
    "s3.timeweb.com", "selectel.ru", "cloud.ru", "sbercloud.ru",
    # Международные облака
    "digitaloceanspaces.com", "backblazeb2.com", "amazonaws.com",
    "cloudflare.com", "wasabisys.com", "contabo.com"
]

# Регулярки для извлечения ссылок на облака из текста
S3_DOMAIN_PATTERNS = [
    # Российские облака
    r'https?://[a-zA-Z0-9.-]*storage\.yandexcloud\.net[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*yandexcloud\.net[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*cloud\.yandex\.net[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*vkcloud-storage\.ru[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*mcs\.mail\.ru[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*hb\.bizmrg\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*object\.pscloud\.io[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*s3pointer\.ru[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*s3\.timeweb\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*selectel\.ru[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*cloud\.ru[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*sbercloud\.ru[^\s"<>\)]*',
    # Международные облака
    r'https?://[a-zA-Z0-9.-]*digitaloceanspaces\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*backblazeb2\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*amazonaws\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*wasabisys\.com[^\s"<>\)]*',
    r'https?://[a-zA-Z0-9.-]*contabo\.com[^\s"<>\)]*',
]

S3_COMMON_FILES = [
    # Основные конфиги
    "configs.txt", "v2ray.txt", "xray.txt", "reality.txt", "vless.txt",
    "whitelist.txt", "wl.txt", "white.txt", "russia.txt", "ru_nodes.txt",
    "bypass.txt", "antifilter.txt", "antizapret.txt",
    # VPN форматы
    "free.txt", "proxy.txt", "socks.txt", "shadowsocks.txt", "ss.txt",
    "clash.txt", "clash.meta.txt", "clash_pro.txt", "singbox.txt", "sing-box.txt",
    "nekoray.txt", "nekobox.txt", "hiddify.txt", "outline.txt",
    # Подписки
    "sub.txt", "subs.txt", "sub1.txt", "sub2.txt", "sub3.txt", "subscription.txt",
    "urls.txt", "links.txt", "list.txt",
    # Данные
    "data.txt", "database.txt", "db.txt", "nodes.txt",
    "share.txt", "shared.txt", "public.txt", "free_nodes.txt",
    # Результаты сканирований
    "result.txt", "results.txt", "output.txt", "updated.txt", "latest.txt",
    # Нумерованные файлы
    "1.txt", "2.txt", "3.txt", "node.txt", "config.txt"
]

# 2. Hard Block
BAD_DOMAINS = [
    '.ir', 'zula.ir', 'mci.ir', 'arvancloud', 'derp', 'mobinnet', 'shatel',
    '.cn', '.pk', '.af', '.sy', '.sa'
]
ARABIC_REGEX = re.compile(r'[\u0600-\u06FF]')

# 3. Guide Keywords (Интеллектуальная фильтрация)
GUIDE_KEYWORDS_HARD = [
    'tutorial', 'how to', 'guide', 'install', 'instruction', 'manual',
    'readme', 'step 1', 'step 2', 'шаг', 'настройка', 'setting',
    'как настроить', 'инструкция', 'руководство', 'скачать приложение',
    'установка', 'запуск', 'обзор', 'review'
]

CONTENT_KEYWORDS_SOFT = [
    'donate', 'patreon', 'boosty', 'купить', 'цена', 'руб', 'доллар',
    't.me/', 'telegram', 'channel', 'подпишись', 'price', 'buy'
]

# 4. Black SNI (Trash)
BLACK_SNI = [
    'google.com', 'youtube.com', 'facebook.com', 'instagram.com', 'twitter.com',
    'cloudflare.com', 'amazon.com', 'microsoft.com', 'oracle.com', 'apple.com',
    'fuck.rkn', 'iran', 'cloud', 'doubleclick', 'adservice', 'analytics',
    'pornhub', 'xvideos', 'bet', 'casino', 'yahoo.com', 'azure.com',
    'worker', 'pages.dev', 'herokuapp', 'workers.dev', 'localhost', '127.0.0.1'
]

# 5. White SNI (RU Boost) - Расширенный список
WHITE_SNI = [
    # Государственные сервисы
    "gosuslugi.ru", "mos.ru", "nalog.ru", "pochta.ru", "rzd.ru", "gosuslugi", "edu.ru",
    # Банки и финансы
    "sberbank.ru", "sber.ru", "tinkoff.ru", "tbank.ru", "vtb.ru", "alfa.ru", "alfabank.ru",
    "raiffeisen.ru", "gazprombank.ru", "otpbank.ru", "pochtabank.ru", "qiwi.com",
    # E-commerce
    "yandex.ru", "ya.ru", "ozon.ru", "wildberries.ru", "aliexpress.ru", "avito.ru",
    "market.yandex.ru", "beru.ru", "lamoda.ru", "sportmaster.ru",
    # Социальные и медиа
    "vk.com", "mail.ru", "dzen.ru", "rutube.ru", "kinopoisk.ru", "ok.ru",
    "livejournal.ru", "rambler.ru",
    # Технологии и сервисы
    "habr.ru", "hh.ru", "autoru.ru", "auto.ru", "cian.ru", "aviasales.ru", "tutu.ru",
    # Ритейл
    "dns-shop.ru", "mvideo.ru", "eldorado.ru", "citilink.ru", "leroymerlin.ru",
    "petrovich.ru", "detmir.ru", "leroymerlin.ru",
    # Специальные метки
    "ru_target", "russia", ".ru"
]

# Global Caches & State
CONTENT_HASHES = set()
SEEN_FINGERPRINTS = set()
VISITED_URLS = set()
RESULTS_BUFFER_RU = []
RESULTS_BUFFER_POTENTIAL = []

# Statistics
stats = {
    "total_fetched": 0, "errors": 0, "trash": 0, "duplicate": 0,
    "clean_ru": 0, "clean_global": 0, "aggregators": 0
}

token_status = {}

# --- HELPER FUNCTIONS ---

def clean_url(url):
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

def get_random_header():
    return {"User-Agent": random.choice(USER_AGENTS)}

def get_best_github_header():
    """
    Возвращает кортеж (headers, token) если есть доступный токен.
    Возвращает (None, wait_time) если ВСЕ токены в бане.
    """
    if not GITHUB_TOKENS:
        return {}, None

    current_time = int(time.time())
    best_token = None
    min_reset_time = float('inf')

    for token in GITHUB_TOKENS:
        # Инициализация токена
        if token not in token_status:
            token_status[token] = {'reset_time': 0}

        # Если токен жив — берем его сразу
        if current_time >= token_status[token]['reset_time']:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            return headers, token
        
        # Иначе запоминаем, когда он освободится
        if token_status[token]['reset_time'] < min_reset_time:
            min_reset_time = token_status[token]['reset_time']
            best_token = token

    # Если мы тут — живых токенов нет. Считаем сколько ждать.
    wait_time = max(1, min_reset_time - current_time)
    return None, wait_time

def get_md5_head(content):
    head = content[:500].encode('utf-8', errors='ignore')
    return hashlib.md5(head).hexdigest()

def extract_vless_fingerprint(vless_link):
    try:
        pattern = r'vless://(?P<uuid>[a-zA-Z0-9\-]+)@.*?(?:\?|&)(?:pbk|publickey)=(?P<pbk>[a-zA-Z0-9%\-\_]+)'
        match = re.search(pattern, vless_link, re.IGNORECASE)
        if match:
            return f"{match.group('uuid')}:{match.group('pbk')}"
        
        match_simple = re.search(r'vless://(?P<uuid>[a-zA-Z0-9\-]+)@(?P<host>[^:]+)', vless_link)
        if match_simple:
            return f"{match_simple.group('uuid')}:{match_simple.group('host')}"
    except:
        pass
    return None

def generate_variations(url):
    variations = set()
    # Numeric
    match = re.search(r'(\d+)\.(txt|json|yaml|conf|sub)$', url)
    if match:
        base_num = int(match.group(1))
        ext = match.group(2)
        prefix = url[:match.start(1)]
        start, end = 1, 50
        if base_num > 50:
            end = base_num + 10
        for i in range(start, end + 1):
            if i == base_num:
                continue
            variations.add(f"{prefix}{i}.{ext}")
            
    # S3 Brute
    if any(d in url for d in S3_DOMAINS):
        parts = url.split('/')
        if len(parts) > 3:
            base_path = "/".join(parts[:-1])
            for filename in S3_COMMON_FILES:
                variations.add(f"{base_path}/{filename}")
    return list(variations)

def convert_to_raw(url):
    if "raw.githubusercontent.com" in url or "gist.githubusercontent.com" in url:
        return url
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    if "gist.github.com" in url:
        return url + "/raw"
    return url

# --- SEARCH ENGINES ---

async def search_github_safe(session):
    found = set()
    mode = "Token" if GITHUB_TOKENS else "Public"
    logger.info(f"🔍 [GitHub] {mode} Mode: 1 req/{GITHUB_DELAY}s. Tokens: {len(GITHUB_TOKENS)}")
    
    for query in SEARCH_QUERIES:
        page = 1
        while page <= 1:
            # 1. Получаем токен
            headers, result = get_best_github_header()

            # Если вернулся None — значит ВСЕ токены в бане
            if headers is None:
                wait_time = result
                logger.warning(f"🛑 Все токены в бане. Ждем {int(wait_time)} сек...")
                await asyncio.sleep(wait_time + 5)
                continue # Пробуем снова достать токен

            token_used = result
            
            encoded_query = urllib.parse.quote(query)
            url = (
                f"https://api.github.com/search/code?q={encoded_query}"
                f"&sort=indexed&order=desc&per_page=30&page={page}"
            )
            
            try:
                # 2. Глобальная блокировка (Semaphore) + Задержка
                async with GITHUB_SEMAPHORE:
                    await asyncio.sleep(GITHUB_DELAY)
                    async with session.get(url, headers=headers, timeout=15) as resp:
                        
                        if resp.status == 200:
                            data = await resp.json()
                            items = data.get("items", [])
                            for item in items:
                                found.add((convert_to_raw(item['html_url']), f"dork: {query[:20]}..."))
                            
                            if items:
                                token_display = token_used[-4:] if token_used else "anon"
                                logger.info(f"   ✅ [...{token_display}] '{query[:25]}': +{len(items)}")
                            
                            page += 1
                            # Semaphore отпустится сам

                        elif resp.status == 403 or resp.status == 429:
                            reset_time = resp.headers.get("X-RateLimit-Reset")
                            wait_time = 3600 # Дефолт час
                            if reset_time:
                                wait_time = max(10, int(reset_time) - int(time.time()))
                            
                            # Ставим токен в бан (только если токен был)
                            if token_used:
                                token_status[token_used] = {'reset_time': int(time.time()) + wait_time}
                                logger.warning(f"🚫 Токен ...{token_used[-4:]} в бане на {int(wait_time/60)} мин. Смена...")
                            else:
                                # Без токена — ждём перед повтором (GitHub даёт ~10 req/min публично)
                                wait_time = 70  # Ждём чуть больше минуты
                                logger.warning(f"🚫 Rate limit (без токена). Ждём {wait_time} сек...")
                                await asyncio.sleep(wait_time)
                            
                            # НЕ выходим (семафор уже отпущен).
                            # Цикл перезапустится и возьмет другой токен.
                            continue
                        else:
                            # Другие ошибки (422, 404 и т.д.)
                            break

            except Exception as e:
                logger.error(f"Request error: {e}")
                # При ошибке сети прерываем этот запрос
                break
                
    return list(found)

async def search_gists(session):
    found = set()
    logger.info("🔍 [Gist] Сканирование ленты...")
    # Гисты тоже редко, но банят. Используем тот же семафор для безопасности.
    async with GITHUB_SEMAPHORE:
        await asyncio.sleep(GITHUB_DELAY)
        try:
            url = "https://api.github.com/gists/public?per_page=60"
            # Берем любой токен, если есть, иначе без него
            headers, _ = get_best_github_header()
            if headers is None: 
                headers = {} # Если токенов нет совсем

            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    gists = await resp.json()
                    keywords = ["vless", "reality", "sub", "free", "nodes", "v2ray", "whitelist", "bypass"]
                    for gist in gists:
                        files = gist.get("files", {})
                        desc = (gist.get("description") or "").lower()
                        if any(k in desc for k in keywords) or any(k in str(files).lower() for k in keywords):
                            for fname, fcal in files.items():
                                if fcal.get("raw_url"):
                                    found.add((fcal["raw_url"], "source: gist"))
        except Exception:
            pass
    return list(found)

# --- AI ANALYSIS ---

async def ask_huggingface_async(session, snippet):
    if not HF_TOKEN:
        return "unknown", "No Token"
    
    prompt = f"""Analyze this text. Is it a 'Guide/Tutorial', 'Spam', 'RU VPN Config', or 'Global VPN Config'?
Look for Russian SNI (gosuslugi, yandex, etc) or Russian text.
If it contains donation links but has configs, it is a Config, not spam.
Format: "Verdict: [RU/Global/Spam/Guide] Reason: [reason]"
Snippet: {snippet[:700]}"""
    
    try:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {
            "inputs": prompt,
            "parameters": {"max_new_tokens": 25, "return_full_text": False}
        }
        
        async with session.post(HF_API_URL, headers=headers, json=payload, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                text = res[0]['generated_text'].lower() if isinstance(res, list) else ""
                
                if "guide" in text or "tutorial" in text:
                    return "guide", "AI detected guide"
                if "spam" in text:
                    return "spam", "AI detected spam"
                if "ru" in text:
                    return "ru", "AI detected RU context"
                if "global" in text:
                    return "global", "AI says Global"
                
                return "unknown", text
    except:
        pass
    return "unknown", "Error"

# --- CORE LOGIC ---

async def fetch_and_analyze(session, url, depth, ai_semaphore):
    url_clean = clean_url(url)
    if url_clean in VISITED_URLS:
        return "duplicate", 0, None
    VISITED_URLS.add(url_clean)

    try:
        headers = get_random_header()
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                return "dead", 0, None
            content = await resp.text(errors='ignore')
    except:
        return "error", 0, None

    # 1. Dedup
    content_hash = get_md5_head(content)
    if content_hash in CONTENT_HASHES:
        return "duplicate", 0, None
    CONTENT_HASHES.add(content_hash)

    # 2. Multiline fix
    content = re.sub(r'(\n|\r)\s*(?=[&\?])', '', content)

    # 3. Base64
    if "vless://" not in content and len(content) > 50:
        try:
            decoded = base64.b64decode(content).decode('utf-8', errors='ignore')
            if "vless://" in decoded:
                content = decoded
        except:
            pass

    # 4. Hard Block (Arabic/Iran)
    if ARABIC_REGEX.search(content):
        return "trash", 0, "Arabic"
    if any(d in content for d in BAD_DOMAINS):
        return "trash", 0, "Bad Domain"

    # 5. Guide Heuristic
    content_lower = content.lower()
    hard_guide_hits = sum(1 for word in GUIDE_KEYWORDS_HARD if word in content_lower)
    
    if hard_guide_hits >= 2 and "vless://" not in content and "reality" not in content:
        return "trash", 0, "Pure Guide"

    # 6. Matryoshka & S3 Extraction
    links_raw = re.findall(r'(https?://[^\s<>"]+)', content)
    subs = [l for l in links_raw if any(x in l for x in ['.txt', '.json', '.yaml', 'raw', 'gist'])]
    
    # NEW: Extract S3 links from text
    for pattern in S3_DOMAIN_PATTERNS:
        s3_matches = re.findall(pattern, content)
        for s3_url in s3_matches:
            if s3_url not in subs:
                subs.append(s3_url)

    if len(subs) >= 3 and "vless://" not in content:
        if depth < RECURSION_DEPTH:
            return "aggregator", 0, subs
        return "trash", 0, "Max recursion"

    # 7. VLESS Parsing
    vless_links = re.findall(r'vless://[^\s<>"]+', content)
    valid_count = 0
    white_hits = 0
    
    for link in vless_links:
        if "security=reality" not in link and "type=grpc" not in link:
            continue
        if any(b in link for b in BLACK_SNI):
            continue
        if any(p in link for p in ['uuid', 'server', 'your-uuid', 'example.com']):
            continue
            
        uuid_match = re.search(r'(?P<uuid>[a-f0-9\-]{32,36})@', link, re.I)
        if uuid_match:
            uuid = uuid_match.group('uuid').replace('-', '')
            if len(uuid) != 32:
                continue 
            if len(set(uuid)) < 5:
                continue 
        
        if any(w in link for w in WHITE_SNI):
            white_hits += 1
        
        fp = extract_vless_fingerprint(link)
        if fp and fp not in SEEN_FINGERPRINTS:
            SEEN_FINGERPRINTS.add(fp)
            valid_count += 1

    if valid_count == 0:
        return "trash", 0, "No valid VLESS"

    # 8. Classification
    is_ru = False
    if white_hits > 0 or "Russia" in content or "ru_" in content:
        is_ru = True
    
    # AI Check
    verdict = "unknown"
    if not is_ru:
        async with ai_semaphore:
            verdict, reason = await ask_huggingface_async(session, content)
            if verdict == "ru":
                is_ru = True
            elif verdict == "guide":
                return "trash", 0, "AI-Guide"
            elif verdict == "spam":
                return "trash", 0, "AI-Spam"

    tag = "RU" if is_ru else "GLOBAL"
    
    # Variations
    variations = generate_variations(url_clean)
    hidden_subs = []
    if valid_count > 0:
        for link in links_raw:
            if any(x in link for x in ['/sub?', '/api/', 'download', 'get.php']):
                hidden_subs.append(link)
    
    return "clean", valid_count, (tag, variations + hidden_subs + subs)

# --- WORKER ---

async def worker(queue, session, ai_sem):
    while True:
        item = await queue.get()
        url, source_tag, depth = item
        status, count, data = await fetch_and_analyze(session, url, depth, ai_sem)
        stats["total_fetched"] += 1
        
        if status == "clean":
            tag, variations = data
            if tag == "RU":
                RESULTS_BUFFER_RU.append(url)
                stats["clean_ru"] += count
                logger.info(f"✅ [RU] Found {count} nodes: {url}")
            else:
                RESULTS_BUFFER_POTENTIAL.append(url)
                stats["clean_global"] += count
                logger.info(f"⚠️ [POTENTIAL] Found {count} nodes: {url}")

            if variations:
                for v_url in variations:
                    if clean_url(v_url) not in VISITED_URLS:
                        await queue.put((v_url, "source: recursion", depth))
                            
        elif status == "aggregator":
            stats["aggregators"] += 1
            for sub_url in data:
                if clean_url(sub_url) not in VISITED_URLS:
                    await queue.put((sub_url, "source: recursion", depth + 1))
                    
        elif status == "trash":
            stats["trash"] += 1
        elif status == "error":
            stats["errors"] += 1
            
        queue.task_done()

# --- SMART MERGE ---

def smart_merge_and_save(filename, new_urls):
    existing = set()
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    existing.add(line.strip())
                    
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
    # Детальный лог токенов
    if GITHUB_TOKENS:
        logger.info(f"🔑 Найдено токенов: {len(GITHUB_TOKENS)}")
        for i, t in enumerate(GITHUB_TOKENS, 1):
            logger.info(f"   Token #{i}: ...{t[-6:]} (len={len(t)})")
    else:
        logger.warning("⚠️ Токены не найдены! Работаем без авторизации (медленно)")
        logger.info(f"   GTA_TOKEN raw length: {len(os.getenv('GTA_TOKEN', ''))}")
        logger.info(f"   GITHUB_TOKEN raw length: {len(os.getenv('GITHUB_TOKEN', ''))}")
    
    async with aiohttp.ClientSession() as session:
        queue = asyncio.Queue()
        ai_sem = asyncio.Semaphore(AI_LIMIT)

        # Harvest
        gh_results = await search_github_safe(session)
        gist_results = await search_gists(session)
        
        for url, tag in gh_results + gist_results:
            queue.put_nowait((url, tag, 0))
            
        if queue.empty():
            logger.warning("No seeds found.")
            return

        # Process
        workers = [
            asyncio.create_task(worker(queue, session, ai_sem))
            for _ in range(CONCURRENCY_LIMIT)
        ]
        await queue.join()
        for w in workers:
            w.cancel()

    # Save
    if RESULTS_BUFFER_RU:
        added, total = smart_merge_and_save("verified_ru.txt", RESULTS_BUFFER_RU)
        logger.info(f"🔥 [RU] Saved {added} new sources. Total: {total}")

    if RESULTS_BUFFER_POTENTIAL:
        added_p, total_p = smart_merge_and_save("potential_mixed.txt", RESULTS_BUFFER_POTENTIAL)
        logger.info(f"🗂️ [MIXED] Saved {added_p} unverified sources. Total: {total_p}")

    # Stats
    logger.info("=" * 40)
    logger.info("📊 SESSION STATISTICS:")
    logger.info(f"  ✅ RU Nodes:    {stats['clean_ru']}")
    logger.info(f"  ⚠️  Potential:   {stats['clean_global']}")
    logger.info(f"  🗑️  Trash:       {stats['trash']}")
    logger.info(f"  🔗 Aggregators:  {stats['aggregators']}")
    logger.info("=" * 40)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass 
