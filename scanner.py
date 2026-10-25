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

def get_data_and_emas(sym, exchange):
    try:
        df_1d, df_1w = None, None
        
        # Берем дневной график для отрисовки
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Day1", headers=HEADERS, timeout=10).json()
            if res.get('success'):
                data = res['data']
                df_1d = pd.DataFrame({
                    'time': pd.to_datetime(data['time'], unit='s'),
                    'open': data['open'], 'high': data['high'], 
                    'low': data['low'], 'close': data['close'], 'volume': data['vol']
                })
            # Недельный график для EMA (или Месячный, если есть данные)
            res_w = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Week1", headers=HEADERS, timeout=10).json()
            if res_w.get('success'): df_1w = pd.DataFrame({'close': res_w['data']['close']})
            
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1D&limit=100", headers=HEADERS, timeout=10).json()
            if res.get('code') == '00000':
                data = res['data']
                df_1d = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_1d = df_1d.iloc[::-1]
                df_1d['time'] = pd.to_datetime(df_1d['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df_1d[col] = df_1d[col].astype(float)
            
            res_w = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=1week&limit=200", headers=HEADERS, timeout=10).json()
            if res_w.get('code') == '00000':
                df_1w = pd.DataFrame(res_w['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df_1w['close'] = df_1w['close'].astype(float)

        if df_1d is None or df_1d.empty or df_1w is None or df_1w.empty: return None, None

        df_1d.set_index('time', inplace=True)
        
        # Считаем Недельные EMA
        emas = {}
        for span in [7, 14, 28, 50, 100, 200]:
            if len(df_1w) >= span: 
                emas[span] = df_1w['close'].ewm(span=span, adjust=False).mean().iloc[-1]

        return df_1d, emas
    except Exception as e:
        print(f"Data error {sym}: {e}")
        return None, None

def draw_chart(sym, df_1d, emas, target_ema):
    try:
        filename = f"{sym}_chart.png"
        
        # Оставляем последние 60 дней для графика
        plot_df = df_1d.tail(60)
        
        hlines = dict(hlines=[target_ema], colors=['green'], linestyle='-', linewidths=2.0)
        
        mpf.plot(plot_df, type='candle', style='yahoo', volume=True, savefig=filename, 
                 title=f"{sym} 1D | Target EMA: {target_ema:.4f}", tight_layout=True, hlines=hlines)
        return filename
    except Exception as e:
        print(f"Chart error {sym}: {e}")
        return None

# --- Сбор данных ---
def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            coins += [{'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice'])} for c in res['data'] if c['symbol'].endswith('_USDT') and float(c.get('amount24', 0)) > 2000000]
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            coins += [{'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr'])} for c in res['data'] if float(c.get('usdtVolume', 0)) > 2000000]
    except: pass
    return coins

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    print("Сканируем монеты на близость к EMA (1W)...")
    
    all_coins = get_all_coins()
    # Убираем дубликаты (если монета есть и на MEXC, и на Bitget)
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    # Проверяем первые 80 монет (чтобы не превысить лимиты API)
    for coin in list(unique_coins)[:80]:
        if found >= 3: break
        
        sym, ex, current_price = coin['sym'], coin['ex'], coin['price']
        
        df_1d, emas = get_data_and_emas(sym, ex)
        if not emas: continue

        # Ищем EMA, к которой цена ближе всего
        best_ema_span = None
        best_ema_val = 0
        min_diff_pct = 999
        
        for span, val in emas.items():
            # Считаем расстояние в процентах
            diff_pct = abs(current_price - val) / val * 100
            
            # Если цена в пределах 2.5% от EMA (и EMA выше цены, т.е. это сопротивление)
            if diff_pct < min_diff_pct and diff_pct < 2.5 and val > current_price * 0.98:
                min_diff_pct = diff_pct
                best_ema_span = span
                best_ema_val = val

        # Если нашли подходящую EMA (7, 14, 28, 50, 100, 200)
        if best_ema_span:
            chart_file = draw_chart(sym, df_1d, emas, best_ema_val)
            if chart_file:
                text = f"🧲 <b>ЦЕНА У УРОВНЯ EMA</b>\n"
                text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
                text += f"💰 Цена: <code>{current_price}</code>\n"
                text += f"🎯 <b>EMA {best_ema_span} (1W):</b> <code>{best_ema_val:.4f}</code>\n"
                text += f"📏 Расстояние: <code>{min_diff_pct:.2f}%</code>\n\n"
                
                if best_ema_span in [7, 14]:
                    text += f"🔥 <b>Цена подошла к EMA {best_ema_span}!</b> Ставь лимитку на шорт прямо на этот уровень."
                else:
                    text += f"⚠️ Цена у EMA {best_ema_span}. Готовься к отскоку."

                send_telegram_photo(chart_file, text)
                os.remove(chart_file)
                found += 1
                time.sleep(1)

    if found == 0:
        print("Нет монет у EMA.")

if __name__ == "__main__":
    main()
