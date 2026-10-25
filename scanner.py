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
    except Exception as e:
        print(f"TG text error: {e}")

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f"TG photo error: {e}")

def draw_chart(sym, exchange):
    try:
        df_15m, df_1w = None, None
        
        # 1. 15-минутные свечи (для графика и входа)
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
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                data = res['data']
                df_15m = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_15m = df_15m.iloc[::-1]
                df_15m['time'] = pd.to_datetime(df_15m['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df_15m[col] = df_15m[col].astype(float)

        # 2. Недельные свечи (для расчета EMA)
        if exchange == 'MEXC':
            res_1w = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Week1", headers=HEADERS, timeout=10).json()
            if res_1w.get('success'):
                df_1w = pd.DataFrame({'close': res_1w['data']['close']})
        elif exchange == 'Bitget':
            res_1w = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1week&limit=200", headers=HEADERS, timeout=10).json()
            if res_1w.get('code') == '00000':
                df_1w = pd.DataFrame(res_1w['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_1w['close'] = df_1w['close'].astype(float)
                df_1w = df_1w.iloc[::-1] # Старые сначала

        if df_15m is None or df_15m.empty or df_1w is None or df_1w.empty: 
            return None, None

        df_15m.set_index('time', inplace=True)
        
        # Считаем EMA на Недельном (1W) графике
        emas = {}
        for span in [7, 14, 28, 50, 100]:
            if len(df_1w) >= span: # Считаем только если хватает данных
                emas[span] = df_1w['close'].ewm(span=span, adjust=False).mean().iloc[-1]

        filename = f"{sym}_chart.png"
        
        # Готовим горизонтальные линии для графика
        lines = []
        colors = []
        for span, val in emas.items():
            lines.append(val)
            if span == 7: colors.append('blue')
            elif span == 14: colors.append('orange')
            elif span == 28: colors.append('purple')
            elif span == 50: colors.append('green')
            elif span == 100: colors.append('red')

        hlines = dict(hlines=lines, colors=colors, linestyle='-', linewidths=1.5) if lines else None
                      
        mpf.plot(df_15m, type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 15m | Lines = Weekly EMAs", tight_layout=True, hlines=hlines)
        
        return filename, emas
    except Exception as e:
        print(f"Chart error {sym}: {e}")
        return None, None

# (Функции сканирования без изменений)
def get_mexc_data():
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        return [c for c in res.get('data', []) if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 5000000] if res.get('success') else []
    except: return []

def get_bitget_data():
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        return [c for c in res.get('data', []) if float(c.get('usdtVolume', 0)) > 5000000] if res.get('code') == '00000' else []
    except: return []

def check_mexc_spike(sym):
    try:
        res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
        if res.get('success') and len(res['data']['close']) >= 2:
            change = ((float(res['data']['close'][-1]) - float(res['data']['close'][-2])) / float(res['data']['close'][-2])) * 100
            if change > 2.0: return change, float(res['data']['close'][-1]) # Чуть снизил порог до 2%, чтобы чаще ловить
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
    print("Сканируем на пампы...")
    found = 0
    coins = [{'ex': 'MEXC', 'sym': c['symbol']} for c in get_mexc_data()] + \
            [{'ex': 'Bitget', 'sym': c['symbol']} for c in get_bitget_data()]
    
    unique_coins = {f"{c['ex']}_{c['sym']}": c for c in coins}.values()

    for coin in list(unique_coins)[:70]:
        if found >= 3: break
        sym, ex = coin['sym'], coin['ex']
        spike = check_mexc_spike(sym) if ex == 'MEXC' else check_bitget_spike(sym)
            
        if spike:
            change, price = spike
            chart_file, emas = draw_chart(sym, ex)
            
            if chart_file and emas:
                # Ищем ближайшую EMA сверху (сопротивление)
                nearest_ema = None
                nearest_val = 0
                min_diff = 999
                
                for span, val in emas.items():
                    if val > price: # Ищем только те, что выше текущей цены
                        diff = ((val - price) / price) * 100
                        if diff < min_diff:
                            min_diff = diff
                            nearest_ema = span
                            nearest_val = val

                text = f"⚡️ <b>ПАМП (15m)</b> + Недельные EMA\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"📈 Рост: <code>+{change:.2f}%</code> | 💰 Цена: <code>{price}</code>\n\n"
                
                if nearest_ema:
                    text += f"🎯 <b>Ближайшее сопротивление (1W):</b>\n"
                    text += f"├ EMA {nearest_ema}: <code>{nearest_val:.4f}</code> ({(min_diff):.1f}% сверху)\n\n"
                    if min_diff < 3.0:
                        text += f"🔥 <b>ВНИМАНИЕ!</b> Цена почти у недельной EMA. Идеальная точка для лимитки на шорт!"
                    else:
                        text += f"⚠️ До сопротивления еще далеко. Ждите."
                else:
                    text += f"🚀 Цена выше всех недельных EMA (Космос). Шорт опасен."

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

    if found == 0:
        print("Пампов нет.")
        send_telegram_text("🤖 Бот активен (Ищем шорт от 1W EMA). Ждем пампов.")

if __name__ == "__main__":
    main()
