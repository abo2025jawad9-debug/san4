import os
import time
import json
import uuid
import requests
import subprocess
import concurrent.futures
from datetime import datetime, timedelta
from dataclasses import dataclass
from pybit.unified_trading import HTTP

# ==========================================
# CONFIGURATION (الإعدادات)
# ==========================================

@dataclass
class Config:
    """فئة (Class) لتخزين إعدادات الاتصال الآمنة الخاصة بواجهة Bybit وتليجرام"""
    api_key: str = os.getenv('BYBIT_API_KEY')             # مفتاح الربط العام لمنصة Bybit
    secret: str = os.getenv('BYBIT_API_SECRET')           # المفتاح السري لمنصة Bybit
    telegram_token: str = os.getenv('TELEGRAM_TOKEN')     # التوكن الخاص ببوت التليجرام لإرسال الإشعارات
    telegram_chat_id: str = os.getenv('TELEGRAM_CHAT_ID') # معرف الدردشة الخاص بك على تليجرام لاستقبال الرسائل

cfg = Config()

API_KEY = cfg.api_key
API_SECRET = cfg.secret


# [التحديث الجديد]: قائمة العملات التي سيتداول عليها البوت (يمكنك إضافة أو حذف أي عملة)
SYMBOLS = ["AAVEUSDT", "SOLUSDT", "HYPEUSDT","CRCLXUSDT","BTCUSDT","ETHUSDT","BICOUSDT","WLDUSDT","INJUSDT","ENAUSDT","XRPUSDT","CCUSDT","BILLUSDT","DATAUSDT","MONUSDT","CAPUSDT","ADAUSDT","LITUSDT","LTCUSDT","BNBUSDT","XAUTUSDT","LINKUSDT","MNTUSDT","CAPUSDT","NYMUSDT"]

BUY_AMOUNT_USD = 5.00         # قيمة كل صفقة شراء بالدولار
TAKER_FEE_PERCENT = 0.002     # نسبة رسوم المنصة للطلبات المباشرة (Taker Fee)، عادة تكون 0.1%

# ================= إعدادات الاستراتيجية المئوية =================
TAKE_PROFIT_PCT = 0.00         # نسبة الربح الصافي المطلوب تحقيقها قبل البيع (مثال: 1.0% من إجمالي التكلفة)
PRICE_STEP_PCT = 1.0          # مسافة التعزيز كنسبة مئوية (البوت سينتظر هبوط السعر بنسبة 1.5% من آخر شراء ليعزز)
BUY_NEAR_24H_LOW_PCT = 0.08    # نسبة التسامح للشراء من قاع اليوم (يشتري فقط إذا كان السعر لا يرتفع بأكثر من 0.5% عن أدنى سعر في آخر 24 ساعة)

# ================= إعدادات النظام =================
JSON_FILE = 'sh.json'         # اسم الملف المحلي الذي سيتم حفظ سجل العمليات (Database) فيه
MAX_OPEN_POSITIONS = 2        # الحد الأقصى لعدد الصفقات المفتوحة (لكل عملة على حدة)
REBUY_WAIT_MINUTES = 1        # الحد الأدنى من الدقائق للانتظار بين صفقات الشراء لنفس العملة
SLEEP_SECONDS = 7             # وقت الاستراحة بالثواني بين كل دورة فحص للسوق (لتخفيف الضغط على واجهة API)
RUN_DURATION_HOURS = 5.8      # المدة الإجمالية لتشغيل السكربت بالساعات قبل الإغلاق التلقائي (يفيد في التحديثات وجدولة الخوادم)

PROXY_LIST = []               # قائمة ديناميكية تخزن عناوين البروكسي المجانية المسحوبة لتجنب حظر الـ IP
client = None                 # المتغير الذي سيحمل جلسة الاتصال (Session) بمنصة Bybit

# ================= بروكسيات =================

def fetch_free_proxies():
    """تقوم بجلب قوائم بروكسيات مجانية من عدة مصادر على الإنترنت وتخزينها في قائمة لتخطي الحظر الجغرافي أو قيود الطلبات"""
    proxies = []
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=elite",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    ]
    print("[PROXY] جَلْبُ قَائِمَةِ البُرُوكْسِي...")
    for source in sources:
        try:
            response = requests.get(source, timeout=15)
            if response.status_code == 200:
                lines = response.text.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if ':' in line and len(line) < 30:
                        proxy_url = f"http://{line}"
                        if proxy_url not in proxies:
                            proxies.append(proxy_url)
        except Exception:
            pass
    proxies = list(dict.fromkeys(proxies))
    print("[PROXY] إِجْمَالِيُّ مَا تَمَّ جَلْبُهُ: %d" % len(proxies))
    return proxies

def test_proxy(proxy_url):
    """تقوم باختبار البروكسي عن طريق محاولة الاتصال بخادم Bybit وقياس سرعة الاستجابة"""
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        start = time.time()
        response = requests.get("https://api.bybit.com/v5/market/time", proxies=proxies, timeout=3)
        if response.status_code == 200:
            latency = time.time() - start
            return latency
        return None
    except:
        return None

def get_best_proxy():
    """تستخدم المسارات المتعددة (Threads) لفحص البروكسيات المتاحة واختيار الأسرع منها للعمل"""
    global PROXY_LIST
    if not PROXY_LIST:
        PROXY_LIST = fetch_free_proxies()

    print("[PROXY] فَحْصُ %d بُرُوكْسِي..." % min(100, len(PROXY_LIST)))
    tested = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(test_proxy, p): p for p in PROXY_LIST[:100]}
        for future in concurrent.futures.as_completed(futures):
            proxy = futures[future]
            latency = future.result()
            if latency:
                tested.append((proxy, latency))
            else:
                if proxy in PROXY_LIST:
                    PROXY_LIST.remove(proxy)

    if not tested:
        print("[PROXY] لَا يُوجَدُ بُرُوكْسِي يَعْمَلُ! جَارِي إِعَادَةُ الجَلْبِ...")
        PROXY_LIST = []
        return None

    tested.sort(key=lambda x: x[1])
    best = tested[0]
    print("[PROXY] الأَفْضَلُ: %s (السُّرْعَةُ: %.2fs)" % (best[0], best[1]))
    return {"http": best[0], "https": best[0]}

def init_client_with_retries():
    """تقوم بإنشاء اتصال موثق بمنصة Bybit باستخدام أسرع بروكسي متوفر، مع محاولات إعادة اتصال في حال الفشل"""
    global client, PROXY_LIST

    while True:
        for attempt in range(1, 4):
            print("[INIT] مُحَاوَلَةُ الاِتِّصَالِ %d/3 بـ Bybit..." % attempt)
            proxy = get_best_proxy()
            if proxy is None:
                time.sleep(3)
                continue

            try:
                os.environ['HTTP_PROXY'] = proxy['http']
                os.environ['HTTPS_PROXY'] = proxy['https']

                client = HTTP(
                    testnet=False,
                    api_key=API_KEY,
                    api_secret=API_SECRET
                )
                
                client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
                print("[INIT] تَمَّ الاِتِّصَالُ بِنَجَاحٍ! البُرُوكْسِي: %s" % proxy['http'])
                return True
            except Exception as e:
                print("[INIT] تَمَّ رَفْضُ البُرُوكْسِي أَوْ حَدَثَ خَطَأٌ: %s" % e)
                if proxy['http'] in PROXY_LIST:
                    PROXY_LIST.remove(proxy['http'])
                
                os.environ.pop('HTTP_PROXY', None)
                os.environ.pop('HTTPS_PROXY', None)
            
            time.sleep(2)

        print("[INIT] فَشِلَتْ 3 مُحَاوَلَاتٍ. جَارِي إِعَادَةُ جَلْبِ البُرُوكْسِي...")
        PROXY_LIST = []
        time.sleep(5)

# ================= واتساب وإدارة الملفات =================

import requests
import urllib.parse

def send_whatsapp_message(message):
    """
    دالة لإرسال إشعارات الصفقات إلى واتساب عبر CallMeBot
    """
    phone_number = "967772490746"
    api_key = "9569018"
    
    encoded_message = urllib.parse.quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone_number}&text={encoded_message}&apikey={api_key}"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print("✅ تم إرسال إشعار الواتساب بنجاح.")
        else:
            print(f"⚠️ فشل إرسال الإشعار. كود الخطأ: {response.status_code}")
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الاتصال بخدمة واتساب: {e}")

def load_history():
    """تقرأ سجل الصفقات المحفوظة من ملف JSON وتسترجعها على شكل قاموس (Dictionary)"""
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                pass
    return {}

def save_history(history):
    """تقوم بحفظ سجل الصفقات المحدث إلى ملف JSON لضمان عدم ضياع البيانات عند إعادة التشغيل"""
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

def git_commit_and_push():
    """تقوم برفع ملف JSON المحدث إلى مستودع Git الخاص بك برمجياً للاحتفاظ بنسخة احتياطية من الصفقات"""
    for attempt in range(1, 4):
        try:
            subprocess.run(['git', '--work-tree=' + os.getcwd(), 'config', '--global', 'user.name', 'Bot'], check=True)
            subprocess.run(['git', '--work-tree=' + os.getcwd(), 'config', '--global', 'user.email', 'bot@bot.com'], check=True)
            subprocess.run(['git', '--work-tree=' + os.getcwd(), 'add', JSON_FILE], check=True)
            status = subprocess.run(['git', '--work-tree=' + os.getcwd(), 'diff', '--staged', '--quiet'])
            if status.returncode != 0:
                subprocess.run(['git', '--work-tree=' + os.getcwd(), 'commit', '-m', 'تَحْدِيثُ عَمَلِيَّاتِ التَّدَاوُلِ'], check=True)
                subprocess.run(['git', '--work-tree=' + os.getcwd(), 'push'], check=True)
            return True
        except Exception as e:
            print("[GIT] فَشِلَ الرَّفْعُ: %s" % e)
            time.sleep(2)
    return False

# ================= حسابات =================

def calculate_sell_thresholds(buy_price, qty, buy_fee_usd):
    """
    تحسب تكلفة الشراء، والرسوم المقدرة، وسعر التعادل الحقيقي الذي يغطي 
    نقص الكمية بسبب الرسوم، وتحسب سعر البيع المطلوب لتحقيق نسبة الربح المستهدفة.
    """
    buy_cost = buy_price * qty
    
    # حساب الكمية الصافية التقريبية بعد خصم رسوم المنصة في الشراء
    sellable_qty = qty * (1 - TAKER_FEE_PERCENT)
    
    # سعر التعادل الحقيقي: التكلفة الإجمالية مقسومة على الكمية الصافية مخصوماً منها رسوم البيع المستقبلية
    break_even = buy_cost / (sellable_qty * (1 - TAKER_FEE_PERCENT))
    
    # السعر المطلوب لتحقيق الربح الصافي
    min_profit_price = break_even * (1 + (TAKE_PROFIT_PCT / 100))

    return {
        "buy_cost": buy_cost,
        "buy_fee_usd": buy_fee_usd,
        "estimated_sell_fee": buy_cost * TAKER_FEE_PERCENT, # تقديري
        "total_fees": buy_fee_usd + (buy_cost * TAKER_FEE_PERCENT),
        "total_cost": buy_cost, 
        "break_even_price": break_even,
        "min_sell_price": min_profit_price
    }

# ================= عمليات السوق =================

def get_market_data(symbol):
    """تقوم بجلب السعر الحالي للسوق وأدنى سعر للعملة خلال الـ 24 ساعة الماضية"""
    try:
        res = client.get_tickers(category="spot", symbol=symbol)
        ticker = float(res['result']['list'][0]['lastPrice'])
        low_24h = float(res['result']['list'][0]['lowPrice24h'])
        print("[PRICE] [%s] السِّعْرُ الحَالِيُّ: %.2f | قَاعُ 24 سَاعَة: %.2f" % (symbol, ticker, low_24h))
        return ticker, low_24h
    except Exception as e:
        print("[PRICE] فَشَلٌ فِي جَلْبِ البَيَانَاتِ: %s" % e)
        return None, None

def get_usdt_balance():
    """تقوم بجلب الرصيد المتاح من عملة USDT سواء في حساب Unified أو Spot"""
    try:
        res = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        balance = float(res['result']['list'][0]['coin'][0]['walletBalance'])
        return balance
    except Exception as e:
        try:
            res = client.get_wallet_balance(accountType="SPOT", coin="USDT")
            balance = float(res['result']['list'][0]['coin'][0]['walletBalance'])
            return balance
        except Exception:
            return 0.0

def execute_buy(symbol):
    """ترسل أمر شراء بالسعر السوقي الحالي للعملة المحددة وتسحب تفاصيل الرسوم والسعر الفعلي للتنفيذ"""
    for attempt in range(1, 4):
        try:
            current_price, _ = get_market_data(symbol)
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(BUY_AMOUNT_USD),
                marketUnit="quoteCoin"
            )
            
            order_id = order['result']['orderId']
            time.sleep(1.5) 
            
            exec_res = client.get_executions(category="spot", orderId=order_id)
            fills = exec_res['result']['list']
            
            total_fee_usd = 0.0
            total_qty = 0.0
            total_cost = 0.0
            asset_fee = 0.0

            for fill in fills:
                fee = float(fill['execFee'])
                qty = float(fill['execQty'])
                price = float(fill['execPrice'])
                
                total_qty += qty
                total_cost += qty * price
                
                asset_fee += fee
                total_fee_usd += fee * price

            actual_price = total_cost / total_qty if total_qty > 0 else current_price
            sellable_qty = total_qty - asset_fee

            return order, total_fee_usd, total_qty, actual_price, total_cost, sellable_qty

        except Exception as e:
            print("[BUY] [%s] فَشَلَتْ المُحَاوَلَةُ %d: %s" % (symbol, attempt, e))
            time.sleep(2)

    send_whatsapp_message(f"[ERROR] [{symbol}] فشلت الشراء بعد 3 محاولات")
    return None, 0, 0, 0, 0, 0

def execute_sell(symbol, qty):
    """ترسل أمر بيع بالسعر السوقي الحالي وتقوم بتقريب الكمية بناءً على دقة العملة المسموح بها في المنصة"""
    for attempt in range(1, 4):
        try:
            info = client.get_instruments_info(category="spot", symbol=symbol)
            step_str = info['result']['list'][0]['lotSizeFilter']['basePrecision']
            step = float(step_str)
            prec = len(step_str.split('.')[-1].rstrip('0')) if '.' in step_str else 0
            
            qty = round(qty - (qty % step), prec)

            if qty <= 0:
                print("[SELL] الكَمِّيَّةُ صِفْرٌ بَعْدَ التَّقْرِيبِ")
                return None, 0, 0, 0

            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(qty),
                marketUnit="baseCoin"
            )
            
            order_id = order['result']['orderId']
            time.sleep(1.5)
            
            exec_res = client.get_executions(category="spot", orderId=order_id)
            fills = exec_res['result']['list']
            
            total_fee = 0.0
            total_received = 0.0

            for fill in fills:
                fee = float(fill['execFee'])
                qty_f = float(fill['execQty'])
                price = float(fill['execPrice'])
                
                total_received += qty_f * price
                total_fee += fee

            actual_price = total_received / qty if qty > 0 else 0
            return order, total_received, total_fee, actual_price

        except Exception as e:
            print("[SELL] [%s] فَشَلَتْ المُحَاوَلَةُ %d: %s" % (symbol, attempt, e))
            time.sleep(2)

    return None, 0, 0, 0

# ================= منطق التداول الرئيسي =================

def count_open_positions(history, symbol=None):
    """تحسب إجمالي الصفقات المفتوحة لجميع العملات في المنصة"""
    return sum(1 for op in history.values() if isinstance(op, dict) and op.get('status') == "معلقة - جاري الانتظار")
def get_open_positions(history, symbol):
    """تسترجع قائمة الصفقات المعلقة (المفتوحة) الخاصة بعملة معينة"""
    return {op_id: op for op_id, op in history.items() 
            if isinstance(op, dict) and op.get('status') == "معلقة - جاري الانتظار" and op.get('symbol') == symbol}

def get_last_buy_price(history, symbol):
    """تسترجع سعر الشراء لآخر صفقة (مفتوحة) خاصة بعملة معينة لتحديد متى يتم التعزيز"""
    open_ops = get_open_positions(history, symbol)
    if not open_ops:
        return None
    last = max(open_ops.items(), key=lambda x: x[1].get('buy_time', ''))
    return last[1]['buy_price']

def get_last_buy_time(history, symbol):
    """تسترجع وقت الشراء لآخر صفقة (مفتوحة) للعملة المحددة لحساب وقت الانتظار المتبقي"""
    open_ops = get_open_positions(history, symbol)
    if not open_ops:
        return None
    times = [datetime.fromisoformat(op['buy_time']) for op in open_ops.values() if op.get('buy_time')]
    return max(times) if times else None

def get_last_sell_time(history, symbol):
    """تسترجع وقت آخر صفقة تم بيعها (وإغلاقها) للعملة المحددة"""
    sell_times = []
    for op in history.values():
        if isinstance(op, dict) and op.get('symbol') == symbol and op.get('status') == "تم البيع" and 'sell_details' in op:
            sd = op['sell_details']
            if 'sell_date' in sd and 'sell_time' in sd:
                try:
                    dt_str = f"{sd['sell_date']}T{sd['sell_time']}"
                    sell_times.append(datetime.fromisoformat(dt_str))
                except:
                    pass
    return max(sell_times) if sell_times else None

def get_absolute_last_buy_price(history, symbol):
    """تسترجع سعر الشراء لآخر عملية شراء مطلقاً (سواء كانت الصفقة ما زالت مفتوحة أم مباعة) للعملة المحددة"""
    times = []
    for op in history.values():
        if isinstance(op, dict) and op.get('symbol') == symbol and 'buy_time' in op and 'buy_price' in op:
            times.append((datetime.fromisoformat(op['buy_time']), op['buy_price']))
    if not times:
        return None
    times.sort(key=lambda x: x[0])
    return times[-1][1]

def create_buy_operation(symbol):
    """تقوم بتنفيذ الشراء فعلياً، حساب الأسعار المطلوبة، وتخزين بيانات الصفقة الجديدة في السجل"""
    order, fee, qty, actual_price, total_cost, sellable_qty = execute_buy(symbol)

    if order is None or qty <= 0:
        print("[BUY] [%s] فَشَلَ إِنْشَاءُ عَمَلِيَّةِ الشِّرَاءِ" % symbol)
        return None

    calc = calculate_sell_thresholds(actual_price, qty, fee)
    op_id = f"buy_{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()

    buy_data = {
        "symbol": symbol, # إضافة اسم العملة للصفقة
        "type": "buy",
        "status": "معلقة - جاري الانتظار",
        "date": now.date().isoformat(),
        "time": now.time().isoformat(),
        "buy_time": now.isoformat(),
        "buy_price": round(actual_price, 5),
        "qty": round(qty, 8),
        "sellable_qty": round(sellable_qty, 8),
        "buy_amount_usd": BUY_AMOUNT_USD,
        "buy_fee_usd": round(fee, 4),
        "buy_cost": round(calc['buy_cost'], 4),
        "total_cost": round(calc['total_cost'], 4),
        "break_even_price": round(calc['break_even_price'], 5),
        "min_sell_price": round(calc['min_sell_price'], 5),
        "sell_details": {}
    }

    history = load_history()
    history[op_id] = buy_data
    save_history(history)
    git_commit_and_push()

    balance = get_usdt_balance()

    msg = (
        f"✅ <b>تَمَّ الشِّرَاءُ بنجاح! ({symbol})</b>\n"
        f"المعرف: {op_id}\n"
        f"السعر: {actual_price:.5f}\n"
        f"سعر التعادل: {calc['break_even_price']:.5f}\n"
        f"سعر البيع المطلوب (مضاف إليه {TAKE_PROFIT_PCT}%): {calc['min_sell_price']:.5f}\n"
        f"💳 <b>الرصيد المتاح:</b> {balance:.5f} USDT"
    )
    send_whatsapp_message(msg)

    print("[BUY] [%s] تَمَّ الإِنْشَاءُ: %s @ %.2f" % (symbol, op_id, actual_price))
    return op_id

def try_sell_all(history, current_price, symbol):
    """تفحص جميع الصفقات المفتوحة للعملة المحددة وتقوم ببيعها إذا وصل السعر الحالي لهدف الربح"""
    open_positions = get_open_positions(history, symbol)

    if not open_positions:
        print("[SELL] [%s] لَا تُوجَدُ عَمَلِيَّاتٌ مَفْتُوحَةٌ لِلْبَيْعِ" % symbol)
        return False, history

    print("[SELL] [%s] جَارِي فَحْصُ %d عَمَلِيَّاتٍ مَفْتُوحَةٍ..." % (symbol, len(open_positions)))
    sold_any = False

    for op_id, pos in open_positions.items():
        buy_price = pos['buy_price']
        qty = pos.get('sellable_qty', pos['qty'])
        min_sell = pos['min_sell_price']
        buy_cost = pos['buy_cost']
        buy_fee = pos['buy_fee_usd']

        print("[SELL_CHECK] [%s] %s | شِرَاء@%.2f | الحَالِيُّ@%.2f | الهَدَفُ@%.2f" % 
              (symbol, op_id, buy_price, current_price, min_sell))

        if current_price >= min_sell:
            print("[SELL] [%s] %s تَمَّ بُلُوغُ الهَدَفِ! جَارِي البَيْعُ..." % (symbol, op_id))

            order, received, sell_fee, sell_price = execute_sell(symbol, qty)

            if order:
                actual_profit = received - buy_cost - sell_fee
                sold_any = True

                history[op_id]['status'] = "تم البيع"
                history[op_id]['sell_details'] = {
                    "sell_id": f"sell_{uuid.uuid4().hex[:8]}",
                    "sell_price": round(sell_price, 2),
                    "received_usd": round(received, 4),
                    "sell_fee_usd": round(sell_fee, 4),
                    "profit_usd": round(actual_profit, 4),
                    "profit_percent": round((actual_profit / (buy_cost + buy_fee)) * 100, 3),
                    "sell_date": datetime.utcnow().date().isoformat(),
                    "sell_time": datetime.utcnow().time().isoformat()
                }

                balance = get_usdt_balance()

                msg = (
                    f"💰 <b>تَمَّ البَيْعُ بِنَجَاحٍ! ({symbol})</b>\n"
                    f"المعرف: {op_id}\n"
                    f"الشراء: {buy_price:.2f} | البيع: {sell_price:.2f}\n"
                    f"الربح الصافي الفعلي: {actual_profit:.4f} USDT\n"
                    f"💳 <b>الرصيد المتاح:</b> {balance:.2f} USDT"
                )
                send_whatsapp_message(msg)
                print("[SELL] [%s] تَمَّ البَيْعُ %s بِرِبْح=%.4f" % (symbol, op_id, actual_profit))
            else:
                print("[SELL] [%s] فَشَلَتْ عَمَلِيَّةُ بَيْعِ %s" % (symbol, op_id))
        else:
            print("[SELL_CHECK] [%s] %s لَمْ يَحِنِ الوَقْتُ بَعْدُ" % (symbol, op_id))

    return sold_any, history

def can_rebuy(history, current_price, symbol):
    """تتحقق من الشروط الزمنية والسعرية للعملة المحددة لمعرفة ما إذا كان البوت مسموحاً له بالتعزيز (شراء جديد)"""
    last_time = get_last_buy_time(history, symbol)
    last_price = get_last_buy_price(history, symbol)

    if last_time is None or last_price is None:
        return False

    elapsed = datetime.utcnow() - last_time
    elapsed_min = elapsed.total_seconds() / 60

    if elapsed < timedelta(minutes=REBUY_WAIT_MINUTES):
        return False

    target_rebuy_price = last_price * (1 - (PRICE_STEP_PCT / 100))
    if current_price > target_rebuy_price:
        return False

    return True

# ================= الدالة الرئيسية =================
def get_all_tickers_data():
    """تقوم بجلب أسعار جميع العملات في السوق بطلب واحد فقط (لقطة شاشة لحظية)"""
    try:
        res = client.get_tickers(category="spot")
        tickers_dict = {}
        
        for item in res['result']['list']:
            symbol = item['symbol']
            # نتجاهل أي عملة لا تنتهي بـ USDT لتنظيف البحث
            if not symbol.endswith("USDT"):
                continue
                
            tickers_dict[symbol] = {
                'lastPrice': float(item['lastPrice']),
                'lowPrice24h': float(item['lowPrice24h']),
                'highPrice24h': float(item['highPrice24h']), # جلب أعلى سعر
                'turnover24h': float(item['turnover24h'])    # جلب حجم التداول بالدولار
            }
        print(f"[PRICE] تَمَّ جَلْبُ لَقْطَةِ السُّوقِ لِـ {len(tickers_dict)} عَمَلَةٍ USDT بِنَجَاحٍ.")
        return tickers_dict
    except Exception as e:
        print(f"[PRICE] فَشَلٌ فِي جَلْبِ البَيَانَاتِ الجَمَاعِيَّةِ: {e}")
        return None

def calculate_rsi(prices, period=14):
    """حساب مؤشر القوة النسبية بناءً على إغلاقات الشموع"""
    if len(prices) < period + 1:
        return 50.0  # قيمة محايدة إذا لم تكن البيانات كافية
    
    gains = []
    losses = []
    for i in range(1, len(prices)):
        difference = prices[i] - prices[i - 1]
        if difference > 0:
            gains.append(difference)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(difference))
            
    # حساب المتوسط الأولي
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # حساب الـ RSI باستخدام طريقة وايلدر للتنعيم (Wilder's Smoothing)
    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
        
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)
def is_safe_to_buy_rsi(symbol):
    """
    تتأكد من أن العملة في منطقة تشبع بيعي (15 دقيقة) 
    وأن شمعة الدقيقة الواحدة الأخيرة المغلقة كانت خضراء لتأكيد الارتداد الحقيقي.
    """
    try:
        # 1. جلب شموع 15 دقيقة لحساب RSI
        res = client.get_kline(
            category="spot",
            symbol=symbol,
            interval="15", 
            limit=30
        )
        
        klines = res.get('result', {}).get('list', [])
        if not klines or len(klines) < 15:
            return False
            
        klines.reverse()
        close_prices = [float(k[4]) for k in klines]
        
        # حساب RSI على فريم 15 دقيقة
        rsi_value = calculate_rsi(close_prices, period=14)
        is_oversold = rsi_value <= 40.0
        
        # إذا لم تكن العملة في منطقة تشبع بيعي، نتوقف فوراً لتوفير طلبيات API
        if not is_oversold:
            return False

        # 2. جلب آخر شمعتين على فريم الدقيقة الواحدة (1m) للتأكد من الارتداد
        res_1m = client.get_kline(
            category="spot",
            symbol=symbol,
            interval="1",
            limit=3
        )
        
        klines_1m = res_1m.get('result', {}).get('list', [])
        if not klines_1m or len(klines_1m) < 2:
            return False

        klines_1m.reverse() # الترتيب من الأقدم للأحدث
        
        # الفهرس -2 يمثل الشمعة السابقة التي أغلقت بالفعل قبل ثوانٍ على فريم 1 دقيقة
        prev_1m_open = float(klines_1m[-2][1])
        prev_1m_close = float(klines_1m[-2][4])
        
        is_1m_green = prev_1m_close > prev_1m_open

        # الشروط النهائية: RSI 15m ممتاز + شمعة 1m السابقة أغلقت خضراء
        if is_oversold and is_1m_green:
            print(f"│ [مُؤَشِّرُ RSI + 1M] {symbol} تأكد ارتدادها بشمعة دقيقة خضراء مغلقة! (RSI: {rsi_value}) 🟢")
            return True
        else:
            print(f"│ [تجاهل] {symbol} في القاع (RSI: {rsi_value}) لكن شمعة الدقيقة الأخيرة ليست خضراء 🔴")
            return False
            
    except Exception as e:
        print(f"│ [خَطَأٌ] فَشَلَ التَّحَقُّقُ مِنْ مُؤَشِّرِ RSI لِعُمْلَةِ {symbol}: {e}")
        return False

def check_recent_high_target(symbol, current_price):
    """
    تجلب شموع آخر 4 ساعات وتتأكد ما إذا كانت العملة قد وصلت للسعر المستهدف مؤخراً
    """
    try:
        # نحتاج ارتفاع بنسبة 0.3% تقريباً لتغطية (رسوم الشراء + رسوم البيع + ربح 0.1%)
        required_jump_pct = (TAKER_FEE_PERCENT * 2) + (TAKE_PROFIT_PCT / 100) 
        target_price = current_price * (1 + required_jump_pct)
        
        # جلب شموع فريم الساعة (60 دقيقة) لآخر 4 ساعات
        res = client.get_kline(category="spot", symbol=symbol, interval="60", limit=5)
        
        recent_highest = 0
        for kline in res['result']['list']:
            high_price = float(kline[2]) # kline[2] هو أعلى سعر في الشمعة
            if high_price > recent_highest:
                recent_highest = high_price
                
        # هل أعلى سعر في آخر 4 ساعات أكبر من السعر الذي نحتاجه الآن؟
        if recent_highest >= target_price:
            return True, target_price, recent_highest
        else:
            return False, target_price, recent_highest
            
    except Exception as e:
        print(f"│ [KLINE] خَطَأ فِي جَلْبِ الشُّمُوعِ لِعَمَلَةِ {symbol}: {e}")
        return False, 0, 0


import time
from datetime import datetime

def main():
    """الدالة الرئيسية التي تقوم ببدء تشغيل الماسح الذكي للسوق لتنفيذ المنطق"""
    if not API_KEY or not API_SECRET:
        print("[ERROR] لَا تُوجَدُ مَفَاتِيحُ API!")
        return

    print("[START] بَدْءُ تَشْغِيلِ البُوتِ (الْمَاسِحُ الذَّكِيُّ لِلسُّوقِ)...")
    init_client_with_retries()

    start_time = time.time()
    end_time = start_time + (RUN_DURATION_HOURS * 3600)

    while time.time() < end_time:
        loop_start = time.time()

        try:
            history = load_history()
            
            # --- أخذ اللقطة اللحظية للسوق بالكامل بطلب واحد ---
            all_tickers = get_all_tickers_data()
            if all_tickers is None:
                print("[LOOP] فَشَلٌ فِي جَلْبِ لَقْطَةِ السُّوقِ، سَنُحَاوِلُ مَرَّةً أُخْرَى...")
                time.sleep(2)
                continue
            
            # المرور على كل عملات السوق المتاحة في الذاكرة 
            for symbol, data in all_tickers.items():
                
                # تجاهل أي عملة لا تنتهي بـ USDT
                if not symbol.endswith("USDT"):
                    continue

                current_price = data['lastPrice']
                low_24h = data['lowPrice24h']
                high_24h = data['highPrice24h']
                volume_24h = data['turnover24h']

                # ───[ فلترة العملات الميتة والخاملة ]───
                if volume_24h < 500000:
                    continue 
                if low_24h == 0: 
                    continue
                volatility_pct = ((high_24h - low_24h) / low_24h) * 100
                if volatility_pct < 5.0:
                    continue
                # ─────────────────────────────────────

                print(f"\n┌───[ جَارِي فَحْصُ {symbol} (حَيَوِيَّة: {volatility_pct:.1f}%) ]─────────────────────┐")
                print("│ [PRICE] السِّعْرُ الحَالِيُّ: %.5f | قَاعُ 24 سَاعَة: %.5f | السِّيُولَة: %.0f$" % (current_price, low_24h, volume_24h))

                open_count = count_open_positions(history, symbol)
                
                print("│ [خُطْوَةُ 1] فَحْصُ البَيْعِ لِلْعَمَلِيَّاتِ المَفْتُوحَةِ (%d)" % open_count)
                sold, history = try_sell_all(history, current_price, symbol)

                if sold:
                    print("│ [النَّتِيجَةُ] يَبِيعُ! تَمَّتْ عَمَلِيَّةُ البَيْعِ بِنَجَاحٍ.")
                    save_history(history)
                    git_commit_and_push()
                else:
                    print("│ [النَّتِيجَةُ] لَمْ يَبِعْ → فَحْصُ إِعَادَةِ الشِّرَاءِ...")
                    
                    if open_count < MAX_OPEN_POSITIONS:
                        limit_buy_target = low_24h * (1 + (BUY_NEAR_24H_LOW_PCT / 100))
                        is_price_in_buy_zone = (current_price <= limit_buy_target)
                        
                        print("│ [إِسْتِرَاتِيجِيَّة] هَدَفُ الشِّرَاءِ المُعَلَّقِ: <= %.5f (السِّعْرُ الحَالِيُّ: %.5f)" % (limit_buy_target, current_price))

                        last_sell_time = get_last_sell_time(history, symbol)
                        wait_sell_ok = True
                        elapsed_since_sell = 0.0
                        
                        if last_sell_time:
                            elapsed_since_sell = (datetime.utcnow() - last_sell_time).total_seconds() / 60
                            if elapsed_since_sell < 1.0:
                                print("│ [تَجَاوُزٌ] اِنْتِظَارُ 1 دَقَائِقَ بَعْدَ البَيْعِ. (مَرَّتْ %.1f دَقِيقَة)" % elapsed_since_sell)
                                wait_sell_ok = False
                        
                        if wait_sell_ok:
                            wants_to_buy = False
                            buy_message = ""

                            # تحديد هل استوفت العملة شروط الشراء الخاصة بك؟
                            if open_count == 0:
                                abs_last_buy_price = get_absolute_last_buy_price(history, symbol)
                                if abs_last_buy_price is None:
                                    if is_price_in_buy_zone:
                                        wants_to_buy = True
                                        buy_message = "│ [شِرَاءٌ] السِّعْرُ هَبَطَ لِمِنْطَقَةِ القَاعِ! جَارِي التَّنْفِيذُ فَوْراً..."
                                    else:
                                        print("│ [تَجَاوُزٌ] السِّعْرُ مُرْتَفِعٌ. نَنْتَظِرُ هُبُوطَهُ لِمِنْطَقَةِ الشِّرَاءِ الآمِنَةِ.")
                                else:
                                    if elapsed_since_sell >= 1.0:
                                        if is_price_in_buy_zone:
                                            wants_to_buy = True
                                            buy_message = "│ [شِرَاءٌ] مَرَّتْ سَاعَةٌ كَامِلَةٌ وَالسِّعْرُ فِي القَاعِ. جَارِي الشِّرَاءُ..."
                                        else:
                                            print("│ [تَجَاوُزٌ] مَرَّتْ دَقِيقَةٌ لَكِنَّ السِّعْرَ لَمْ يَصِلْ لِلْقَاعِ بَعْدُ.")
                                    elif current_price <= abs_last_buy_price:
                                        if is_price_in_buy_zone:
                                            wants_to_buy = True
                                            buy_message = "│ [شِرَاءٌ] السِّعْرُ أَقَلُّ مِنْ آخِرِ شِرَاءٍ وَفِي قَاعِ 24 سَاعَة. جَارِي الشِّرَاءُ..."
                                        else:
                                            print("│ [تَجَاوُزٌ] السِّعْرُ أَقَلُّ مِنْ آخِرِ شِرَاءٍ، لَكِنَّهُ لَيْسَ فِي القَاعِ الْمَطْلُوبِ.")
                                    else:
                                        minutes_left = 60.0 - elapsed_since_sell
                                        print("│ [تَجَاوُزٌ] نَنْتَظِرُ اِنْخِفَاضَهُ أَوْ مُرُورَ (%.1f) دَقِيقَة..." % minutes_left)
                            elif can_rebuy(history, current_price, symbol):
                                if is_price_in_buy_zone:
                                    wants_to_buy = True
                                    buy_message = "│ [شِرَاءٌ] يَشْتَرِي! الشُّرُوطُ مُطَابِقَةٌ لِإِعَادَةِ الشِّرَاءِ فِي القَاعِ..."
                                else:
                                     print("│ [تَجَاوُزٌ] شُرُوطُ إِعَادَةِ الشِّرَاءِ تَحَقَّقَتْ، لَكِنَّ السِّعْرَ لَيْسَ فِي القَاعِ.")
                            else:
                                print("│ [تَجَاوُزٌ] شُرُوطُ الشِّرَاءِ التَّقْلِيدِيَّةِ لَمْ تَتَحَقَّقْ بَعْدُ.")

                            # ───[ الفحص النهائي للشموع قبل التنفيذ ]───
                            if wants_to_buy:
                                # الحارس الجديد: فحص مؤشر القوة النسبية RSI أولاً
                                if not is_safe_to_buy_rsi(symbol):
                                    print("│ [تَجَاوُزٌ] تَمَّ إِلْغَاءُ الشِّرَاءِ: مُؤَشِّرُ RSI يُحَذِّرُ مِنِ اسْتِمْرَارِ الْهُبُوطِ (سِكِّينٌ سَاقِطٌ) ⚠️")
                                else:
                                    passed_recent_high, target, recent_high = check_recent_high_target(symbol, current_price)
                                    if passed_recent_high:
                                        print("│ [BUY-CHECK] مُمْتَازٌ! العُمْلَةُ حَقَّقَتْ %.5f مُؤَخَّراً، وَهِيَ قَادِرَةٌ عَلَى العَوْدَةِ لِهَدَفِنَا %.5f" % (recent_high, target))
                                        print(buy_message)
                                        create_buy_operation(symbol)
                                    else:
                                        print("│ [SKIP] تَمَّ التَّجَاهُلُ: العُمْلَةُ فِي هُبُوطٍ مُسْتَمِرٍّ، لَمْ تَصِلْ لِهَدَفِ %.5f فِي آخِرِ 4 سَاعَاتٍ." % target)
                            # ──────────────────────────────────────────
                    else:
                        print("│ [تَحْذِيرٌ] تَمَّ بُلُوغُ الحَدِّ الأَقْصَى لِلصَّفَقَاتِ لِهَذِهِ العُمْلَةِ (%d)." % MAX_OPEN_POSITIONS)

                print(f"└───[ اِنْتِهَاءُ فَحْصِ {symbol} ]─────────────────────┘")

        except Exception as e:
            error_str = str(e)
            print("[ERROR] %s" % error_str[:200])
            if any(k in error_str.lower() for k in ["connection", "proxy", "read", "timeout", "api", "unauthorized"]):
                init_client_with_retries()

        elapsed = time.time() - loop_start
        sleep_time = max(0, SLEEP_SECONDS - elapsed)
        time.sleep(sleep_time)

    print("[END] تَمَّ الاِنْتِهَاءُ مِنَ الدَّوْرَةِ زَمَنِيًّا!")

if __name__ == "__main__":
    main()
