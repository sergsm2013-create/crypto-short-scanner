import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {'User-Agent': 'Mozilla/5.0'}

MIN_VOLUME_USDT = 3_000_000  # Снизили объем, чтобы ловить больше альткоинов
MIN_24H_CHANGE = 10.0        # Монета должна быть в игре (рост от 10% за сутки)
MAX_SIGNALS_PER_RUN = 5

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

# --- ФУНКЦИЯ ПОИСКА EMA НА 1H И 15M ---
def get_intraday_emas(sym, exchange, current_price):
    emas = {}
    # Теперь смотрим на 1H и 15M
    timeframes = {"MEXC": [("Min60", "1H"), ("Min15", "15m")], "Bitget": [("1H", "1H"), ("15m", "15m")]}
    
    for tf, tf_name in timeframes[exchange]:
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

            # Ищем EMA 7, 14, 28, 50
            for span in [7, 14, 28, 50]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    # Нам нужны только EMA, которые ВЫШЕ текущей цены (куда лететь для шорта)
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 3.0: # Ищем только близкие цели (до 3%)
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

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
                if c['symbol'].endswith('_USDT'):
                    vol = float(c.get('amount24', 0))
                    change24h = float(c.get('riseFallRate', 0)) * 100
                    if vol > MIN_VOLUME_USDT and change24h >= MIN_24H_CHANGE:
                        coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice']), 'change24h': change24h})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                if c['symbol'].endswith("USDT"):
                    vol = float(c.get('usdtVolume', 0))
                    change24h = float(c.get('change24h', 0)) * 100
                    if vol > MIN_VOLUME_USDT and change24h >= MIN_24H_CHANGE:
                        coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr']), 'change24h': change24h})
    except: pass
    return sorted(coins, key=lambda x: x['change24h'], reverse=True)

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    all_coins = get_all_coins()
    found = 0
    
    for coin in all_coins:
        if found >= MAX_SIGNALS_PER_RUN: break
            
        sym, ex, current_price, change24h = coin['sym'], coin['ex'], coin['price'], coin['change24h']
        df = get_15m_data(sym, ex)
        if df is None or len(df) < 30: continue
        
        # 🔥 ЛОВИМ МОМЕНТ (Импульс прямо сейчас) 🔥
        open_p = df['open'].iloc[-1]
        current_pump = ((current_price - open_p) / open_p) * 100
        
        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].iloc[-20:-1].mean()
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
        
        # Если нет импульса (свеча вялая и объем низкий) - пропускаем
        if current_pump < 1.0 and vol_ratio < 2.0: continue

        # Ищем ближайшие EMA на 1H и 15m
        emas = get_intraday_emas(sym, ex, current_price)
        
        ema_text = ""
        if emas:
            sorted_emas = sorted(emas.items(), key=lambda x: x[1]['diff'])
            for name, data in sorted_emas[:3]: # Берем 3 ближайшие цели
                ema_text += f"├ <b>{name}:</b> <code>{data['val']:.4f}</code> (+{data['diff']:.1f}%)\n"
            ema_text += "\n<i>👆 Ставь лимитку на первую EMA. Если пробьет — на вторую.</i>"
        else:
            ema_text = "⚠️ <i>Ближайших EMA (1H/15m) над ценой нет. Цена в космосе.</i>"

        # Рисуем график
        chart_file = f"{sym}_scalp.png"
        df_plot = df.copy()
        df_plot.set_index('time', inplace=True)
        mpf.plot(df_plot.tail(40), type='candle', style='yahoo', volume=True, savefig=chart_file, title=f"{sym} 15m | SCALP +{change24h:.1f}%", tight_layout=True)

        # Текст сообщения
        text = f"⚡️ <b>СКАЛЬП-СИГНАЛ: ИМПУЛЬС!</b>\n"
        text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym.split('_')[0]}</b>\n\n"
        text += f"🔥 <b>Рост 24ч: {change24h:+.1f}%</b>\n"
        text += f"📊 <b>Импульс 15м: {current_pump:+.2f}% (Vol x{vol_ratio:.1f})</b>\n"
        text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
        text += f"🎯 <b>Ближайшие цели (1H / 15m):</b>\n{ema_text}"

        send_telegram_photo(chart_file, text)
        os.remove(chart_file)
        found += 1
        time.sleep(1)

if __name__ == "__main__":
    main()
