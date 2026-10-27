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

# --- ПОИСК ТОЛЬКО НЕДЕЛЬНЫХ И МЕСЯЧНЫХ EMA ---
def get_htf_emas(sym, exchange, current_price, direction):
    emas = {}
    
    # Только 1W и 1M (Week1, Month1)
    timeframes = {
        "MEXC": [("Week1", "1W"), ("Month1", "1M")], 
        "Bitget": [("1week", "1W"), ("1M", "1M")]
    }
    
    for tf, tf_name in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=5).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
                else: continue
            elif exchange == 'Bitget':
                res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity={tf}&limit=200", headers=HEADERS, timeout=5).json()
                if res.get('code') == '00000' and res.get('data'):
                    df = pd.DataFrame(res['data'], columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                    df['close'] = df['close'].astype(float)
                else: continue

            # Нас интересуют EMA 7, 14, 28, 50 на старших ТФ
            for span in [7, 14, 28, 50]:
                if len(df) >= span:
                    val = df['close'].ewm(span=span, adjust=False).mean().iloc[-1]
                    
                    # Для ШОРТА - ищем EMA СВЕРХУ
                    if direction == 'SHORT' and val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        if diff_pct <= 5.0: # Если до стены не больше 5%
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
                    
                    # Для ЛОНГА - ищем EMA СНИЗУ
                    elif direction == 'LONG' and val < current_price:
                        diff_pct = ((current_price - val) / current_price) * 100
                        if diff_pct <= 5.0:
                            emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

def draw_chart(sym, df_15m, target_val, target_name, direction):
    try:
        filename = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        color = 'red' if direction == 'SHORT' else 'green'
        hlines = dict(hlines=[target_val], colors=[color], linestyle='-', linewidths=2.0)
        
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | {direction} {target_name}", tight_layout=True, hlines=hlines)
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
        
        # Проверяем изменение за 15 минут
        open_p = df_15m['open'].iloc[-1]
        change_pct = ((current_price - open_p) / open_p) * 100
        
        direction = None
        if change_pct >= 2.0: direction = 'SHORT'
        elif change_pct <= -2.0: direction = 'LONG'
        
        if not direction: continue

        # 🔥 Ищем ТОЛЬКО Недельные (1W) и Месячные (1M) стены 🔥
        wall = get_htf_emas(sym, ex, current_price, direction)
        if not wall: continue
        
        # Берем самую близкую стену
        best_wall_name, best_wall_data = min(wall.items(), key=lambda x: x[1]['diff'])

        chart_file = draw_chart(sym, df_15m, best_wall_data['val'], best_wall_name, direction)
        
        if chart_file:
            base_coin = sym.split('_')[0].replace('USDT', '')
            
            if direction == 'SHORT':
                stop_loss = best_wall_data['val'] * 1.015
                tp = best_wall_data['val'] * 0.95
                emoji = "🔴"
                action = "ШОРТ"
            else:
                stop_loss = best_wall_data['val'] * 0.985
                tp = best_wall_data['val'] * 1.05
                emoji = "🟢"
                action = "ЛОНГ"

            text = f"{emoji} <b>СИГНАЛ: {action} (1W/1M)</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{base_coin}</b>\n"
            text += f"📊 Изм. 15м: <b>{change_pct:+.2f}%</b>\n"
            text += f"💰 Цена: <code>{current_price:.4f}</code>\n\n"
            
            text += f"🧱 <b>Стена (Старший ТФ):</b>\n"
            text += f"├ <b>{best_wall_name}</b>\n"
            text += f"├ <b>Лимитка: {best_wall_data['val']:.4f}</b>\n"
            text += f"└ Осталось: <b>{best_wall_data['diff']:.2f}%</b>\n\n"
            
            text += f"🛑 <b>Стоп-лосс:</b> <code>{stop_loss:.4f}</code>\n"
            text += f"🟢 <b>Тейк-профит:</b> <code>{tp:.4f}</code>\n\n"
            text += f"⚡️ <i>Ставь лимитку на указанный уровень!</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

if __name__ == "__main__":
    main()
