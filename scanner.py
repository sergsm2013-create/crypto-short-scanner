import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {'User-Agent': 'Mozilla/5.0'}

# --- Отправка фото (без кнопок) ---
def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            payload = {
                'chat_id': CHAT_ID, 
                'caption': caption, 
                'parse_mode': 'HTML'
            }
            requests.post(url, files={'photo': photo}, data=payload, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

# --- Поиск уровня "Откуда был слив" (Order Block) ---
def find_dump_level(df):
    try:
        # Ищем последнюю сильную красную свечу за последние 30 свечей
        for i in range(len(df)-2, len(df)-30, -1):
            candle = df.iloc[i]
            body = candle['open'] - candle['close']
            # Если это сильная красная свеча (тело больше половины диапазона)
            if candle['close'] < candle['open'] and body > (candle['high'] - candle['low']) * 0.5:
                return candle['high'] 
        return None
    except: return None

# --- Проверка глобального тренда (1D) ---
def check_daily_trend(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Day1", headers=HEADERS, timeout=3).json()
            if res.get('success'): df = pd.DataFrame({'close': [float(c) for c in res['data']['close']]})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1D&limit=100", headers=HEADERS, timeout=3).json()
            if res.get('code') == '00000': df = pd.DataFrame({'close': [float(c[4]) for c in res['data']][::-1]})
        if len(df) < 50: return True 
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        return df['close'].iloc[-1] < ema50 
    except: return True

# --- Поиск EMA (стен) ---
def get_target_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": ["Min60", "Hour4", "Day1", "Week1", "Month1"], "Bitget": ["1H", "4H", "1D", "1week", "1M"]}
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=3).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=200", headers=HEADERS, timeout=3).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue
            tf_name = tf.replace('Min60', '1H').replace('Hour4', '4H').replace('Day1', '1D').replace('Week1', '1W').replace('Month1', '1M').replace('1week', '1W')

            for span in [7, 14, 28, 50]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 3.0:
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

def draw_chart(sym, df_15m, target_val, target_name, dump_level):
    try:
        filename = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        lines = [target_val]
        colors = ['red']
        
        # Если нашли уровень слива, добавляем его как фиолетовую линию
        if dump_level:
            lines.append(dump_level)
            colors.append('purple')
        
        hlines = dict(hlines=lines, colors=colors, linestyle='-', linewidths=[2.0, 1.5])
        
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m", tight_layout=True, hlines=hlines)
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
        
        # 1. Ищем сильный памп (от 5%)
        open_p = df_15m['open'].iloc[-1]
        pump_change = ((current_price - open_p) / open_p) * 100
        if pump_change < 5.0: continue

        # 2. Проверяем глобальный тренд
        if not check_daily_trend(sym, ex): continue

        # 3. Ищем уровень слива и EMA
        dump_level = find_dump_level(df_15m)
        target_emas = get_target_emas(sym, ex, current_price)
        
        if not target_emas and not dump_level: continue
        
        best_name, best_val, best_diff = "", 0, 999
        
        if target_emas:
            best_ema = min(target_emas.items(), key=lambda x: x[1]['diff'])
            best_name = best_ema[0]
            best_val = best_ema[1]['val']
            best_diff = best_ema[1]['diff']
        elif dump_level and dump_level > current_price:
            best_name = "Зона слива (Order Block)"
            best_val = dump_level
            best_diff = ((dump_level - current_price) / current_price) * 100

        chart_file = draw_chart(sym, df_15m, best_val, best_name, dump_level)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            text = f"🚀 <b>{base_coin}</b>\n"
            text += f"Pump: <b>{pump_change:.2f}%</b>\n"
            text += f"{open_p:.4f} -> {current_price:.4f}\n\n"
            
            if best_name:
                text += f"🎯 <b>Сопротивление: {best_name}</b>\n"
                text += f"├ Уровень: <code>{best_val:.4f}</code>\n"
                text += f"└ Осталось: <b>{best_diff:.2f}%</b>\n\n"
            
            if dump_level:
                text += f"🧱 <b>Уровень слива (Order Block):</b> <code>{dump_level:.4f}</code>\n\n"
            
            text += f"📉 <i>Думаю получим откат с текущих от уровня, откуда ранее слив рисовали.</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
