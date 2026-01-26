import os
import requests
import time
import logging
import asyncio
import aiohttp
import base64
import binascii
import random
from datetime import datetime

# --- CONFIGURATION & LOGGING ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VPNScout")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
HEADERS_AUTH = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Limits
CONCURRENCY_LIMIT = 20
AI_LIMIT = 2

# User Agents Rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
]

# Dorks
SEARCH_QUERIES = [
    "vless reality whitelist extension:txt",
    "vless reality whitelist extension:json",
    "vless reality whitelist extension:yaml",
    "vless sub RU extension:txt",
    "security=reality xtls-rprx-vision extension:txt"
]

# --- DATA LISTS ---

BLACK_SNI = [
    'google.com', 'youtube.com', 'facebook.com', 'instagram.com', 'twitter.com',
    'cloudflare', 'amazon', 'microsoft', 'oracle', 'amazon.com', '147135001195.sec22org.com',
    'fuck.rkn', 'microsoft.com', 'iran', 'cloud', 'doubleclick', 'adservice', 'analytics',
    'osl-no-01.fromblancwithlove.com', 'pornhub', 'xvideos', 'iryiccyne.wwtraveler.com',
    'bet', 'casino', 'cdnjs.com', 'yahoo.com', 'azure.com', 'vpn', 'proxy', 'tunnel',
    'cloudflare.com', 'ams1.fromblancwithlove.com', 'chatgpt.com', 'github.com',
    'gos9.portal-guard.com', 'worker', 'pages.dev', 'herokuapp', 'excoino.com',
    'pizza', 'paypal.com', 'apple.com', 'tradingview.com', 'mynoderu.nodesecure.ru',
    'free', 'EbraSha', 'whatsapp.com', 'fonts', 'arvancloud', 'derp', '.ir', 'xyz',
    'dl1-uk-cdn.easy-upload.org'
]

BANNED_TLDS = [
    '.ir', '.cn', '.pk', '.af', '.sy', '.sa', '.vn', '.th', '.id', 
    '.br', '.ng', '.bd', '.ye', '.mn', '.kh', '.et', '.ar', '.in',
    '.kp', '.hk', '.tw', '.if', '.win', '.net', '.io', '.top', '.shop',
    '.eu', '.jp', '.icu', '.online', '.xyz', '.org', '.dev', '.site'
]

WHITE_SNI = [
    "www.unicreditbank.ru", "www.gazprombank.ru", "cdn.gpb.ru", "mkb.ru", "www.open.ru",
    "cobrowsing.tbank.ru", "cdn.rosbank.ru", "www.psbank.ru", "www.raiffeisen.ru",
    "www.rzd.ru", "st.gismeteo.st", "stat-api.gismeteo.net", "c.dns-shop.ru",
    "restapi.dns-shop.ru", "www.pochta.ru", "passport.pochta.ru", "chat-ct.pochta.ru",
    "www.x5.ru", "www.ivi.ru", "api2.ivi.ru", "hh.ru", "i.hh.ru", "hhcdn.ru",
    "sentry.hh.ru", "cpa.hh.ru", "www.kp.ru", "cdnn21.img.ria.ru", "lenta.ru",
    "sync.rambler.ru", "s.rbk.ru", "www.rbc.ru", "target.smi2.net", "hb-bidder.skcrtxr.com",
    "strm-spbmiran-07.strm.yandex.net", "pikabu.ru", "www.tutu.ru", "cdn1.tu-tu.ru",
    "api.apteka.ru", "static.apteka.ru", "images.apteka.ru", "scitylana.apteka.ru",
    "www.drom.ru", "c.rdrom.ru", "www.farpost.ru", "s11.auto.drom.ru", "i.rdrom.ru",
    "yummy.drom.ru", "www.drive2.ru", "lemanapro.ru", "stats.vk-portal.net",
    "sun6-21.userapi.com", "sun6-20.userapi.com", "avatars.mds.yandex.net",
    "queuev4.vk.com", "sun6-22.userapi.com", "sync.browser.yandex.net", "top-fwz1.mail.ru",
    "ad.mail.ru", "eh.vk.com", "akashi.vk-portal.net", "sun9-38.userapi.com",
    "st.ozone.ru", "ir.ozone.ru", "vt-1.ozone.ru", "io.ozone.ru", "ozone.ru",
    "xapi.ozon.ru", "strm-rad-23.strm.yandex.net", "online.sberbank.ru",
    "esa-res.online.sberbank.ru", "egress.yandex.net", "st.okcdn.ru", "rs.mail.ru",
    "counter.yadro.ru", "742231.ms.ok.ru", "splitter.wb.ru", "a.wb.ru",
    "user-geo-data.wildberries.ru", "banners-website.wildberries.ru",
    "chat-prod.wildberries.ru", "servicepipe.ru", "alfabank.ru", "statad.ru",
    "alfabank.servicecdn.ru", "alfabank.st", "ad.adriver.ru", "privacy-cs.mail.ru",
    "imgproxy.cdn-tinkoff.ru", "mddc.tinkoff.ru", "le.tbank.ru", "hrc.tbank.ru",
    "id.tbank.ru", "rap.skcrtxr.com", "eye.targetads.io", "px.adhigh.net", "nspk.ru",
    "sba.yandex.net", "identitystatic.mts.ru", "tag.a.mts.ru", "login.mts.ru",
    "serving.a.mts.ru", "cm.a.mts.ru", "login.vk.com", "api.a.mts.ru", "mtscdn.ru",
    "d5de4k0ri8jba7ucdbt6.apigw.yandexcloud.net", "moscow.megafon.ru", "api.mindbox.ru",
    "web-static.mindbox.ru", "storage.yandexcloud.net", "personalization-web-stable.mindbox.ru",
    "www.t2.ru", "beeline.api.flocktory.com", "static.beeline.ru", "moskva.beeline.ru",
    "wcm.weborama-tech.ru", "1013a--ma--8935--cp199.stbid.ru", "msk.t2.ru", "s3.t2.ru",
    "get4click.ru", "dzen.ru", "yastatic.net", "csp.yandex.net", "sntr.avito.ru",
    "yabro-wbplugin.edadeal.yandex.ru", "cdn.uxfeedback.ru", "goya.rutube.ru",
    "api.expf.ru", "fb-cdn.premier.one", "www.kinopoisk.ru", "widgets.kinopoisk.ru",
    "payment-widget.plus.kinopoisk.ru", "api.events.plus.yandex.net", "tns-counter.ru",
    "speller.yandex.net", "widgets.cbonds.ru", "www.magnit.com", "magnit-ru.injector.3ebra.net",
    "jsons.injector.3ebra.net", "2gis.ru", "d-assets.2gis.ru", "s1.bss.2gis.com",
    "www.tbank.ru", "strm-spbmiran-08.strm.yandex.net", "id.tbank.ru", "tmsg.tbank.ru",
    "vk.com", "www.wildberries.ru", "www.ozon.ru", "ok.ru", "yandex.ru",
    "epp.genproc.gov.ru", "duma.gov.ru", "alfabank.ru", "pochta.ru", "честныйзнак.рф",
    "moskva.taximaxim.ru", "2gis.ru", "tutu.ru", "rzd.ru", "rambler.ru",
    "lenta.ru", "gazeta.ru", "rbc.ru", "kp.ru", "government.ru",
    "st.ozone.ru", "disk.yandex.ru", "api.mindbox.ru", 
    "egress.yandex.net", "sba.yandex.net", "goya.rutube.ru", 
    "kremlin.ru", "sun6-22.userapi.com", "pptest.userapi.com", "sun9-101.userapi.com", "travel.yandex.ru",
    "trk.mail.ru", "1l-api.mail.ru", "m.47news.ru", "crowdtest.payment-widget-smarttv.plus.tst.kinopoisk.ru", "external-api.mediabilling.kinopoisk.ru",
    "external-api.plus.kinopoisk.ru", "graphql-web.kinopoisk.ru", "graphql.kinopoisk.ru", "1l.mail.ru", "tickets.widget.kinopoisk.ru",
    "st.kinopoisk.ru", "quiz.kinopoisk.ru", "payment-widget.kinopoisk.ru", "payment-widget-smarttv.plus.kinopoisk.ru", "oneclick-payment.kinopoisk.ru",
    "microapps.kinopoisk.ru", "ma.kinopoisk.ru", "hd.kinopoisk.ru", "crowdtest.payment-widget.plus.tst.kinopoisk.ru", "api.plus.kinopoisk.ru",
    "st-im.kinopoisk.ru", "1l-s2s.mail.ru", "sso.kinopoisk.ru", "touch.kinopoisk.ru", "1l-view.mail.ru",
    "1link.mail.ru", "1l-hit.mail.ru", "2021.mail.ru", "2018.mail.ru", "23feb.mail.ru",
    "2019.mail.ru", "2020.mail.ru", "1l-go.mail.ru", "8mar.mail.ru", "9may.mail.ru",
    "aa.mail.ru", "8march.mail.ru", "afisha.mail.ru", "agent.mail.ru", "amigo.mail.ru",
    "analytics.predict.mail.ru", "alpha4.minigames.mail.ru", "alpha3.minigames.mail.ru", "answer.mail.ru", "api.predict.mail.ru",
    "answers.mail.ru", "authdl.mail.ru", "av.mail.ru", "apps.research.mail.ru", "auto.mail.ru",
    "bb.mail.ru", "bender.mail.ru", "beko.dom.mail.ru", "azt.mail.ru", "bd.mail.ru",
    "autodiscover.corp.mail.ru", "aw.mail.ru", "beta.mail.ru", "biz.mail.ru", "blackfriday.mail.ru",
    "bitva.mail.ru", "blog.mail.ru", "bratva-mr.mail.ru", "browser.mail.ru", "calendar.mail.ru",
    "capsula.mail.ru", "cloud.mail.ru", "cdn.newyear.mail.ru", "cars.mail.ru", "code.mail.ru",
    "cobmo.mail.ru", "cobma.mail.ru", "cog.mail.ru", "cdn.connect.mail.ru", "cf.mail.ru",
    "comba.mail.ru", "compute.mail.ru", "codefest.mail.ru", "combu.mail.ru", "corp.mail.ru",
    "commba.mail.ru", "crazypanda.mail.ru", "ctlog.mail.ru", "cpg.money.mail.ru", "ctlog2023.mail.ru",
    "ctlog2024.mail.ru", "cto.mail.ru", "cups.mail.ru", "da.biz.mail.ru", "da-preprod.biz.mail.ru",
    "data.amigo.mail.ru", "dk.mail.ru", "dev1.mail.ru", "dev3.mail.ru", "dl.mail.ru",
    "deti.mail.ru", "dn.mail.ru", "dl.marusia.mail.ru", "doc.mail.ru", "dragonpals.mail.ru",
    "dom.mail.ru", "duck.mail.ru", "dev2.mail.ru", "e.mail.ru", "ds.mail.ru",
    "education.mail.ru", "dobro.mail.ru", "esc.predict.mail.ru", "et.mail.ru", "fe.mail.ru",
    "finance.mail.ru", "five.predict.mail.ru", "foto.mail.ru", "games-bamboo.mail.ru", "games-fisheye.mail.ru",
    "games.mail.ru", "genesis.mail.ru", "geo-apart.predict.mail.ru", "golos.mail.ru", "go.mail.ru",
    "gpb.finance.mail.ru", "gibdd.mail.ru", "health.mail.ru", "guns.mail.ru", "horo.mail.ru",
    "hs.mail.ru", "help.mcs.mail.ru", "imperia.mail.ru", "it.mail.ru", "internet.mail.ru",
    "infra.mail.ru", "hi-tech.mail.ru", "jd.mail.ru", "journey.mail.ru", "junior.mail.ru",
    "juggermobile.mail.ru", "kicker.mail.ru", "knights.mail.ru", "kino.mail.ru", "kingdomrift.mail.ru",
    "kobmo.mail.ru", "komba.mail.ru", "kobma.mail.ru", "kommba.mail.ru", "kombo.mail.ru",
    "kz.mcs.mail.ru", "konflikt.mail.ru", "kombu.mail.ru", "lady.mail.ru", "landing.mail.ru",
    "la.mail.ru", "legendofheroes.mail.ru", "legenda.mail.ru", "loa.mail.ru", "love.mail.ru",
    "lotro.mail.ru", "mailer.mail.ru", "mailexpress.mail.ru", "man.mail.ru", "maps.mail.ru",
    "marusia.mail.ru", "mcs.mail.ru", "media-golos.mail.ru", "mediapro.mail.ru", "merch-cpg.money.mail.ru",
    "miniapp.internal.myteam.mail.ru", "media.mail.ru", "mobfarm.mail.ru", "mowar.mail.ru", "mozilla.mail.ru",
    "my.mail.ru", "mosqa.mail.ru", "mking.mail.ru", "minigames.mail.ru", "myteam.mail.ru",
    "nebogame.mail.ru", "money.mail.ru", "net.mail.ru", "new.mail.ru", "newyear2018.mail.ru",
    "stats.vk-portal.net"
]

# --- HELPER FUNCTIONS ---

def get_random_header():
    """Возвращает заголовки с рандомным User-Agent."""
    return {"User-Agent": random.choice(USER_AGENTS)}

def convert_to_raw(html_url):
    return html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

def safe_decode(content):
    """
    Пытается декодировать Base64. 
    Если декодируется в читаемый текст — возвращает его.
    Если ошибка или бинарщина — возвращает оригинал.
    """
    try:
        # Убираем пробелы/переносы
        clean_content = content.replace("\n", "").replace(" ", "")
        decoded_bytes = base64.b64decode(clean_content)
        decoded_str = decoded_bytes.decode('utf-8')
        # Простая проверка: если более 90% символов печатные, это текст
        printable = sum(1 for c in decoded_str if c.isprintable())
        if printable / len(decoded_str) > 0.9:
            return decoded_str
    except (binascii.Error, UnicodeDecodeError):
        pass
    return content

def load_existing_files():
    """Загружает существующие файлы в сеты для удаления дубликатов."""
    files = ["verified_ru.txt", "verified_global.txt", "manual_review.txt"]
    data = {}
    for fname in files:
        if os.path.exists(fname):
            with open(fname, "r", encoding="utf-8") as f:
                data[fname] = set(line.strip() for line in f if line.strip())
        else:
            data[fname] = set()
    return data

def search_github_sync():
    found_urls = set()
    logger.info(">>> Запуск GitHub Search API...")
    
    with requests.Session() as session:
        session.headers.update(HEADERS_AUTH)
        for query in SEARCH_QUERIES:
            try:
                url = f"https://api.github.com/search/code?q={query}&sort=indexed&order=desc&per_page=15"
                resp = session.get(url)
                
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    for item in items:
                        found_urls.add(convert_to_raw(item['html_url']))
                    logger.info(f"Query '{query}': найдено {len(items)} новых файлов.")
                elif resp.status_code == 403:
                    logger.warning("GitHub API Rate Limit! Ждем 60 сек...")
                    time.sleep(60)
                else:
                    logger.error(f"Ошибка поиска {resp.status_code} для: {query}")
                
                time.sleep(3)
            except Exception as e:
                logger.error(f"Ошибка при поиске: {e}")
                
    return list(found_urls)

# --- ASYNC LOGIC ---

async def ask_huggingface_async(session, snippet):
    """
    Спрашивает AI. Обрабатывает многословные ответы.
    """
    if not HF_TOKEN:
        return "unknown"
        
    prompt = f"""
    Analyze this VPN config snippet. 
    Does it look like a Russian specific list (RU domains), a Global generic list, or Spam/Junk?
    Answer strictly with one of these words inside your sentence: 'Global', 'RU', or 'Spam'.
    Snippet: {snippet[:1000]}
    """
    
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 50, "return_full_text": False}
    }
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    try:
        async with session.post(HF_API_URL, headers=headers, json=payload, timeout=15) as resp:
            if resp.status == 200:
                result = await resp.json()
                if isinstance(result, list) and 'generated_text' in result[0]:
                    answer = result[0]['generated_text'].lower()
                    # Ищем ключевые слова в ответе
                    if "spam" in answer: return "spam"
                    if "ru" in answer or "russian" in answer: return "ru"
                    if "global" in answer: return "global"
    except Exception as e:
        logger.error(f"AI Error: {e}")
    
    return "unknown"

async def process_url(session, url, semaphore, ai_semaphore):
    async with semaphore:
        try:
            headers = get_random_header()
            headers['Range'] = 'bytes=0-20480' # 20KB для надежности
            
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status not in [200, 206]:
                    logger.warning(f"Dead link ({resp.status}): {url}")
                    return None
                
                raw_content = await resp.text()
                
            # 1. Base64 Decode Check
            content = safe_decode(raw_content)
            
            # 2. Logic Check
            total_len = len(content)
            if total_len < 50: return None
            
            bad_count = 0
            white_count = 0
            reasons = []
            
            # Подсчет очков
            if any(tld in content for tld in BANNED_TLDS):
                bad_count += 2
                reasons.append("Banned TLDs")
            
            for sni in BLACK_SNI:
                if sni in content: 
                    bad_count += 1
            
            for sni in WHITE_SNI:
                if sni in content: 
                    white_count += 1
            
            est_lines = max(1, total_len / 100)
            bad_ratio = bad_count / est_lines
            
            # Bonus
            if white_count > 0:
                bad_ratio *= 0.5
                reasons.append("White SNI Boost")

            # Classification
            if bad_ratio < 0.3:
                # Если чисто, решаем RU или Global по наличию белых SNI или .ru
                if white_count > 0 or ".ru" in content:
                    logger.info(f"✅ Verified RU: {url}")
                    return ("ru", url)
                else:
                    logger.info(f"🌍 Verified Global: {url}")
                    return ("global", url)

            elif bad_ratio > 0.8:
                reason_str = ", ".join(reasons) if reasons else "High keyword density"
                logger.info(f"🗑️ Trash ({int(bad_ratio*100)}% bad - {reason_str}): {url}")
                return None
            
            else:
                # Suspect -> AI
                logger.info(f"🤔 Suspect ({int(bad_ratio*100)}% bad). Asking AI...")
                async with ai_semaphore:
                    verdict = await ask_huggingface_async(session, content)
                
                if verdict == "ru":
                    logger.info(f"🤖 AI -> RU: {url}")
                    return ("ru", url)
                elif verdict == "global":
                    logger.info(f"🤖 AI -> Global: {url}")
                    return ("global", url)
                elif verdict == "spam":
                    logger.info(f"🤖 AI -> Spam: {url}")
                    return None
                else:
                    logger.info(f"⚠️ Manual: {url}")
                    return ("manual", url)

        except asyncio.TimeoutError:
            logger.warning(f"Timeout: {url}")
        except Exception as e:
            logger.error(f"Error processing {url}: {e}")
        return None

async def main_async(urls):
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    ai_semaphore = asyncio.Semaphore(AI_LIMIT)
    
    async with aiohttp.ClientSession() as session:
        tasks = [process_url(session, url, semaphore, ai_semaphore) for url in urls]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

# --- MAIN ---

def main():
    start_time = time.time()
    
    # 1. Загрузка старых данных (Smart Merge)
    existing_data = load_existing_files()
    
    # 2. Сбор ссылок
    urls = search_github_sync()
    if not urls:
        logger.info("Новых ссылок не найдено.")
        return

    # 3. Асинхронная проверка
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(main_async(urls))
    
    # 4. Распределение результатов и удаление дубликатов
    new_ru = 0
    new_global = 0
    
    for status, url in results:
        if status == "ru":
            if url not in existing_data["verified_ru.txt"]:
                existing_data["verified_ru.txt"].add(url)
                new_ru += 1
        elif status == "global":
            if url not in existing_data["verified_global.txt"]:
                existing_data["verified_global.txt"].add(url)
                new_global += 1
        elif status == "manual":
            existing_data["manual_review.txt"].add(url)

    # 5. Сохранение
    for fname, data_set in existing_data.items():
        with open(fname, "w", encoding="utf-8") as f:
            for url in sorted(data_set):
                f.write(url + "\n")
                
    # 6. Output for Commit
    now = datetime.now().strftime("%d-%m-%Y %H:%M")
    total_new = new_ru + new_global
    commit_msg = f"Scout Update: {now} (+{new_ru} RU, +{new_global} Global)"
    
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"COMMIT_MSG={commit_msg}\n")
            
    logger.info(f"DONE. Time: {round(time.time() - start_time, 2)}s")
    logger.info(f"Stats: +{new_ru} RU, +{new_global} Global added.")

if __name__ == "__main__":
    main()
