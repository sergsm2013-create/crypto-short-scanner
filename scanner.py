import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

def draw_chart(sym, exchange):
    try:
        df_15m, df_1w = None, None
        
        # 1. 15-минутные свечи
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=10).json()
            if res.get('success'):
                data = res['data']
                df_15m = pd.DataFrame({
                    'time': pd.to_datetime(data['time'], unit='s'),
                    'open': data['open'], 'high': data['high'], 
                    'low': data['low'], 'close': data['close'], 'volume': data['vol']
                })
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=100", headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                data = res['data']
                df_15m = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_15m = df_15m.iloc[::-1]
                df_15m['time'] = pd.to_datetime(df_15m['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df_15m[col] = df_15m[col].astype(float)

        # 2. Недельные свечи (для EMA)
        if exchange == 'MEXC':
            res_1w = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Week1", headers=HEADERS, timeout=10).json()
            if res_1w.get('success'): df_1w = pd.DataFrame({'close': res_1w['data']['close']})
        elif exchange == 'Bitget':
            res_1w = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1week&limit=200", headers=HEADERS, timeout=10).json()
            if res_1w.get('code') == '00000':
                df_1w = pd.DataFrame(res_1w['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_1w['close'] = df_1w['close'].astype(float)

        if df_15m is None or df_15m.empty or df_1w is None or df_1w.empty: return None, None, None

        df_15m.set_index('time', inplace=True)
        
        # Считаем недельные EMA
        emas = {}
        for span in [7, 14, 28, 50, 100]:
            if len(df_1w) >= span: emas[span] = df_1w['close'].ewm(span=span, adjust=False).mean().iloc[-1]

        # Считаем Фибоначчи (по последним 50 свечам)
        recent_df = df_15m.tail(50)
        high_price = recent_df['high'].max()
        low_price = recent_df['low'].min()
        diff = high_price - low_price
        
        fib_levels = {}
        if diff > 0:
            fib_levels = {
                '0.786': high_price - diff * 0.786,
                '0.618': high_price - diff * 0.618,
                '0.5': high_price - diff * 0.5,
                '0.382': high_price - diff * 0.382
            }

        filename = f"{sym}_chart.png"
        
        # Готовим линии
        lines, colors, widths, styles = [], [], [], []
        
        # EMA (жирные линии)
        for span, val in emas.items():
            lines.append(val)
            if span == 7: colors.append('blue')
            elif span == 14: colors.append('orange')
            elif span == 28: colors.append('purple')
            elif span == 50: colors.append('green')
            elif span == 100: colors.append('red')
            widths.append(1.5)
            styles.append('-')

        # Фибоначчи (тонкие пунктиры)
        for level, val in fib_levels.items():
            lines.append(val)
            colors.append('gray')
            widths.append(0.8)
            styles.append(':')

        hlines = dict(hlines=lines, colors=colors, linestyle=styles, linewidths=widths) if lines else None
                      
        mpf.plot(df_15m.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 15m | Fib + 1W EMAs", tight_layout=True, hlines=hlines)
        
        return filename, emas, fib_levels
    except Exception as e:
        print(f"Chart error {sym}: {e}")
        return None, None, None

# --- Сбор данных (Снижен порог объема до 2 млн) ---
def get_mexc_data():
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        return [c for c in res.get('data', []) if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 2000000] if res.get('success') else []
    except: return []

def get_bitget_data():
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        return [c for c in res.get('data', []) if float(c.get('usdtVolume', 0)) > 2000000] if res.get('code') == '00000' else []
    except: return []

# --- Проверка пампов (Снижен порог до 2%) ---
def check_mexc_spike(sym):
    try:
        res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
        if res.get('success') and len(res['data']['close']) >= 2:
            change = ((float(res['data']['close'][-1]) - float(res['data']['close'][-2])) / float(res['data']['close'][-2])) * 100
            if change > 2.0: return change, float(res['data']['close'][-1])
    except: pass
    return None

def check_bitget_spike(sym):
    try:
        res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=2", headers=HEADERS, timeout=5).json()
        if res.get('code') == '00000' and len(res['data']) >= 2:
            change = ((float(res['data'][0][4]) - float(res['data'][0][1])) / float(res['data'][0][1])) * 100
            if change > 2.0: return change, float(res['data'][0][4])
    except: pass
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    print("Сканируем MEXC и Bitget на пампы (>2%)...")
    found = 0
    
    # Собираем монеты
    coins = [{'ex': 'MEXC', 'sym': c['symbol']} for c in get_mexc_data()] + \
            [{'ex': 'Bitget', 'sym': c['symbol']} for c in get_bitget_data()]
    
    unique_coins = {f"{c['ex']}_{c['sym']}": c for c in coins}.values()

    # Проверяем до 100 монет
    for coin in list(unique_coins)[:100]:
        if found >= 3: break # Максимум 3 сигнала за раз, чтобы не спамить
        sym, ex = coin['sym'], coin['ex']
        
        spike = check_mexc_spike(sym) if ex == 'MEXC' else check_bitget_spike(sym)
            
        if spike:
            change, price = spike
            chart_file, emas, fibs = draw_chart(sym, ex)
            
            if chart_file and emas:
                # Ищем ближайшую EMA сверху
                nearest_ema, nearest_val, min_diff = None, 0, 999
                for span, val in emas.items():
                    if val > price:
                        diff = ((val - price) / price) * 100
                        if diff < min_diff:
                            min_diff, nearest_ema, nearest_val = diff, span, val

                text = f"⚡️ <b>ПАМП (15m) + Аналитика</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"📈 Рост: <code>+{change:.2f}%</code> | 💰 Цена: <code>{price}</code>\n\n"
                
                if nearest_ema:
                    text += f"🎯 <b>Ближайшее сопротивление (1W):</b>\n"
                    text += f"├ <b>EMA {nearest_ema}: <code>{nearest_val:.4f}</code></b> ({(min_diff):.1f}%)\n"
                    
                    # Проверка на конфлюенс (совпадение EMA и Фибо)
                    confluence = False
                    if fibs:
                        for fib_lvl, fib_val in fibs.items():
                            if abs(fib_val - nearest_val) / nearest_val < 0.01: # Если разница меньше 1%
                                confluence = True
                                text += f"└ 📐 Совпадение с Fib {fib_lvl}!\n"
                                break
                    
                    if confluence:
                        text += f"\n🔥🔥 <b>КОНФЛЮЕНС!</b> Пересечение EMA и Фибо. Идеально для лимитки на шорт!\n"
                    elif min_diff < 3.0:
                        text += f"\n🔥 <b>ВНИМАНИЕ!</b> Цена почти у недельной EMA. Готовь шорт!\n"
                    else:
                        text += f"\n⚠️ До потолка еще далеко. Ждите.\n"
                
                text += f"\n📐 <b>Сетка Фибо (15m):</b>\n"
                if fibs:
                    for lvl, val in fibs.items():
                        text += f"├ {lvl}: <code>{val:.4f}</code>\n"

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

    if found == 0:
        print("Пампов нет.")

if __name__ == "__main__":
    main()
