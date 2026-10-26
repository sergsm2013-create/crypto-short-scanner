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

# --- Поиск EMA (строго сверху и не дальше 3%) ---
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
                    # Берем ТОЛЬКО EMA, которые ВЫШЕ цены
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 3.0: # Не дальше 3% (чтобы был быстрый вход)
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

def draw_chart(sym, df_15m, target_val, target_name):
    try:
        filename = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        # Красная линия - потолок
        hlines = dict(hlines=[target_val], colors=['red'], linestyle='-', linewidths=2.0)
        
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | {target_name}", tight_layout=True, hlines=hlines)
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
        
        # 1. Ищем памп (от 5%)
        open_p = df_15m['open'].iloc[-1]
        pump_change = ((current_price - open_p) / open_p) * 100
        if pump_change < 5.0: continue

        # 2. Тренд
        if not check_daily_trend(sym, ex): continue

        # 3. Ищем потолок СТРОГО СВЕРХУ (макс 3% расстояния)
        target_emas = get_target_emas(sym, ex, current_price)
        if not target_emas: continue # Если нет потолка, пропускаем (никаких старых зон снизу!)
        
        # Берем самую близкую EMA
        best_name, best_data = min(target_emas.items(), key=lambda x: x[1]['diff'])

        chart_file = draw_chart(sym, df_15m, best_data['val'], best_name)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            text = f"🚀 <b>{base_coin}</b>\n"
            text += f"Pump: <b>{pump_change:.2f}%</b>\n"
            text += f"{open_p:.4f} -> {current_price:.4f}\n\n"
            
            text += f"🎯 <b>Потолок: {best_name}</b>\n"
            text += f"├ Лимитка на шорт: <code>{best_data['val']:.4f}</code>\n"
            text += f"└ Осталось: <b>{best_data['diff']:.2f}%</b>\n\n"
            
            text += f"📉 <i>Цена летит в стену. Ставь лимитку на уровень выше.</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
