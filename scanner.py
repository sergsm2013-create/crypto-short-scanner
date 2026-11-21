def find_reversal(df, bias_4h, change_24h):
    if len(df) < 110: return None
    current = df.iloc[-1]
    history = df.iloc[-101:-1]
    structure = df.iloc[-7:-1]

    atr = current["atr"]
    rsi = current["rsi"]
    if pd.isna(atr) or pd.isna(rsi) or atr <= 0: return None
    if not volume_confirmed(df): return None

    prev_high = history["high"].max()
    prev_low = history["low"].min()
    struct_high = structure["high"].max()
    struct_low = structure["low"].min()

    # 🚫 ЖЕЛЕЗОБЕТОННЫЙ ЗАПРЕТ НА ШОРТ ПРОТИВ ТРЕНДА
    # Если монета выросла за 24ч больше 20% — шорт запрещен физически
    if change_24h > 20.0:
        return None

    # 🚫 Если 4H тренд явно бычий — тоже запрет
    if bias_4h == "BULLISH":
        return None

    # SHORT
    swept_high = current["high"] > prev_high and current["close"] < prev_high
    bearish_choch = current["close"] < struct_low and current["close"] < current["open"]
    
    if swept_high and bearish_choch and rsi < 75:
        levels = calculate_trade_levels("SHORT", current["close"], current["high"], atr)
        if levels:
            return {**levels, "direction": "SHORT", "strategy": "REVERSAL", 
                    "reason": f"100-Sweep + CHoCH | 24h: {change_24h:+.1f}%"}

    # 🚫 ЖЕЛЕЗОБЕТОННЫЙ ЗАПРЕТ НА ЛОНГ ПРОТИВ ТРЕНДА
    if change_24h < -20.0:
        return None
    if bias_4h == "BEARISH":
        return None

    # LONG
    swept_low = current["low"] < prev_low and current["close"] > prev_low
    bullish_choch = current["close"] > struct_high and current["close"] > current["open"]
    
    if swept_low and bullish_choch and rsi > 25:
        levels = calculate_trade_levels("LONG", current["close"], current["low"], atr)
        if levels:
            return {**levels, "direction": "LONG", "strategy": "REVERSAL",
                    "reason": f"100-Sweep + CHoCH | 24h: {change_24h:+.1f}%"}

    return None
