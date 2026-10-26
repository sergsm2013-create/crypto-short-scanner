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

# --- ПОИСК ЗОНЫ ПЛОТНОСТИ (EMA + Исторический хай) ---
def find_density_zone(sym, exchange, current_price):
    zones = []
    timeframes = {"MEXC": ["Min60", "Hour4", "Day1", "Week1"], "Bitget": ["1H", "4H", "1D", "1week"]}
    
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=3).json()
                if not res.get('success'): continue
                data = res['data']
                df = pd.DataFrame({'high': [float(h) for h in data['high']], 'close': [float(c) for c in data['close']]})
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=100", headers=HEADERS, timeout=3).json()
                if res.get('code') != '00000': continue
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                for col in ['high', 'close']: df[col] = df[col].astype(float)

            if len(df) < 50: continue
            
            # Считаем EMA 7, 14, 28
            ema7 = df['close'].ewm(span=7, adjust=False).mean().iloc[-1]
            ema14 = df['close'].ewm(span=14, adjust=False).mean().iloc[-1]
            ema28 = df['close'].ewm(span=28, adjust=False).mean().iloc[-1]

            # Ищем исторический максимум (High) за последние 50 свечей
            hist_high = df['high'].iloc[-50:].max()

            # Проверяем, есть ли СЛИЯНИЕ (Конфлюенс): 
            # Если EMA находится близко к историческому максимуму (разница < 0.5%)
            # и обе находятся над текущей ценой
            tf_name = tf.replace('Min60', '1H').replace('Hour4', '4H').replace('Day1', '1D').replace('Week1', '1W').replace('1week', '1W')
            
            for name, val in [("EMA 7", ema7), ("EMA 14", ema14), ("EMA 28", ema28)]:
                if val > current_price and hist_high > current_price:
                    diff = abs(val - hist_high) / hist_high * 100
                    if diff < 0.5: # Если EMA и исторический хай совпадают
                        zones.append({
                            'tf': tf_name, 
                            'level': max(val, hist_high), # Берем пик этой зоны
                            'type': 'Плотность (EMA + High)',
                            'diff': ((max(val, hist_high) - current_price) / current_price) * 100
                        })
        except: pass
    
    if not zones: return None
    # Берем самую близкую зону
    return min(zones, key=lambda x: x['diff'])

def draw_chart(sym, df_15m, target_val, zone_info):
    try:
        filename = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        # Рисуем красную линию на уровне плотности
        hlines = dict(hlines=[target_val], colors=['red'], linestyle='-', linewidths=2.0)
        
        title = f"{sym} 15m | Density: {zone_info['tf']} {zone_info['type']}"
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, title=title, tight_layout=True, hlines=hlines)
        return filename
    except: return None

def get_15m_data(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=3).json()
            if res.get('success'):
                data = res['data']
                return pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=3).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1]
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)
                return df
    except: pass
    return None

def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                vol, high, low = float(c.get('amount24', 0)), float(c.get('high24h', 0)), float(c.get('low24h', 0))
                if c['symbol'].endswith('_USDT') and vol > 5000000 and low > 0:
                    if ((high - low) / low) * 100 > 5.0:
                        coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice'])})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                vol, high, low = float(c.get('usdtVolume', 0)), float(c.get('high24h', 0)), float(c.get('low24h', 0))
                if vol > 5000000 and low > 0:
                    if ((high - low) / low) * 100 > 5.0:
                        coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr'])})
    except: pass
    return coins

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    start_time = time.time()
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    for coin in list(unique_coins):
        if found >= 3: break
        if time.time() - start_time > 240: break
            
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        df_15m = get_15m_data(sym, ex)
        if df_15m is None or len(df_15m) < 30: continue
        
        # 1. Ищем памп (от 3%)
        open_p = df_15m['open'].iloc[-1]
        pump_change = ((current_price - open_p) / open_p) * 100
        if pump_change < 3.0: continue

        # 2. Ищем ЗОНУ ПЛОТНОСТИ (где EMA совпадает с историческим хаем)
        zone = find_density_zone(sym, ex, current_price)
        if not zone: continue # Если нет плотности, пропускаем

        # 3. Если до зоны осталось меньше 2%
        if zone['diff'] <= 2.0:
            chart_file = draw_chart(sym, df_15m, zone['level'], zone)
            
            if chart_file:
                base_coin = sym.split('_')[0].replace('USDT', '')
                
                # Стоп ставим на 1.5% выше зоны плотности
                stop_loss = zone['level'] * 1.015
                tp1 = current_price * 0.97
                
                text = f"🚀 <b>{base_coin}</b>\n"
                text += f"Pump: <b>{pump_change:.2f}%</b>\n"
                text += f"{open_p:.4f} -> {current_price:.4f}\n\n"
                
                text += f"🧱 <b>ЗОНА ПЛОТНОСТИ (Конфлюенс):</b>\n"
                text += f"├ TF: <b>{zone['tf']}</b>\n"
                text += f"├ Сопротивление: <b>{zone['type']}</b>\n"
                text += f"├ <b>Лимитка на шорт: {zone['level']:.4f}</b>\n"
                text += f"└ Осталось: <b>{zone['diff']:.2f}%</b>\n\n"
                
                text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.4f}</code>\n"
                text += f"🟢 <b>Тейк-профит:</b> <code>{tp1:.4f}</code>\n\n"
                text += f"📉 <i>Цена летит в зону, где сходятся EMA и исторический максимум. Вероятность отскока высокая.</i>"

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

if __name__ == "__main__":
    main()
