"""B17 hypothesis registry generator: pump & dump riding, all lifecycle stages, second-level to multi-day.
Writes research/batch_B17_registry.yaml (pre-registration). Nothing here reads price data.

Protocol (applies to every hypothesis):
  discovery 2024-04..2025-12 | validation 2026-01..2026-09 (selection only, already read by earlier batches)
  confirmation = FORWARD paper test, pre-registered stop date and n, for anything that survives validation
  hierarchical FDR: BH q=0.10 across family "best" statistics (max-stat bootstrap), then BH q=0.10 within family
  every family has a random-entry control (same universe, same timestamps' hour-of-day distribution) and must beat it
  every trade hypothesis reports mean net, day-clustered CI, MAE p90/p99 (adverse excursion), and slices
    (HMM regime, size bucket, liquidity tercile, korean_listed, hour block KST, year third)
  costs: 0.05%/side + slippage from bookDepth at entry size $5k (not the old volume proxy) + funding; latency added explicitly
"""
from __future__ import annotations

import itertools
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
H = []

DATA = {
    "k1m": "have", "metrics1h": "have", "metrics5m": "download: data.binance.vision futures/um/daily/metrics (5-min OI, L/S, taker)",
    "aggTrades": "have method (on-demand around events)", "bookDepth": "have (1-min snapshots, +-1..5%)",
    "bookTicker": "download: data.binance.vision futures/um/daily/bookTicker (best bid/ask every change; sample around events only, ~1-3 GB/day/liquid coin)",
    "liquidationSnapshot": "download: data.binance.vision futures/um/daily/liquidationSnapshot + live WS !forceOrder@arr recorder",
    "spot1s": "download: data.binance.vision spot/daily/klines/{SYMBOL}/1s",
    "bybit_trades": "download: public.bybit.com/trading/{SYMBOL}/ daily trade CSVs",
    "upbit_ws": "record now: Upbit websocket trade+orderbook -> data/cache/upbit_ws (no bulk history exists)",
    "bithumb_ws": "record now: Bithumb websocket trade+orderbook",
    "hyperliquid": "download: s3://hyperliquid-archive (requester pays, small) or Info API trades",
    "coinalyze": "API key (free tier): cross-exchange OI + liquidations 1-min",
    "telegram": "scrape with Telethon (유리's own API id) public pump channels -> event labels",
    "cryptopanic": "API (free): news timestamps per coin", "santiment": "sanpy free tier: social volume (delayed)",
    "bigquery": "have (crypto_ethereum token_transfers)", "notices": "have", "unlocks": "have", "kimchi": "have (upbit1h vs binance)",
}

EXITS_L = {"t15m": "exit +15m", "t1h": "exit +1h", "t4h": "exit +4h", "t24h": "exit +24h",
           "trail2": "trail 2x 1h ATR from running high", "hazard": "B15_1 hazard exit", "tp10": "take-profit +10% else +24h"}
STOPS = {"none": "no stop", "s5": "hard stop 5%", "s10": "hard stop 10%", "atr": "stop 1.5x 1h ATR"}
RES = {"1s": "1-second", "15s": "15-second", "1m": "1-minute", "5m": "5-minute", "1h": "hourly"}


def add(fam, stage, statement, data, res, side, entry, exit_, stop="none", horizon=None, metric="mean net/trade", extra=None):
    H.append(dict(id=f"B17_{fam}_{len([h for h in H if h['family']==fam])+1:03d}", family=fam, stage=stage, statement=statement,
                  data=data, resolution=res, side=side, entry=entry, exit=exit_, stop=stop, horizon=horizon, metric=metric,
                  runnable_now=all(DATA[d].startswith("have") for d in data), **(extra or {})))


# F01 precursor anticipation: enter BEFORE the pump (never tested as a trade at the right granularity)
for hz, model, ex in itertools.product(["1h", "3h", "6h", "12h", "24h"], ["logit", "lgbm", "gru"], ["tp10", "t24h", "trail2"]):
    add("F01_precursor_long", "pre-onset", f"Composite of the 21 passing precursors ({model}) trained on P(pump within {hz}); "
        f"long when P > discovery threshold (top 0.5% coin-hours), size 1/n concurrent; exit {EXITS_L[ex]}. Beats random-entry control.",
        ["k1m", "metrics1h", "bookDepth", "kimchi", "bigquery"], "1h", "long", f"P(pump<{hz}) > thr", EXITS_L[ex], horizon=hz)
# F02 onset detection at second-level resolution
TRIG = {"tps": "trades/sec > 8x 1h baseline", "taker": "taker-buy share 15s > 0.8 with volume 5x", "spread": "bookTicker spread collapse + ask size drop 70%",
        "sweep": "ask side +1% swept (bookDepth delta) within 60s", "liq": "short liquidation cascade >= 3 forceOrders in 30s",
        "oi": "5-min OI jump > 2% with price < +2%", "cp": "BOCPD change-point on 1s returns (hazard 1/200)", "hawkes": "Hawkes branching ratio of buy arrivals > 0.8"}
TDATA = {"tps": ["aggTrades"], "taker": ["aggTrades"], "spread": ["bookTicker"], "sweep": ["bookDepth", "aggTrades"], "liq": ["liquidationSnapshot"],
         "oi": ["metrics5m"], "cp": ["aggTrades"], "hawkes": ["aggTrades"]}
for (tk, desc), res, lat, ex in itertools.product(TRIG.items(), ["1s", "15s", "1m"], ["5s", "30s", "120s"], ["t15m", "t1h", "t4h", "trail2", "hazard"]):
    if tk == "oi" and res != "1m":
        continue
    add("F02_onset_long", "onset", f"Trigger '{desc}' evaluated on {RES[res]} bars; enter long {lat} after trigger (latency modelled), {EXITS_L[ex]}.",
        TDATA[tk] + ["k1m"], res, "long", f"{tk}+{lat}", EXITS_L[ex], extra={"latency": lat, "trigger": tk})
# F03 mid-pump continuation filters (long at m0+5m only if filter true)
FILT = ["volume decay ratio (5m/1m) > 0.6", "taker share minutes 2-5 > minute 0-1", "OI up since onset (real money, not spot-led)",
        "spot share rising (spot-led continuation)", "Korean share rising", "depth refill speed on ask < 50%/min", "price acceleration positive at m0+5",
        "no exchange notice in 24h", "coin's last pump > 30d ago", "BTC 1h return > 0", "HMM state == bull", "funding < 0.01%/8h (not crowded)",
        "thin-book precursor was active (depth/volume < p20)", "pump is the coin's first +10% of the day", "market pump count in +-3 min < 3 (not a burst)"]
for f, ex, st in itertools.product(FILT, ["t1h", "t24h", "hazard"], ["none", "s5"]):
    add("F03_continuation_filter", "mid-pump", f"Long at m0+5m only when: {f}; {EXITS_L[ex]}, {STOPS[st]}. Filtered mean beats unfiltered mean (paired, day-clustered).",
        ["k1m", "metrics5m", "bookDepth", "upbit_ws", "notices"], "1m", "long", "m0+5m if filter", EXITS_L[ex], st)
# F04 exhaustion / dump short with squeeze control (B15_6 lesson: the tail is the problem)
DTRIG = ["3% drawdown from running high", "5% drawdown", "first 5-min bar with taker share < 0.4", "OI drops 3% from its post-onset high (long liquidation begins)",
         "funding next-8h estimate > 0.1%", "ask depth at +1% doubles within 10 min (sellers stack)", "trades/sec falls below 25% of onset rate", "bookTicker spread widens 3x"]
for d, st, ex, flt in itertools.product(DTRIG, ["s5", "s10", "atr"], ["50% retrace or 24h", "t4h"], ["all", "whale-driven only", "size >= 25% only"]):
    add("F04_dump_short", "post-peak", f"Short at next minute after '{d}' ({flt}); exit {ex}; {STOPS[st]}. Mean net > 0 AND MAE p90 < 15%.",
        ["k1m", "aggTrades", "metrics5m", "bookDepth", "bookTicker", "liquidationSnapshot"], "1m", "short", d, ex, st, extra={"filter": flt})
# F05 squeeze prediction / abstention
for hz, mdl in itertools.product(["1h", "4h", "24h"], ["lgbm", "gru", "conformal-abstain"]):
    add("F05_squeeze_model", "post-peak", f"Predict MAE > 20% within {hz} for post-peak shorts ({mdl}); abstain when P > thr. Filtered short net > 0.",
        ["k1m", "aggTrades", "metrics5m", "liquidationSnapshot"], "1m", "short", "post-peak short if not flagged", f"exit {hz}", "s10", hz, metric="AUC + filtered net")
# F06 multi-day / serial
for s in ["coins that pumped >= 2x in 30d pump again within 7d (serial pumper long on next precursor)", "post-pump drift day 2-3 negative (short at +24h)",
          "post-pump drift day 3-7 positive after full retrace (long at +72h if retrace >= 100%)", "listing-day pumps (Upbit/Binance notice < 24h) retrace less: long at m0+5m",
          "unlock-week pumps retrace more: short after peak", "pumps on weekend retrace more", "pumps in KST 09-12 (Korean open) continue more",
          "pumps during US session (14-21 UTC) retrace faster", "second pump of the same coin within 6h continues less", "pump after 7d of falling volume continues more"]:
    add("F06_multiday", "multi-day", s + ". Net > 0 with CI; beats random control.", ["k1m", "notices", "unlocks", "upbit_ws"], "1h", "long/short", "see statement", "24h-7d")
# F07 cross-venue lead-lag
for src, hz, side in itertools.product(["upbit_ws", "bithumb_ws", "spot1s", "hyperliquid", "bybit_trades", "coinalyze"], ["1m", "5m", "15m", "1h"], ["long", "short"]):
    add("F07_venue_leadlag", "onset", f"{src} price/flow leads Binance perp by {hz}: {'follow' if side=='long' else 'fade'} a +5% {src} move not yet in the perp; exit {hz} after.",
        [src, "k1m"], "1m" if src not in ("spot1s",) else "1s", side, f"{src} leads", f"exit +{hz}", horizon=hz)
# F08 regime conditioning of the best per-stage strategy
for reg, stg in itertools.product(["HMM state", "BTC 7d trend sign", "funding regime (median funding sign)", "hour block KST (00-08/09-17/18-23)", "weekend"],
                                  ["F01 best", "F02 best", "F03 best", "F04 best"]):
    add("F08_regime", "all", f"{stg} conditioned on {reg}: at least one state has net CI > 0 and the state is stable across year-thirds.", ["k1m"], "1h", "both", stg, "as parent")
# F09 coordination
for s in ["Hawkes self-excitation of pump onsets across coins (branching ratio) predicts a burst continuing", "Telegram pump-call timestamp precedes onset by > 60s: enter at call",
          "Telegram-labelled pumps dump deeper than organic (short after peak)", "cross-coin sector sympathy: second coin in the same sector pumps within 15 min of the first",
          "coordinated burst (>= 10 coins) = market beta: long BTC-hedged basket continues 1h", "pump onset clustering by minute-of-hour (bot schedules)"]:
    add("F09_coordination", "onset", s, ["k1m", "telegram", "aggTrades"], "1m", "both", "see statement", "1h/24h")
# F10 sizing / portfolio
for s in ["fractional Kelly (1/4) from calibrated hazard P vs fixed size", "vol-targeted size (10% daily vol) vs fixed", "max 3 concurrent pump positions vs unlimited",
          "size scaled by depth at +-1% (slippage-aware)", "size zero when MAE-model P > 0.3", "pyramiding: add at +3% if hazard low", "scale out 50% at +5%", "no averaging down (control)"]:
    add("F10_sizing", "all", s + ". Sharpe and max drawdown of the equity path on the SAME entries.", ["k1m", "bookDepth"], "1m", "both", "F02/F03 best", "as parent", metric="Sharpe, MDD")
# F11 execution
for s in ["maker entry at -0.3% limit vs taker (fill rate modelled from bookTicker)", "iceberg 5 slices over 60s vs one shot", "entry on Bybit when Binance spread wider",
          "exit via limit at +X% vs market at signal", "skip when bookDepth +-1% < $50k", "latency 5s vs 30s vs 120s (paired)"]:
    add("F11_execution", "all", s, ["bookTicker", "bookDepth", "bybit_trades"], "1s", "both", "F02 best", "as parent", metric="net after modelled fills")
# F12 ML architecture sweep for onset targets
for mdl, win, tgt in itertools.product(["lgbm", "tcn", "gru", "transformer", "hawkes-feat+lgbm"], ["5m ticks", "15m ticks", "60m 1-min bars"],
                                       ["+5% within 15m", "peak within 5m", "drawdown > 10% within 6h"]):
    add("F12_ml_onset", "onset", f"{mdl} on {win} predicting '{tgt}'; purged walk-forward, isotonic calibration; trade at thr fixed on discovery.",
        ["aggTrades", "k1m", "metrics5m"], "1s", "both", "model P > thr", "target horizon", metric="AUC, PR-AUC, net")
# F13 change-point / motif
for m, ex in itertools.product(["BOCPD on 1s returns", "matrix-profile motif of the last 100 pumps' first 60s", "DTW nearest-medoid at 60s (not 10m)"], ["t15m", "t1h", "hazard"]):
    add("F13_changepoint", "onset", f"{m} as trigger; long, {EXITS_L[ex]}.", ["aggTrades", "k1m"], "1s", "long", m, EXITS_L[ex])
# F14 offline RL exit
for s in ["CQL exit policy on B15 paths vs hazard exit", "IQL exit vs hazard", "RL with squeeze penalty (CVaR-10 objective)", "RL entry+exit jointly vs rule"]:
    add("F14_rl_exit", "mid-pump", s + "; evaluated on validation pumps only, no reward leakage.", ["k1m"], "1m", "long", "m0+1", "policy")
# F15 stops specific to pumps
for st, ent in itertools.product(["s5", "s10", "atr", "time+s10", "trail+s10"], ["m0+1 long", "precursor long", "post-peak short"]):
    add("F15_pump_stops", "all", f"{st} on {ent}: loss-cutting efficiency > 0 AND mean net not worse than no-stop (paired).", ["k1m"], "1m", "both", ent, "24h", st, metric="LCE, net")
# F16 Korean-specific
for s in ["kimchi premium > 3% at onset: perp pump continues less (Korean retail already in)", "kimchi premium rising during pump: continue", "Upbit 5-min volume share of coin > 30%: fade",
          "pump starts within 10 min of KST 09:00: continue", "Upbit warning flag active: short after peak", "Bithumb-led (Bithumb before Upbit): larger pumps",
          "Korean listed & thin book: precursor long", "Korean unlisted pumps retrace faster", "KRW/USDT rate move same hour", "Upbit orderbook imbalance at onset (needs recorder)",
          "Korean night (KST 01-06) pumps are bot-driven: dump deeper", "Korean open gap (KST 09:00 vs 08:59) predicts 1h perp"]:
    add("F16_korea", "all", s, ["kimchi", "upbit_ws", "bithumb_ws", "k1m"], "1m", "both", "see statement", "1h/24h")
# F17 book microstructure at 1s
for s in ["ask depletion rate (bookTicker ask size drops) 30s before onset", "quote-stuffing count (updates/sec > 50) precedes pumps", "spread/depth ratio spike",
          "bid wall appearance (bookDepth bid +1% x3) predicts continuation", "ask wall removal predicts +5% in 5m", "microprice drift 60s > 0.3%",
          "trade-through rate (trades > best ask) 15s", "order-flow imbalance (Cont) 1s -> 5m return", "depth asymmetry mean-reverts within 10m (fade)", "iceberg detection: repeated same-size fills"]:
    add("F17_microstructure", "onset", s, ["bookTicker", "bookDepth", "aggTrades"], "1s", "both", "see statement", "5m-1h")
# F18 funding / liquidation mechanics
for s in ["pumps in the last 15 min before funding settlement (00/08/16 UTC) retrace at settlement: short", "liquidation cluster (est. from OI+leverage) within 5% above price: long into it",
          "short-liquidation volume > long-liquidation in first 5m: continuation", "funding flips positive during pump: exhaustion", "OI falls while price rises (short covering, not new longs): fade",
          "cross-exchange OI divergence (Coinalyze): Binance-only pump fades", "liquidation cascade depth predicts bounce (long at cascade end)", "pump with OI up > 10%: deeper dump",
          "predicted funding > 0.3%/8h at peak: short pays carry", "liq snapshot: forced-buy volume share > 30% = squeeze, not demand"]:
    add("F18_funding_liq", "all", s, ["metrics5m", "liquidationSnapshot", "coinalyze", "k1m"], "5m", "both", "see statement", "1h/8h")
# F19 news / social (needs data)
for s in ["CryptoPanic news within 30 min before onset: pump continues more", "no news pump = manipulation: dump deeper", "Santiment social volume z > 3 before onset",
          "Binance announcement 'will list' vs 'delist' word class", "Twitter/X mention burst (if data obtained)", "Telegram call size (channel members) vs pump size",
          "news pumps peak later (>2h)", "social-driven pumps have crowd onsets (link to B15_3)", "news pump + thin book = largest", "news sentiment sign predicts direction",
          "Korean community (DCinside/Naver) mention burst (if scraped)", "Reddit mention burst"]:
    add("F19_news_social", "pre-onset", s, ["cryptopanic", "santiment", "telegram", "k1m"], "1m", "both", "see statement", "1h-24h")
# F20 on-chain
for s in ["exchange inflow spike 1-6h BEFORE onset = distribution: dump deeper", "whale transfer to Binance in the pump hour: short after peak", "stablecoin inflow to Binance 24h: more pumps",
          "token-holder concentration (top10) > 60%: whale onsets", "low inflow + thin book composite precursor", "outflow after pump = holders exiting: no rebound",
          "bridge inflow (L2) precedes pumps of that chain's tokens", "new-token age < 90d and thin book: largest pumps"]:
    add("F20_onchain", "pre-onset", s, ["bigquery", "k1m", "bookDepth"], "1h", "both", "see statement", "6h-24h")

FAMILY_RULES = {
    "fdr": "BH q=0.10 over families (max-stat bootstrap) then BH q=0.10 within family",
    "control": "random-entry control matched on hour-of-day and liquidity; hypothesis must beat control with CI > 0",
    "pass_trade": "validation mean net > 0 with day-clustered CI > 0, beats control, MAE p90 < 15% (longs) / < 20% (shorts), no size/regime slice with CI < 0, THEN forward paper test n >= 100 events",
    "pass_model": "validation AUC CI > 0.55 and the implied trade passes pass_trade",
}
out = {"registered": "2026-09-28", "protocol": __doc__.strip().splitlines()[2:], "family_rules": FAMILY_RULES, "data_sources": DATA,
       "counts": {"total": len(H), "runnable_now": sum(h["runnable_now"] for h in H),
                  "by_family": {f: sum(h["family"] == f for h in H) for f in sorted({h["family"] for h in H})}},
       "hypotheses": H}
yaml.safe_dump(out, open(ROOT / "research/batch_B17_registry.yaml", "w"), sort_keys=False, width=160, allow_unicode=True)
print(out["counts"])
