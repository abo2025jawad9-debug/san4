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
    api_key: str = os.getenv('BYBIT_API_KEY')
    secret: str = os.getenv('BYBIT_API_SECRET')
    telegram_token: str = os.getenv('TELEGRAM_TOKEN')
    telegram_chat_id: str = os.getenv('TELEGRAM_CHAT_ID')

cfg = Config()

API_KEY = cfg.api_key
API_SECRET = cfg.secret
TELEGRAM_TOKEN = cfg.telegram_token
TELEGRAM_CHAT_ID = cfg.telegram_chat_id

SYMBOL = "AAVEUSDT"
BUY_AMOUNT_USD = 14.3
TAKER_FEE_PERCENT = 0.001
MIN_PROFIT_USD = 0.05  

# [تعديلات الاستراتيجية الجديدة - الأمر المعلق الذكي]
# النسبة المئوية المسموح بها فوق أدنى سعر (قاع) لآخر 24 ساعة.
# قيمة (1.5) تعني: البوت لن يشتري إلا إذا كان السعر الحالي لا يرتفع بأكثر من 1.5% عن قاع اليوم.
# يمكنك تقليلها إلى (1.0) ليكون البوت أكثر صرامة، أو رفعها إذا أردت تسريع الشراء.
BUY_NEAR_24H_LOW_PCT = 0.5 

# الفارق السعري المطلوب بين كل صفقة والتي تليها (بالدولار)
PRICE_STEP_USD = 0.3
    
JSON_FILE = 'sh.json'
MAX_OPEN_POSITIONS = 1
REBUY_WAIT_MINUTES = 1
SLEEP_SECONDS = 7
RUN_DURATION_HOURS = 5.8 

PROXY_LIST = []
client = None

# ================= بروكسيات =================
# [تم الاحتفاظ بدوال البروكسي كما هي لضمان عمل البوت على الخوادم]

def fetch_free_proxies():
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

# ================= تليجرام وإدارة الملفات =================

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    for attempt in range(1, 4):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False

def load_history():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                pass
    return {}

def save_history(history):
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

def git_commit_and_push():
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
    buy_cost = buy_price * qty
    estimated_sell_fee = buy_cost * TAKER_FEE_PERCENT
    total_fees = buy_fee_usd + estimated_sell_fee
    total_cost = buy_cost + total_fees
    
    break_even = total_cost / qty
    min_profit_price = (total_cost + MIN_PROFIT_USD) / qty

    return {
        "buy_cost": buy_cost,
        "buy_fee_usd": buy_fee_usd,
        "estimated_sell_fee": estimated_sell_fee,
        "total_fees": total_fees,
        "total_cost": total_cost,
        "break_even_price": break_even,
        "min_sell_price": min_profit_price
    }

# ================= عمليات السوق =================

def get_market_data():
    """
    [تعديل]: دالة جديدة تجلب السعر الحالي بالإضافة إلى أدنى سعر في آخر 24 ساعة
    """
    try:
        res = client.get_tickers(category="spot", symbol=SYMBOL)
        ticker = float(res['result']['list'][0]['lastPrice'])
        low_24h = float(res['result']['list'][0]['lowPrice24h'])
        print("[PRICE] السِّعْرُ الحَالِيُّ: %.2f | قَاعُ 24 سَاعَة: %.2f" % (ticker, low_24h))
        return ticker, low_24h
    except Exception as e:
        print("[PRICE] فَشَلٌ فِي جَلْبِ البَيَانَاتِ: %s" % e)
        return None, None

def get_usdt_balance():
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

def execute_buy():
    for attempt in range(1, 4):
        try:
            current_price, _ = get_market_data()
            order = client.place_order(
                category="spot",
                symbol=SYMBOL,
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
            print("[BUY] فَشَلَتْ المُحَاوَلَةُ %d: %s" % (attempt, e))
            time.sleep(2)

    send_telegram_message("[ERROR] فشل الشراء بعد 3 محاولات!")
    return None, 0, 0, 0, 0, 0

def execute_sell(qty):
    for attempt in range(1, 4):
        try:
            info = client.get_instruments_info(category="spot", symbol=SYMBOL)
            step_str = info['result']['list'][0]['lotSizeFilter']['basePrecision']
            step = float(step_str)
            prec = len(step_str.split('.')[-1].rstrip('0')) if '.' in step_str else 0
            
            qty = round(qty - (qty % step), prec)

            if qty <= 0:
                print("[SELL] الكَمِّيَّةُ صِفْرٌ بَعْدَ التَّقْرِيبِ")
                return None, 0, 0, 0

            order = client.place_order(
                category="spot",
                symbol=SYMBOL,
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
            print("[SELL] فَشَلَتْ المُحَاوَلَةُ %d: %s" % (attempt, e))
            time.sleep(2)

    return None, 0, 0, 0

# ================= منطق التداول الرئيسي =================

def count_open_positions(history):
    return sum(1 for op in history.values() if isinstance(op, dict) and op.get('status') == "معلقة - جاري الانتظار")

def get_open_positions(history):
    return {op_id: op for op_id, op in history.items() 
            if isinstance(op, dict) and op.get('status') == "معلقة - جاري الانتظار"}

def get_last_buy_price(history):
    open_ops = get_open_positions(history)
    if not open_ops:
        return None
    last = max(open_ops.items(), key=lambda x: x[1].get('buy_time', ''))
    return last[1]['buy_price']

def get_last_buy_time(history):
    open_ops = get_open_positions(history)
    if not open_ops:
        return None
    times = [datetime.fromisoformat(op['buy_time']) for op in open_ops.values() if op.get('buy_time')]
    return max(times) if times else None

def get_last_sell_time(history):
    sell_times = []
    for op in history.values():
        if isinstance(op, dict) and op.get('status') == "تم البيع" and 'sell_details' in op:
            sd = op['sell_details']
            if 'sell_date' in sd and 'sell_time' in sd:
                try:
                    dt_str = f"{sd['sell_date']}T{sd['sell_time']}"
                    sell_times.append(datetime.fromisoformat(dt_str))
                except:
                    pass
    return max(sell_times) if sell_times else None

def get_absolute_last_buy_price(history):
    times = []
    for op in history.values():
        if isinstance(op, dict) and 'buy_time' in op and 'buy_price' in op:
            times.append((datetime.fromisoformat(op['buy_time']), op['buy_price']))
    if not times:
        return None
    times.sort(key=lambda x: x[0])
    return times[-1][1]

def create_buy_operation():
    order, fee, qty, actual_price, total_cost, sellable_qty = execute_buy()

    if order is None or qty <= 0:
        print("[BUY] فَشَلَ إِنْشَاءُ عَمَلِيَّةِ الشِّرَاءِ")
        return None

    calc = calculate_sell_thresholds(actual_price, qty, fee)
    op_id = f"buy_{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()

    buy_data = {
        "type": "buy",
        "status": "معلقة - جاري الانتظار",
        "date": now.date().isoformat(),
        "time": now.time().isoformat(),
        "buy_time": now.isoformat(),
        "buy_price": round(actual_price, 2),
        "qty": round(qty, 8),
        "sellable_qty": round(sellable_qty, 8),
        "buy_amount_usd": BUY_AMOUNT_USD,
        "buy_fee_usd": round(fee, 4),
        "buy_cost": round(calc['buy_cost'], 4),
        "total_cost": round(calc['total_cost'], 4),
        "break_even_price": round(calc['break_even_price'], 2),
        "min_sell_price": round(calc['min_sell_price'], 2),
        "sell_details": {}
    }

    history = load_history()
    history[op_id] = buy_data
    save_history(history)
    git_commit_and_push()

    balance = get_usdt_balance()

    msg = (
        "✅ <b>تَمَّ الشِّرَاءُ بنجاح!</b>\n"
        f"المعرف: {op_id}\n"
        f"السعر: {actual_price:.2f}\n"
        f"سعر التعادل: {calc['break_even_price']:.2f}\n"
        f"سعر البيع المطلوب: {calc['min_sell_price']:.2f}\n"
        f"💳 <b>الرصيد المتاح:</b> {balance:.2f} USDT"
    )
    send_telegram_message(msg)

    print("[BUY] تَمَّ الإِنْشَاءُ: %s @ %.2f" % (op_id, actual_price))
    return op_id

def try_sell_all(history, current_price):
    open_positions = get_open_positions(history)

    if not open_positions:
        print("[SELL] لَا تُوجَدُ عَمَلِيَّاتٌ مَفْتُوحَةٌ لِلْبَيْعِ")
        return False, history

    print("[SELL] جَارِي فَحْصُ %d عَمَلِيَّاتٍ مَفْتُوحَةٍ..." % len(open_positions))
    sold_any = False

    for op_id, pos in open_positions.items():
        buy_price = pos['buy_price']
        qty = pos.get('sellable_qty', pos['qty'])
        min_sell = pos['min_sell_price']
        buy_cost = pos['buy_cost']
        buy_fee = pos['buy_fee_usd']

        print("[SELL_CHECK] %s | شِرَاء@%.2f | الحَالِيُّ@%.2f | الهَدَفُ@%.2f" % 
              (op_id, buy_price, current_price, min_sell))

        if current_price >= min_sell:
            print("[SELL] %s تَمَّ بُلُوغُ الهَدَفِ! جَارِي البَيْعُ..." % op_id)

            order, received, sell_fee, sell_price = execute_sell(qty)

            if order:
                actual_profit = received - buy_cost - buy_fee - sell_fee
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
                    "💰 <b>تَمَّ البَيْعُ بِنَجَاحٍ!</b>\n"
                    f"المعرف: {op_id}\n"
                    f"الشراء: {buy_price:.2f} | البيع: {sell_price:.2f}\n"
                    f"الربح الصافي الفعلي: {actual_profit:.4f} USDT\n"
                    f"💳 <b>الرصيد المتاح:</b> {balance:.2f} USDT"
                )
                send_telegram_message(msg)
                print("[SELL] تَمَّ البَيْعُ %s بِرِبْح=%.4f" % (op_id, actual_profit))
            else:
                print("[SELL] فَشَلَتْ عَمَلِيَّةُ بَيْعِ %s" % op_id)
        else:
            print("[SELL_CHECK] %s لَمْ يَحِنِ الوَقْتُ بَعْدُ" % op_id)

    return sold_any, history

def can_rebuy(history, current_price):
    last_time = get_last_buy_time(history)
    last_price = get_last_buy_price(history)

    if last_time is None or last_price is None:
        return False

    elapsed = datetime.utcnow() - last_time
    elapsed_min = elapsed.total_seconds() / 60

    if elapsed < timedelta(minutes=REBUY_WAIT_MINUTES):
        return False

    if current_price > (last_price - PRICE_STEP_USD):
        return False

    return True

# ================= الدالة الرئيسية =================

def main():
    if not API_KEY or not API_SECRET:
        print("[ERROR] لَا تُوجَدُ مَفَاتِيحُ API!")
        return

    print("[START] بَدْءُ تَشْغِيلِ البُوتِ...")
    init_client_with_retries()

    start_time = time.time()
    end_time = start_time + (RUN_DURATION_HOURS * 3600)

    history = load_history()
    open_count = count_open_positions(history)
    
    if open_count == 0:
        print("[START] لا توجد صفقات معلقة سابقة. سَيَبْدَأُ الفَحْصُ فِي الدَّوْرَةِ الرَّئِيسِيَّةِ...")
    else:
        print(f"[START] تم العثور على {open_count} صفقات معلقة سابقة. استئناف المراقبة فوراً...")

    while time.time() < end_time:
        loop_start = time.time()

        try:
            history = load_history()
            
            print("\n┌─────────────────────────────────────┐")
            
            # [تعديل] جلب السعر الحالي وقاع 24 ساعة في نفس الوقت
            current_price, low_24h = get_market_data()
            if current_price is None or low_24h is None:
                print("│ [LOOP] فَشَلٌ فِي جَلْبِ السِّعْرِ، جَارِي الإِعَادَةُ...")
                time.sleep(5)
                continue

            open_count = count_open_positions(history)
            
            print("│ [خُطْوَةُ 1] فَحْصُ البَيْعِ لِلْعَمَلِيَّاتِ المَفْتُوحَةِ (%d)" % open_count)
            sold, history = try_sell_all(history, current_price)

            if sold:
                print("│ [النَّتِيجَةُ] يَبِيعُ! تَمَّتْ عَمَلِيَّةُ البَيْعِ بِنَجَاحٍ.")
                save_history(history)
                git_commit_and_push()
            else:
                print("│ [النَّتِيجَةُ] لَمْ يَبِعْ → فَحْصُ إِعَادَةِ الشِّرَاءِ...")
                if open_count < MAX_OPEN_POSITIONS:
                    
                    # [الاستراتيجية الجديدة] حساب منطقة الشراء الآمنة (الأمر المعلق)
                    limit_buy_target = low_24h * (1 + (BUY_NEAR_24H_LOW_PCT / 100))
                    is_price_in_buy_zone = (current_price <= limit_buy_target)
                    
                    print("│ [إِسْتِرَاتِيجِيَّة] هَدَفُ الشِّرَاءِ المُعَلَّقِ: <= %.2f (السِّعْرُ الحَالِيُّ: %.2f)" % (limit_buy_target, current_price))

                    last_sell_time = get_last_sell_time(history)
                    wait_sell_ok = True
                    elapsed_since_sell = 0.0
                    
                    if last_sell_time:
                        elapsed_since_sell = (datetime.utcnow() - last_sell_time).total_seconds() / 60
                        if elapsed_since_sell < 10.0:
                            print("│ [تَجَاوُزٌ] اِنْتِظَارُ 10 دَقَائِقَ بَعْدَ البَيْعِ. (مَرَّتْ %.1f دَقِيقَة)" % elapsed_since_sell)
                            wait_sell_ok = False
                    
                    if wait_sell_ok:
                        if open_count == 0:
                            abs_last_buy_price = get_absolute_last_buy_price(history)
                            if abs_last_buy_price is None:
                                # حالة بدء البوت لأول مرة بدون تاريخ
                                if is_price_in_buy_zone:
                                    print("│ [شِرَاءٌ] السِّعْرُ هَبَطَ لِمِنْطَقَةِ القَاعِ! جَارِي التَّنْفِيذُ فَوْراً...")
                                    create_buy_operation()
                                else:
                                    print("│ [تَجَاوُزٌ] السِّعْرُ مُرْتَفِعٌ. نَنْتَظِرُ هُبُوطَهُ لِمِنْطَقَةِ الشِّرَاءِ الآمِنَةِ.")
                            else:
                                if elapsed_since_sell >= 60.0:
                                    if is_price_in_buy_zone:
                                        print("│ [شِرَاءٌ] مَرَّتْ سَاعَةٌ كَامِلَةٌ وَالسِّعْرُ فِي القَاعِ. جَارِي الشِّرَاءُ...")
                                        create_buy_operation()
                                    else:
                                        print("│ [تَجَاوُزٌ] مَرَّتْ سَاعَةٌ لَكِنَّ السِّعْرَ لَمْ يَصِلْ لِلْقَاعِ بَعْدُ.")
                                elif current_price <= abs_last_buy_price:
                                    if is_price_in_buy_zone:
                                        print("│ [شِرَاءٌ] السِّعْرُ أَقَلُّ مِنْ آخِرِ شِرَاءٍ وَفِي قَاعِ 24 سَاعَة. جَارِي الشِّرَاءُ...")
                                        create_buy_operation()
                                    else:
                                        print("│ [تَجَاوُزٌ] السِّعْرُ أَقَلُّ مِنْ آخِرِ شِرَاءٍ، لَكِنَّهُ لَيْسَ فِي القَاعِ الْمَطْلُوبِ.")
                                else:
                                    minutes_left = 60.0 - elapsed_since_sell
                                    print("│ [تَجَاوُزٌ] نَنْتَظِرُ اِنْخِفَاضَهُ أَوْ مُرُورَ (%.1f) دَقِيقَة..." % minutes_left)
                        elif can_rebuy(history, current_price):
                            if is_price_in_buy_zone:
                                print("│ [شِرَاءٌ] يَشْتَرِي! الشُّرُوطُ مُطَابِقَةٌ لِإِعَادَةِ الشِّرَاءِ فِي القَاعِ...")
                                create_buy_operation()
                            else:
                                 print("│ [تَجَاوُزٌ] شُرُوطُ إِعَادَةِ الشِّرَاءِ تَحَقَّقَتْ، لَكِنَّ السِّعْرَ لَيْسَ فِي القَاعِ.")
                        else:
                            print("│ [تَجَاوُزٌ] شُرُوطُ الشِّرَاءِ التَّقْلِيدِيَّةِ لَمْ تَتَحَقَّقْ بَعْدُ.")
                else:
                    print("│ [تَحْذِيرٌ] تَمَّ بُلُوغُ الحَدِّ الأَقْصَى لِلصَّفَقَاتِ (%d)." % MAX_OPEN_POSITIONS)

            print("└─────────────────────────────────────┘")

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

