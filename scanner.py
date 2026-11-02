import os
import time
import uuid
import html
import logging
import requests
import numpy as np
import pandas as pd
import yfinance as yf
import mplfinance as mpf

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================================================
# 1. CONFIGURATION
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(BASE_DIR, "signals.csv")
LOG_FILE = os.path.join(BASE_DIR, "scanner.log")

MIN_VOLUME_USDT = 5_000_000
MAX_COINS = 80
MAX_SCAN_SECONDS = 180

MIN_RR = 2.0
ATR_PERIOD = 14
VOLUME_MULTIPLIER = 1.10

MAX_SIGNAL_AGE = 96 # 24 часа для 15M свечей
REQUEST_TIMEOUT = 10

# Стратегии
ENABLE_REVERSAL = True
ENABLE_TREND = True

# =========================================================
# 2. LOGGING & HTTP
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("TRADING_SCANNER")

session = requests.Session()
retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

# =========================================================
# 3. SIGNAL DATABASE
# =========================================================
COLUMNS = ["id", "market", "exchange", "symbol", "strategy", "direction", "entry_time",
           "entry", "sl", "tp", "risk", "status", "exit_time", "exit_price", "result_r", "bars"]

def load_signals():
    if not os.path.exists(SIGNALS_FILE): return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(SIGNALS_FILE)
        for col in COLUMNS:
            if col not in df.columns: df[col] = np.nan
        return df[COLUMNS]
    except: return pd.DataFrame(columns=COLUMNS)

def save_signals(df):
    temp = SIGNALS_FILE + ".tmp"
    df.to_csv(temp, index=False)
    os.replace(temp, SIGNALS_FILE)

# =========================================================
# 4. TELEGRAM
# =========================================================
def telegram_text(message):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        res = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                           data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=REQUEST_TIMEOUT)
        return res.json().get("ok", False)
    except Exception as e:
        log.error(f"TG text error: {e}")
        return False

def telegram_photo(path, caption):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        with open(path, "rb") as photo:
            res = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                               data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                               files={"photo": photo}, timeout=REQUEST_TIMEOUT)
        return res.json().get("ok", False)
    except Exception as e:
        log.error(f"TG photo error: {e}")
        return False

# =========================================================
# 5. INDICATORS & DATA NORMALIZATION
# =========================================================
def normalize_df(df):
    if df is None or df.empty: return None
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    req = ["open", "high", "low", "close"]
    if not all(c in df.columns for c in req): return None
    for col in req: df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns: df["volume"] = 0.0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df.dropna(subset=req)
    df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["low"] <= df["close"])]
    return df.sort_index()

def remove_open_candle(df):
    if df is None or len(df) < 3: return None
    return df.iloc[:-1].copy()

def calculate_ema(series, period): return series.ewm(span=period, adjust=False).mean()

def calculate_atr(df, period=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

def calculate_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def prepare_indicators(df):
    df = df.copy()
    df["ema20"] = calculate_ema(df["close"], 20)
    df["atr"] = calculate_atr(df, ATR_PERIOD)
    df["rsi"] = calculate_rsi(df)
    return df

# =========================================================
# 6. GLOBAL TREND FILTER (D1)
# =========================================================
def get_daily_bias(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y", interval="1d")
        if df.empty or len(df) < 200: return "NEUTRAL"
        
        ema200 = df['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
        current = df['Close'].iloc[-1]
        
        if current > ema200: return "BULLISH"
        if current < ema200: return "BEARISH"
        return "NEUTRAL"
    except: return "NEUTRAL"

def get_market_bias_4h(df):
    if df is None or len(df) < 105: return "NEUTRAL"
    df = remove_open_candle(df)
    if df is None or len(df) < 100: return "NEUTRAL"
    close = df["close"]
    ema50 = calculate_ema(close, 50).iloc[-1]
    ema100 = calculate_ema(close, 100).iloc[-1]
    price = close.iloc[-1]
    if price > ema50 and ema50 > ema100: return "BULLISH"
    if price < ema50 and ema50 < ema100: return "BEARISH"
    return "NEUTRAL"

# =========================================================
# 7. RISK MANAGEMENT & STRATEGIES
# =========================================================
def volume_confirmed(df):
    if len(df) < 25: return False
    current = df["volume"].iloc[-1]
    average = df["volume"].iloc[-21:-1].mean()
    return current >= average * VOLUME_MULTIPLIER if average > 0 else True

def calculate_trade_levels(direction, entry, structural_stop, atr):
    if not np.isfinite(atr) or atr <= 0: return None
    buffer = atr * 0.20
    if direction == "LONG":
        sl = structural_stop - buffer
        risk = entry - sl
        if risk <= 0 or risk > atr * 3: return None
        tp = entry + risk * MIN_RR
    else:
        sl = structural_stop + buffer
        risk = sl - entry
        if risk <= 0 or risk > atr * 3: return None
        tp = entry - risk * MIN_RR
    return {"entry": float(entry), "sl": float(sl), "tp": float(tp), "risk": float(risk), "rr": MIN_RR}

def find_reversal(df, bias_4h, bias_d1):
    if len(df) < 110: return None # Нужно минимум 100 свечей для свипа
    current = df.iloc[-1]
    
    # 🔥 СВИП ЗА 100 СВЕЧЕЙ (25 часов) 🔥
    history = df.iloc[-101:-1]
    structure = df.iloc[-7:-1] # Локальный слом структуры (последние 6 свечей)

    atr = current["atr"]
    rsi = current["rsi"]
    if pd.isna(atr) or pd.isna(rsi) or atr <= 0: return None
    if not volume_confirmed(df): return None

    prev_high = history["high"].max()
    prev_low = history["low"].min()
    struct_high = structure["high"].max()
    struct_low = structure["low"].min()

    # SHORT
    swept_high = current["high"] > prev_high and current["close"] < prev_high
    bearish_choch = current["close"] < struct_low and current["close"] < current["open"]
    
    if swept_high and bearish_choch:
        if bias_4h == "BULLISH" or bias_d1 == "BULLISH": return None # Запрет шорта в бычьем тренде
        if rsi > 75: return None
        
        levels = calculate_trade_levels("SHORT", current["close"], current["high"], atr)
        if levels: return {**levels, "direction": "SHORT", "strategy": "REVERSAL", "reason": "100-Candle Sweep + Bearish CHoCH"}

    # LONG
    swept_low = current["low"] < prev_low and current["close"] > prev_low
    bullish_choch = current["close"] > struct_high and current["close"] > current["open"]

    if swept_low and bullish_choch:
        if bias_4h == "BEARISH" or bias_d1 == "BEARISH": return None # Запрет лонга в медвежьем тренде
        if rsi < 25: return None
        
        levels = calculate_trade_levels("LONG", current["close"], current["low"], atr)
        if levels: return {**levels, "direction": "LONG", "strategy": "REVERSAL", "reason": "100-Candle Sweep + Bullish CHoCH"}
    return None

def find_trend_entry(df, bias):
    if len(df) < 30: return None
    current = df.iloc[-1]
    history = df.iloc[-9:-1]
    atr = current["atr"]
    ema = current["ema20"]
    rsi = current["rsi"]
    
    if pd.isna(atr) or pd.isna(ema) or pd.isna(rsi) or atr <= 0: return None
    if not volume_confirmed(df): return None

    if bias == "BULLISH":
        pullback = current["low"] <= ema + atr * 0.25 and current["close"] > ema
        impulse = current["close"] > history["high"].max() and current["close"] > current["open"]
        if pullback and impulse and 50 <= rsi <= 75:
            stop = history["low"].min()
            levels = calculate_trade_levels("LONG", current["close"], stop, atr)
            if levels: return {**levels, "direction": "LONG", "strategy": "TREND", "reason": "4H Bullish + EMA20 Pullback"}

    if bias == "BEARISH":
        pullback = current["high"] >= ema - atr * 0.25 and current["close"] < ema
        impulse = current["close"] < history["low"].min() and current["close"] < current["open"]
        if pullback and impulse and 25 <= rsi <= 50:
            stop = history["high"].max()
            levels = calculate_trade_levels("SHORT", current["close"], stop, atr)
            if levels: return {**levels, "direction": "SHORT", "strategy": "TREND", "reason": "4H Bearish + EMA20 Pullback"}
    return None

# =========================================================
# 8. DATA FETCHING
# =========================================================
def get_mexc_candles(symbol, interval="Min15"):
    try:
        url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}?interval={interval}"
        res = session.get(url, timeout=REQUEST_TIMEOUT).json()
        if not res.get("success"): return None
        data = res["data"]
        df = pd.DataFrame({"time": pd.to_datetime(data["time"], unit="s", utc=True), "open": data["open"], "high": data["high"], "low": data["low"], "close": data["close"], "volume": data.get("vol", 0)})
        df.set_index("time", inplace=True)
        return normalize_df(df)
    except: return None

def get_bitget_candles(symbol, granularity="15m"):
    try:
        res = session.get("https://api.bitget.com/api/v2/mix/market/candles", params={"symbol": symbol, "productType": "USDT-FUTURES", "granularity": granularity, "limit": "200"}, timeout=REQUEST_TIMEOUT).json()
        if res.get("code") != "00000": return None
        df = pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms", utc=True)
        df.set_index("time", inplace=True)
        return normalize_df(df)
    except: return None

def get_yahoo_data(symbol="GC=F", period="5d", interval="15m"):
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        if df.empty: return None
        df.columns = [c.lower() for c in df.columns]
        if df.index.tz is not None: df.index = df.index.tz_convert("UTC").tz_localize(None)
        return normalize_df(df)
    except: return None

def get_yahoo_4h(symbol="GC=F"):
    try:
        df = yf.Ticker(symbol).history(period="730d", interval="1h", auto_adjust=False)
        if df.empty: return None
        df.columns = [c.lower() for c in df.columns]
        if df.index.tz is not None: df.index = df.index.tz_convert("UTC").tz_localize(None)
        df = normalize_df(df)
        return df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    except: return None

# =========================================================
# 9. MESSAGE & CHART
# =========================================================
def price_format(value):
    if abs(value) >= 1000: return f"{value:.2f}"
    if abs(value) >= 1: return f"{value:.4f}"
    if abs(value) >= 0.01: return f"{value:.6f}"
    return f"{value:.8f}"

def format_signal(signal):
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    risk = abs(signal["entry"] - signal["sl"])
    reward = abs(signal["tp"] - signal["entry"])
    return (
        f"{emoji} <b>TRADING SCANNER 2.0</b>\n\n"
        f"<b>{signal['direction']} | {signal['strategy']}</b>\n"
        f"🏦 {signal['exchange']}\n🪙 <b>{signal['symbol']}</b>\n"
        f"📊 ТФ: 15M | Тренд 4H: {signal['bias_4h']} | Тренд D1: {signal['bias_d1']}\n"
        f"📉 RSI: {signal['rsi']:.1f}\n\n"
        f"🔎 <b>Причина:</b> {signal['reason']}\n\n"
        f"💰 Вход: <code>{price_format(signal['entry'])}</code>\n"
        f"🛑 SL: <code>{price_format(signal['sl'])}</code>\n"
        f"🎯 TP: <code>{price_format(signal['tp'])}</code>\n\n"
        f"⚖️ Risk/Reward: <b>1:{reward/risk:.2f}</b>"
    )

def create_chart(df, signal):
    filename = os.path.join(BASE_DIR, f"chart_{uuid.uuid4().hex[:8]}.png")
    plot = df.tail(60).copy()
    plot.index = pd.to_datetime(plot.index)
    plot.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}, inplace=True)
    
    hlines = dict(hlines=[signal["entry"], signal["sl"], signal["tp"]], colors=["blue", "red", "green"], linestyle="--", linewidths=1.3)
    mpf.plot(plot, type="candle", style="yahoo", volume=True, hlines=hlines, title=f"{signal['symbol']} | {signal['direction']} | 15M", tight_layout=True, savefig=dict(fname=filename, dpi=120))
    return filename

# =========================================================
# 10. CORE LOGIC
# =========================================================
def analyze_market(df15, df4h, market, exchange, symbol):
    if df15 is None or df4h is None: return None
    df15, df4h = remove_open_candle(df15), remove_open_candle(df4h)
    if df15 is None or df4h is None or len(df15) < 110 or len(df4h) < 100: return None

    bias_4h = get_market_bias_4h(df4h)
    bias_d1 = get_daily_bias(symbol if market == "GOLD" else symbol.replace("_", "").replace("USDT", "-USD"))
    
    df = prepare_indicators(df15)
    candidates = []

    if ENABLE_REVERSAL:
        sig = find_reversal(df, bias_4h, bias_d1)
        if sig: candidates.append(sig)
    if ENABLE_TREND:
        sig = find_trend_entry(df, bias_4h)
        if sig: candidates.append(sig)

    if len({x["direction"] for x in candidates}) > 1: return None
    if not candidates: return None

    signal = next((x for x in candidates if x["strategy"] == "REVERSAL"), candidates[0])
    signal.update({
        "market": market, "exchange": exchange, "symbol": symbol,
        "bias_4h": bias_4h, "bias_d1": bias_d1, "entry_time": df.index[-1].isoformat(),
        "rsi": float(df["rsi"].iloc[-1]), "atr": float(df["atr"].iloc[-1])
    })
    return signal

def has_active_signal(db, market, exchange, symbol):
    if db.empty: return False
    return not db[(db["market"] == market) & (db["exchange"] == exchange) & (db["symbol"] == symbol) & (db["status"] == "OPEN")].empty

def register_signal(signal, db):
    record = {
        "id": str(uuid.uuid4())[:8], "market": signal["market"], "exchange": signal["exchange"],
        "symbol": signal["symbol"], "strategy": signal["strategy"], "direction": signal["direction"],
        "entry_time": signal["entry_time"], "entry": signal["entry"], "sl": signal["sl"],
        "tp": signal["tp"], "risk": signal["risk"], "status": "OPEN", "exit_time": "",
        "exit_price": np.nan, "result_r": np.nan, "bars": 0
    }
    db = pd.concat([db, pd.DataFrame([record])], ignore_index=True)
    save_signals(db)
    return db

def process_market(market, exchange, symbol, db, market_data):
    key = (market, exchange, symbol)
    
    df15 = market_data.get(key) or get_mexc_candles(symbol, "Min15") if exchange == "MEXC" else market_data.get(key) or get_bitget_candles(symbol, "15m")
    htf_key = ("HTF", exchange, symbol)
    df4h = market_data.get(htf_key) or get_mexc_candles(symbol, "Hour4") if exchange == "MEXC" else market_data.get(htf_key) or get_bitget_candles(symbol, "4H")
    
    if df15 is not None: market_data[key] = df15
    if df4h is not None: market_data[htf_key] = df4h

    if df15 is None or df4h is None: return db
    if has_active_signal(db, market, exchange, symbol): return db

    signal = analyze_market(df15, df4h, market, exchange, symbol)
    if not signal: return db

    db = register_signal(signal, db)
    msg = format_signal(signal)
    
    try:
        chart = create_chart(prepare_indicators(remove_open_candle(df15)), signal)
        if not telegram_photo(chart, msg): telegram_text(msg)
        os.remove(chart)
    except Exception as e:
        log.error(f"Chart error: {e}")
        telegram_text(msg)
        
    log.info(f"SIGNAL {exchange} {symbol} {signal['direction']} {signal['strategy']}")
    return db

def process_gold(db, market_data):
    market, exchange, symbol = "GOLD", "Yahoo", "GC=F"
    key, htf_key = (market, exchange, symbol), ("HTF", exchange, symbol)
    
    df15 = market_data.get(key) or get_yahoo_data("GC=F", "5d", "15m")
    df4h = market_data.get(htf_key) or get_yahoo_4h("GC=F")
    
    if df15 is not None: market_data[key] = df15
    if df4h is not None: market_data[htf_key] = df4h
    if df15 is None or df4h is None: return db
    if has_active_signal(db, market, exchange, symbol): return db

    signal = analyze_market(df15, df4h, market, exchange, symbol)
    if not signal: return db

    db = register_signal(signal, db)
    msg = format_signal(signal)
    try:
        chart = create_chart(prepare_indicators(remove_open_candle(df15)), signal)
        if not telegram_photo(chart, msg): telegram_text(msg)
        os.remove(chart)
    except Exception as e:
        telegram_text(msg)
    return db

def get_all_coins():
    coins = []
    try:
        res = session.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=REQUEST_TIMEOUT).json()
        if res.get("success"):
            for item in res["data"]:
                if item.get("symbol", "").endswith("_USDT") and float(item.get("amount24", 0)) >= MIN_VOLUME_USDT:
                    coins.append({"market": "CRYPTO", "exchange": "MEXC", "symbol": item["symbol"], "volume": float(item.get("amount24", 0))})
    except: pass
    
    try:
        res = session.get("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"}, timeout=REQUEST_TIMEOUT).json()
        if res.get("code") == "00000":
            for item in res["data"]:
                if float(item.get("usdtVolume", 0)) >= MIN_VOLUME_USDT and item.get("symbol", "").endswith("USDT"):
                    coins.append({"market": "CRYPTO", "exchange": "Bitget", "symbol": item["symbol"], "volume": float(item.get("usdtVolume", 0))})
    except: pass
    return sorted(coins, key=lambda x: x["volume"], reverse=True)[:MAX_COINS]

# =========================================================
# 11. MAIN
# =========================================================
def main():
    if not BOT_TOKEN or not CHAT_ID: raise RuntimeError("Set BOT_TOKEN and CHAT_ID")
    log.info("TRADING SCANNER 2.0 STARTED")
    
    db = load_signals()
    market_data = {}
    
    # 1. Золото
    db = process_gold(db, market_data)
    
    # 2. Крипта
    coins = get_all_coins()
    start_time = time.time()
    
    for number, coin in enumerate(coins, 1):
        if time.time() - start_time > MAX_SCAN_SECONDS: break
        try:
            db = process_market(coin["market"], coin["exchange"], coin["symbol"], db, market_data)
        except Exception as e:
            log.error(f"{coin['symbol']}: {e}")
        time.sleep(0.15)
        
    save_signals(db)
    log.info("SCAN COMPLETED")

if __name__ == "__main__":
    try: main()
    except Exception as e: log.exception(f"FATAL ERROR: {e}")
