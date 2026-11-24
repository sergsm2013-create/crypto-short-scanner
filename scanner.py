import os
import requests
import pandas as pd
import mplfinance as mpf
import sys
import time

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {'User-Agent': 'Mozilla/5.0'}

MIN_VOLUME_USDT = 5_000_000
MIN_24H_CHANGE = 10.0  # Снизили до 10%, чтобы ловить больше монет в начале
MAX_SIGNALS_PER_RUN = 5

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            requests.post(url, files={'photo': photo}, data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=15)
    except Exception as e: print(f"TG photo error: {e}")

def get_htf_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": [("Hour4", "4H"), ("Day1", "1D")], "Bitget": [("4H", "4H"), ("1D", "1D")]}
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

            for span in [7, 14, 28, 50, 100, 200]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 10.0: 
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
        
        # 🔥 ЛОВИМ МОМЕНТ 🔥
        open_p = df['open'].iloc[-1]
        current_pump = ((current_price - open_p) / open_p) * 100
        
        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].iloc[-20:-1].mean()
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
        
        # Сигнал только если прямо сейчас идет импульс (рост от 1.5% или объем x3)
        is_impulse = current_pump >= 1.5 or vol_ratio >= 3.0
        if not is_impulse: continue # Если свеча вялая, пропускаем

        emas = get_htf_emas(sym, ex, current_price)
        ema_text = ""
        if emas:
            sorted_emas = sorted(emas.items(), key=lambda x: x[1]['diff'])
            for name, data in sorted_emas[:3]:
                ema_text += f"├ <b>{name}:</b> <code>{data['val']:.4f}</code> (+{data['diff']:.1f}%)\n"
        else:
            ema_text = "⚠️ <i>Ближайших EMA нет.</i>"

        chart_file = f"{sym}_anomaly.png"
        df_plot = df.copy()
        df_plot.set_index('time', inplace=True)
        mpf.plot(df_plot.tail(40), type='candle', style='yahoo', volume=True, savefig=chart_file, title=f"{sym} 15m | PUMP STARTING +{change24h:.1f}%", tight_layout=True)

        text = f"🚨 <b>НАЧАЛО ИМПУЛЬСА!</b>\n"
        text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym.split('_')[0]}</b>\n\n"
        text += f"🔥 <b>Рост 24ч: {change24h:+.1f}%</b>\n"
        text += f"⚡️ <b>Импульс 15м: {current_pump:+.2f}% (Vol x{vol_ratio:.1f})</b>\n"
        text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
        text += f"🧱 <b>Цели:</b>\n{ema_text}\n"
        text += f"<i>👆 Смотри на EMA. Если цена в них упрется — ШОРТ!</i>"

        send_telegram_photo(chart_file, text)
        os.remove(chart_file)
        found += 1
        time.sleep(1)

if __name__ == "__main__":
    main()
