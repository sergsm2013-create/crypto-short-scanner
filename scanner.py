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

def send_telegram_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, headers=HEADERS, timeout=10)
    except Exception as e: print(f"TG text error: {e}")

# --- 1. Получаем EMA с 1H, 4H, 1D ---
def get_target_emas(sym, exchange, current_price):
    emas = {}
    timeframes = {"MEXC": ["Min60", "Hour4", "Day1"], "Bitget": ["1H", "4H", "1D"]}
    for tf in timeframes[exchange]:
        try:
            if exchange == 'MEXC':
                res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval={tf}", headers=HEADERS, timeout=5).json()
                if res.get('success') and 'close' in res['data']: df = pd.DataFrame({'close': res['data']['close']})
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
                    if val > current_price:
                        diff_pct = ((val - current_price) / current_price) * 100
                        emas[f"{tf_name} EMA {span}"] = {'val': val, 'diff': diff_pct}
            time.sleep(0.1)
        except: pass
    return emas

# --- 2. Расчет торгового плана (SL, TP, R:R) ---
def calculate_trade_plan(df_15m, entry_price):
    try:
        # Стоп-лосс: максимум за последние 10 свечей + 1.5% буфер
        recent_high = df_15m['high'].iloc[-10:].max()
        stop_loss = max(recent_high, entry_price) * 1.015 
        risk_pct = ((stop_loss - entry_price) / entry_price) * 100

        # Тейк-профит 1: минимум за последние 20 свечей
        tp1 = df_15m['low'].iloc[-20:].min()
        # Тейк-профит 2: абсолютный минимум за последние 50 свечей
        tp2 = df_15m['low'].iloc[-50:].min()

        # Защита от некорректных данных
        if tp1 >= entry_price: tp1 = entry_price * 0.96
        if tp2 >= tp1: tp2 = tp1 * 0.95

        reward1_pct = ((entry_price - tp1) / entry_price) * 100
        reward2_pct = ((entry_price - tp2) / entry_price) * 100

        rr_ratio = reward1_pct / risk_pct if risk_pct > 0 else 0

        return {
            'sl': stop_loss, 'risk': risk_pct,
            'tp1': tp1, 'reward1': reward1_pct,
            'tp2': tp2, 'reward2': reward2_pct,
            'rr': rr_ratio
        }
    except: return None

# --- 3. SMC Анализ (Объем и Свип) ---
def analyze_smc(df):
    if df is None or len(df) < 20: return None
    
    current_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].iloc[-20:-1].mean()
    vol_spike = current_vol / avg_vol if avg_vol > 0 else 0

    recent_high = df['high'].iloc[-20:-1].max()
    current_high = df['high'].iloc[-1]
    current_close = df['close'].iloc[-1]
    
    is_sweep = False
    sweep_level = 0
    if current_high > recent_high and current_close < recent_high:
        is_sweep = True
        sweep_level = recent_high

    return {'vol_spike': vol_spike, 'is_sweep': is_sweep, 'sweep_level': sweep_level}

# --- 4. Рисование графика ---
def draw_chart(sym, df_15m, target_ema_val, target_name, smc_data):
    try:
        filename = f"{sym}_chart.png"
        df = df_15m.copy()
        df.set_index('time', inplace=True)
        
        lines = [target_ema_val]
        colors = ['green']
        widths = [2.0]
        
        if smc_data['is_sweep']:
            lines.append(smc_data['sweep_level'])
            colors.append('yellow')
            widths.append(1.5)

        hlines = dict(hlines=lines, colors=colors, linestyle='-', linewidths=widths)
        mpf.plot(df.tail(50), type='candle', style='yahoo', volume=True, savefig=filename, title=f"{sym} 15m | Target: {target_name}", tight_layout=True, hlines=hlines)
        return filename
    except: return None

# --- 5. Получение данных 15м ---
def get_15m_data(sym, exchange):
    try:
        if exchange == 'MEXC':
            res = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15", headers=HEADERS, timeout=5).json()
            if res.get('success'):
                data = res['data']
                return pd.DataFrame({'time': pd.to_datetime(data['time'], unit='s'), 'open': data['open'], 'high': data['high'], 'low': data['low'], 'close': data['close'], 'volume': data['vol']})
        elif exchange == 'Bitget':
            res = requests.get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}&productType=USDT-FUTURES&granularity=15m&limit=50", headers=HEADERS, timeout=5).json()
            if res.get('code') == '00000':
                data = res['data']
                df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df = df.iloc[::-1]
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)
                return df
    except: pass
    return None

# --- 6. Сбор ликвидных и волатильных монет ---
def get_all_coins():
    coins = []
    try:
        res = requests.get("https://contract.mexc.com/api/v1/contract/ticker", headers=HEADERS, timeout=10).json()
        if res.get('success'):
            for c in res['data']:
                vol, high, low = float(c.get('amount24', 0)), float(c.get('high24h', 0)), float(c.get('low24h', 0))
                if c['symbol'].endswith('_USDT') and vol > 5000000 and low > 0:
                    volat = ((high - low) / low) * 100
                    if volat > 5.0:
                        coins.append({'ex': 'MEXC', 'sym': c['symbol'], 'price': float(c['lastPrice']), 'volat': volat})
    except: pass
    try:
        res = requests.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES", headers=HEADERS, timeout=10).json()
        if res.get('code') == '00000':
            for c in res['data']:
                vol, high, low = float(c.get('usdtVolume', 0)), float(c.get('high24h', 0)), float(c.get('low24h', 0))
                if vol > 5000000 and low > 0:
                    volat = ((high - low) / low) * 100
                    if volat > 5.0:
                        coins.append({'ex': 'Bitget', 'sym': c['symbol'], 'price': float(c['lastPr']), 'volat': volat})
    except: pass
    
    # Сортируем по волатильности (сначала самые активные)
    coins.sort(key=lambda x: x['volat'], reverse=True)
    return coins

def main():
    if not BOT_TOKEN or not CHAT_ID: sys.exit(1)
    
    start_time = time.time()
    all_coins = get_all_coins()
    unique_coins = {c['sym']: c for c in all_coins}.values()
    
    found = 0
    for coin in list(unique_coins):
        if found >= 3: break
        if time.time() - start_time > 240: break # Лимит 4 минуты
            
        sym, ex, current_price, volat = coin['sym'], coin['ex'], coin['price'], coin['volat']
        
        df_15m = get_15m_data(sym, ex)
        if df_15m is None or len(df_15m) < 2: continue
        
        # Проверка на быстрый памп (от 1.5% за 15 мин)
        open_p = df_15m['open'].iloc[-1]
        pump_change = ((current_price - open_p) / open_p) * 100
        if pump_change < 1.5: continue

        smc = analyze_smc(df_15m)
        if not smc: continue

        target_emas = get_target_emas(sym, ex, current_price)
        if not target_emas: continue

        best_name, best_data = min(target_emas.items(), key=lambda x: x[1]['diff'])
        
        plan = calculate_trade_plan(df_15m, best_data['val'])
        if not plan: continue

        chart_file = draw_chart(sym, df_15m, best_data['val'], best_name, smc)
        if chart_file:
            text = f"🚨 <b>АНОМАЛИЯ + SMC</b>\n"
            text += f"🏦 <b>{ex}</b> | 🪙 <b>{sym}</b>\n"
            text += f"💰 Цена: <code>{current_price}</code> | Волатильность: <b>{volat:.1f}%</b>\n\n"
            
            if smc['vol_spike'] > 2.5:
                text += f"📊 Объем: 🔥 Аномалия (x{smc['vol_spike']:.1f})\n"
            if smc['is_sweep']:
                text += f"⚠️ Ловушка (Sweep): Прокол <code>{smc['sweep_level']:.4f}</code>\n"
            if smc['vol_spike'] < 2.5 and not smc['is_sweep']:
                text += f"📉 Слабых сигналов SMC нет.\n"
            
            text += f"\n🎯 <b>Цель: {best_name}</b>\n"
            text += f"├ Лимитка (Вход): <code>{best_data['val']:.4f}</code>\n"
            text += f"└ Осталось: <b><code>{best_data['diff']:.2f}%</code></b>\n\n"
            
            text += f"🛡 <b>Торговый план:</b>\n"
            text += f"├ 🔴 Стоп: <code>{plan['sl']:.4f}</code> (-{plan['risk']:.1f}%)\n"
            text += f"├ 🟢 Тейк 1: <code>{plan['tp1']:.4f}</code> (+{plan['reward1']:.1f}%)\n"
            text += f"└ 🟢 Тейк 2: <code>{plan['tp2']:.4f}</code> (+{plan['reward2']:.1f}%)\n\n"
            
            text += f"⚖️ R:R = <b>1:{plan['rr']:.1f}</b>\n"
            
            if plan['rr'] >= 1.5:
                text += f"🔥 <b>ОТЛИЧНАЯ СДЕЛКА!</b> Прибыль превышает риск."
            elif plan['rr'] >= 1.0:
                text += f"✅ <b>СДЕЛКА СРЕДНЯЯ.</b> Можно входить малым объемом."
            else:
                text += f"⚠️ <b>ПЛОХАЯ СДЕЛКА.</b> Риск больше прибыли. ПРОПУСТИТЬ."

            if best_data['diff'] < 2.0 and not (smc['is_sweep'] or smc['vol_spike'] > 2.0):
                text += f"\n\n⚠️ <i>Цена у цели, но НЕТ SMC (объема/свипа). ЖДИ ПОДТВЕРЖДЕНИЯ!</i>"

            send_telegram_photo(chart_file, text)
            os.remove(chart_file)
            found += 1
            time.sleep(1)

    if found == 0:
        print("Нет сигналов.")
        # send_telegram_text("🤖 Сканер завершил работу. Сигналов нет.")

if __name__ == "__main__":
    main()
