import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time
from datetime import datetime

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

# --- Получаем EMA с 3-х старших таймфреймов (1H, 4H, 1D) ---
def get_target_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {
        "MEXC": ["Min60", "Hour4", "Day1"],
        "Bitget": ["1H", "4H", "1D"]
    }
    
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=5).json()
                if res.get('success') and 'close' in res['data']:
                    df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=100", headers=HEADERS, timeout=5).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue
            
            tf_name = tf.replace('Min', '').replace('Hour', 'H').replace('Day', 'D')
            for span in [7, 14, 28]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    # Берем только те EMA, которые находятся ВЫШЕ текущей цены (сопротивление)
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

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

# --- СБОР ВСЕХ ВОЛАТИЛЬНЫХ И ЛИКВИДНЫХ МОНЕТ ---
def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                vol = float(c.get('amount24', 0))
                high = float(c.get('high24h', 0))
                low = float(c.get('low24h', 0))
                if c['symbol'].endswith('_USDT') and vol > 5000000 and low > 0:
                    volat = ((high - low) / low) * 100
                    if volat > 5.0: # Берем монеты с размахом от 5%
                        coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice']), 'volat': volat})
    except: pass
    
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                vol = float(c.get('usdtVolume', 0))
                high = float(c.get('high24h', 0))
                low = float(c.get('low24h', 0))
                if vol > 5000000 and low > 0:
                    volat = ((high - low) / low) * 100
                    if volat > 5.0:
                        coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr']), 'volat': volat})
    except: pass
    
    # Сортируем по волатильности: самые бешеные монеты будут проверяться первыми
    coins.sort(key=lambda x: x['volat'], reverse=True)
    return coins

def check_fast_pump(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=3).json()
            if res.get('success') and len(res['data']['close']) >= 2:
                current_price = float(res['data']['close'][-1])
                open_price = float(res['data']['open'][-1])
                change = ((current_price - open_price) / open_price) * 100
                if change > 1.5: return change # Снизили порог до 1.5%
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2", headers=HEADERS, timeout=3).json()
            if res.get('code') == '00000' and len(res['data']) >= 2:
                current_price = float(res['data'][0][4])
                open_price = float(res['data'][0][1])
                change = ((current_price - open_price) / open_price) * 100
                if change > 1.5: return change
    except: pass
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    start_time = time.time()
    print("Сбор монет...")
    
    all_coins = get_all_coins()
    # Убираем дубликаты (если монета есть на двух биржах, оставляем одну)
    unique_coins = {c['sym']: c for c in all_coins}.values()
    # Превращаем обратно в список для сохранения сортировки
    sorted_coins = list(unique_coins)
    
    print(f"Найдено {len(sorted_coins)} активных монет. Начинаем проверку...")
    
    found = 0
    # Ограничиваем время работы 4 минутами (чтобы GitHub не убил процесс)
    for coin in sorted_coins:
        if found >= 5: break # Максимум 5 сигналов за раз
        if time.time() - start_time > 240: 
            print("Таймаут, завершаем...")
            break
            
        sym, ex, current_price, volat = coin['sym'], coin['ex'], coin['price'], coin['volat']
        
        pump_change = check_fast_pump(sym, ex)
        if not pump_change: continue

        print(f"Нашли памп: {sym} +{pump_change:.2f}% за 15м")
        
        target_emas = get_target_emas(sym, ex, current_price)
        if not target_emas: continue

        # Ищем самую близкую EMA сверху
        best_name, best_data = min(target_emas.items(), key=lambda x: x[1]['diff'])
        
        chart_file = draw_chart(sym, ex, best_data['val'], best_name)
        if chart_file:
            text = f"🚨 <b>АНОМАЛИЯ (ПАМП 15м: +{pump_change:.2f}%)</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
            text += f"🔥 Волатильность (24ч): <b>{volat:.1f}%</b>\n"
            text += f"💰 Цена: <code>{current_price}</code>\n\n"
            text += f"🎯 <b>Ближайшая стена: {best_name}</b>\n"
            text += f"├ Уровень (Лимитка): <code>{best_data['val']:.4f}</code>\n"
            text += f"└ Осталось: <b><code>{best_data['diff']:.2f}%</code></b>\n\n"
            
            if best_data['diff'] < 2.0:
                text += f"🔥 <b>ВНИМАНИЕ! Цена почти у цели. Идеально для шорта!</b>"
            else:
                text += f"⚡️ <i>Ставь лимитку на этот уровень.</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

    if found == 0:
        print("Нет сигналов.")
        # Раскомментируйте, если хотите получать отчет, когда рынок спокоен
        # send_telegram_text(f"🤖 Сканирование завершено. Проверено {len(sorted_coins)} монет. Резких пампов нет.")

if __name__ == "__main__":
    main()
