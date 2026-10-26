import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

# --- Получаем EMA с 1H, 4H, 1D ---
def get_target_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": ["Min60", "Hour4", "Day1"], "Bitget": ["1H", "4H", "1D"]}
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=3).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=100", headers=HEADERS, timeout=3).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue
            
            tf_name = tf.replace('Min', '').replace('Hour', 'H').replace('Day', 'D')
            for span in [7, 14, 28]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

# --- РИСОВАНИЕ ГРАФИКА С ФИТИЛЯМИ ---
def draw_chart(sym, exchange, target_ema_val, target_name):
    try:
        filename = f"{sym}_chart.png"
        df_15m = None
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
            if res.get('success'):
                data = res['data']
                df_15m = pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=5).json()
            if res.get('code') == '00000':
                data = res['data']
                df_15m = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_15m = df_15m.iloc[::-1]
                df_15m['time'] = pd.to_datetime(df_15m['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df_15m[col] = df_15m[col].astype(float)

        if df_15m is None or df_15m.empty: return None
        df_15m.set_index('time', inplace=True)
        hlines = dict(hlines=[target_ema_val], colors=['green'], linestyle='-', linewidths=2.0)
        mpf.plot(df_15m, type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | Target: {target_name}", tight_layout=True, hlines=hlines)
        return filename
    except: return None

def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 5000000:
                    coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice'])})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                if float(c.get('usdtVolume', 0)) > 5000000:
                    coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr'])})
    except: pass
    return coins

# --- SMC АНАЛИЗ (Поиск аномалий) ---
def analyze_smc(sym, exchange):
    """Ищем Volume Spike и Liquidity Sweep на 15м графике"""
    try:
        df = None
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
            if res.get('success'):
                data = res['data']
                df = pd.DataFrame({'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=25", headers=HEADERS, timeout=5).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1]
                for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)

        if df is None or len(df) < 25: return None

        # Текущая (последняя) свеча
        current = df.iloc[-1]
        prev_candles = df.iloc[-21:-1] # Предыдущие 20 свечей

        # 1. Volume Spike (Объем в 2 раза больше среднего)
        avg_vol = prev_candles['volume'].mean()
        vol_spike = current['volume'] > (avg_vol * 2.0)
        vol_ratio = current['volume'] / avg_vol if avg_vol > 0 else 0

        # 2. Liquidity Sweep (Прокол максимума с закрытием ниже)
        recent_high = prev_candles['high'].max()
        # Если текущий High выше недавнего максимума, но Close ниже него (длинный фитиль)
        sweep = (current['high'] > recent_high) and (current['close'] < recent_high)
        
        # 3. Сила свечи (Закрылась ли свеча в нижней половине своего диапазона)
        candle_range = current['high'] - current['low']
        close_pos = (current['close'] - current['low']) / candle_range if candle_range > 0 else 0
        weak_close = close_pos < 0.4 # Закрытие в нижних 40% свечи (медвежий признак)

        score = 0
        if vol_spike: score += 1
        if sweep: score += 1
        if weak_close: score += 1

        return {
            'vol_spike': vol_spike,
            'vol_ratio': vol_ratio,
            'sweep': sweep,
            'recent_high': recent_high,
            'score': score
        }
    except Exception as e:
        return None

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    start_time = time.time()
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    print(f"Проверяем {len(unique_coins)} монет...")
    found = 0

    for coin in list(unique_coins)[:60]: # Ограничиваем 60 монетами
        if found >= 3: break
        if time.time() - start_time > 250: break # Лимит времени GitHub
            
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        # Сначала быстрый памп (чтобы не грузить биржу лишний раз)
        try:
            if ex == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=3).json()
                if not res.get('success') or len(res['data']['close']) < 2: continue
                open_p = float(res['data']['open'][-1])
                pump_change = ((current_price - open_p) / open_p) * 100
            else:
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2", headers=HEADERS, timeout=3).json()
                if res.get('code') != '00000' or len(res['data']) < 2: continue
                open_p = float(res['data'][0][1])
                pump_change = ((current_price - open_p) / open_p) * 100
        except: continue

        if pump_change < 1.5: continue # Если нет пампа, идем дальше

        # Если памп есть, делаем глубокий SMC анализ
        smc = analyze_smc(sym, ex)
        if not smc: continue

        target_emas = get_target_emas(sym, ex, current_price)
        if not target_emas: continue

        best_name, best_data = min(target_emas.items(), key=lambda x: x[1]['diff'])
        
        chart_file = draw_chart(sym, ex, best_data['val'], best_name)
        if chart_file:
            # Формируем заголовок в зависимости от SMC-оценки
            if smc['score'] >= 2:
                header = f"🔥 <b>HIGH PROBABILITY SHORT (SMC)</b>"
            else:
                header = f"🚨 <b>АНОМАЛИЯ (ПАМП 15м)</b>"

            text = f"{header}\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
            text += f"💰 Цена: <code>{current_price}</code>\n\n"
            
            # Блок SMC Анализа
            text += f"🧠 <b>SMC Анализ (15м):</b>\n"
            if smc['vol_spike']:
                text += f"├ 📊 Объем: <b>В {smc['vol_ratio']:.1f} раз выше среднего!</b>\n"
            if smc['sweep']:
                text += f"├ 💧 Свип ликвидности: <b>Прокол {smc['recent_high']:.4f} и возврат!</b>\n"
            if not smc['vol_spike'] and not smc['sweep']:
                text += f"├ 📉 Слабых сигналов SMC нет.\n"
                
            text += f"\n🎯 <b>Ближайшая стена: {best_name}</b>\n"
            text += f"├ Лимитка: <code>{best_data['val']:.4f}</code>\n"
            text += f"└ Осталось: <b><code>{best_data['diff']:.2f}%</code></b>\n\n"
            
            if best_data['diff'] < 2.0:
                text += f"🔥 <i>Цена почти у цели. Заходим!</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

    if found == 0:
        print("Нет сигналов.")
        # send_telegram_text("🤖 Сканер SMC: сигналов нет.")

if __name__ == "__main__":
    main()
