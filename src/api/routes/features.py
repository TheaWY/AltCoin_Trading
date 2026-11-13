"""BTC feature panel (btc_features table, scripts/collect_btc_features.py): latest value, coverage, 7d sparkline per metric."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from src.data.storage import get_storage

router = APIRouter(prefix="/research", tags=["research"])

# (source, metric) -> Korean label, unit hint. Everything else gets a default label.
LABELS: dict[tuple[str, str], tuple[str, str]] = {
    ("coinmetrics", "AdrActCnt"): ("활성 주소 수", "n"), ("coinmetrics", "CapMVRVCur"): ("MVRV", "x"),
    ("coinmetrics", "FlowInExUSD"): ("거래소 유입", "usd"), ("coinmetrics", "FlowOutExUSD"): ("거래소 유출", "usd"),
    ("coinmetrics", "SplyExUSD"): ("거래소 보유량", "usd"), ("coinmetrics", "HashRate"): ("해시레이트", "n"),
    ("coinmetrics", "TxCnt"): ("트랜잭션 수", "n"), ("coinmetrics", "FeeTotNtv"): ("총 수수료 (BTC)", "n"),
    ("coinmetrics", "CapMrktCurUSD"): ("시가총액", "usd"), ("coinmetrics", "volume_reported_spot_usd_1d"): ("현물 거래대금", "usd"),
    ("coinmetrics", "ReferenceRateUSD_1h"): ("CM 기준가", "usd"), ("coinmetrics", "IssTotUSD"): ("일 발행액", "usd"),
    ("mempool", "fee_fastestFee"): ("수수료 fastest (sat/vB)", "n"), ("mempool", "mempool_count"): ("멤풀 tx 수", "n"),
    ("mempool", "mempool_vsize"): ("멤풀 vsize", "n"), ("mempool", "hashrate"): ("해시레이트(mempool)", "n"), ("mempool", "difficulty"): ("난이도", "n"),
    ("deribit", "dvol"): ("DVOL 내재변동성", "pct"), ("deribit", "realized_vol"): ("실현변동성", "pct"),
    ("deribit", "put_call_oi"): ("풋/콜 OI", "x"), ("deribit", "atm_iv_7d"): ("ATM IV 7d", "pct"), ("deribit", "atm_iv_30d"): ("ATM IV 30d", "pct"),
    ("deribit", "atm_iv_90d"): ("ATM IV 90d", "pct"), ("deribit", "atm_iv_180d"): ("ATM IV 180d", "pct"),
    ("deribit", "skew_7d"): ("25d 스큐 7d (풋−콜)", "pct"), ("deribit", "skew_30d"): ("25d 스큐 30d", "pct"), ("deribit", "skew_90d"): ("25d 스큐 90d", "pct"),
    ("cftc", "lev_money_net"): ("CME 레버리지펀드 순포지션", "n"), ("cftc", "asset_mgr_net"): ("CME 자산운용사 순포지션", "n"),
    ("cftc", "dealer_net"): ("CME 딜러 순포지션", "n"), ("cftc", "open_interest"): ("CME OI", "n"), ("cftc", "nonrept_net"): ("CME 소액 순포지션", "n"),
    ("defillama", "stable_total_usd"): ("스테이블코인 총공급", "usd"), ("defillama", "usdt_usd"): ("USDT 공급", "usd"),
    ("blockchain", "n_transactions"): ("일 트랜잭션", "n"), ("blockchain", "miners_revenue"): ("채굴자 수익", "usd"),
    ("blockchain", "n_unique_addresses"): ("고유 주소", "n"), ("blockchain", "utxo_count"): ("UTXO 수", "n"),
    ("okx", "ls_account_ratio"): ("OKX 롱/숏 계정비", "x"), ("okx", "taker_buy_ratio"): ("OKX 테이커 매수비중", "ratio"), ("okx", "oi_usd"): ("OKX OI", "usd"),
    ("bybit", "oi_btc"): ("Bybit OI (BTC)", "n"),
    ("binance", "premium_index"): ("선물 프리미엄(베이시스)", "ratio"), ("binance", "spot_close"): ("Binance 현물", "usd"),
    ("premium", "coinbase_premium_pct"): ("코인베이스 프리미엄", "pct"), ("premium", "kimchi_premium_pct"): ("김치 프리미엄", "pct"),
    ("premium", "coinbase_close"): ("Coinbase BTC-USD", "usd"), ("premium", "upbit_btc_krw"): ("Upbit BTC", "krw"), ("premium", "upbit_usdt_krw"): ("Upbit USDT", "krw"),
    ("fng", "fear_greed"): ("공포탐욕지수", "n"),
}
SOURCE_KO = {"coinmetrics": "온체인 (Coin Metrics)", "mempool": "네트워크 (mempool.space)", "deribit": "옵션 (Deribit)", "cftc": "CME 포지션 (CFTC COT)",
             "defillama": "스테이블코인 (DefiLlama)", "blockchain": "온체인 (blockchain.com)", "okx": "파생 (OKX)", "bybit": "파생 (Bybit)",
             "binance": "파생/현물 (Binance)", "premium": "프리미엄 (Coinbase/Upbit)", "fng": "심리 (Fear & Greed)", "polymarket": "예측시장 (Polymarket)"}
HIDE = {("deribit", "dte_7d"), ("deribit", "dte_30d"), ("deribit", "dte_90d"), ("deribit", "dte_180d"), ("okx", "taker_buy"), ("okx", "taker_sell"),
        ("mempool", "fee_halfHourFee"), ("mempool", "fee_hourFee"), ("mempool", "fee_economyFee"), ("mempool", "fee_minimumFee"), ("mempool", "mempool_total_fee")}


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _premiums(since: int) -> list[dict[str, Any]]:
    """derived 1m series: coinbase premium vs Binance spot, kimchi premium via Upbit KRW-USDT."""
    rows = _rows("SELECT source, metric, ts, value FROM btc_features WHERE ts >= ? AND ((source = 'binance' AND metric = 'spot_close') "
                 "OR (source = 'premium' AND metric IN ('coinbase_close','upbit_btc_krw','upbit_usdt_krw')))", (since,))
    s: dict[str, dict[int, float]] = {}
    for r in rows:
        s.setdefault(r["metric"], {})[int(r["ts"])] = float(r["value"])
    out = []
    for t, b in sorted(s.get("spot_close", {}).items()):
        cb, ub, uu = s.get("coinbase_close", {}).get(t), s.get("upbit_btc_krw", {}).get(t), s.get("upbit_usdt_krw", {}).get(t)
        if cb:
            out.append({"source": "premium", "metric": "coinbase_premium_pct", "ts": t, "value": (cb / b - 1) * 100})
        if ub and uu:
            out.append({"source": "premium", "metric": "kimchi_premium_pct", "ts": t, "value": (ub / (b * uu) - 1) * 100})
    return out


@router.get("/btc_features")
def btc_features(days: int = 7) -> dict[str, Any]:
    now = int(time.time()); since = now - days * 86400
    try:
        meta = _rows("SELECT source, metric, MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(*) AS n FROM btc_features GROUP BY source, metric")
        recent = _rows("SELECT source, metric, ts, value FROM btc_features WHERE ts >= ? ORDER BY ts", (since,)) + _premiums(since)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "groups": [], "ts": now}
    series: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for r in recent:
        series.setdefault((r["source"], r["metric"]), []).append((int(r["ts"]), float(r["value"])))
    for m in ("coinbase_premium_pct", "kimchi_premium_pct"):
        if ("premium", m) in series:
            s = series[("premium", m)]
            meta.append({"source": "premium", "metric": m, "first_ts": s[0][0], "last_ts": s[-1][0], "n": len(s)})
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in meta:
        key = (m["source"], m["metric"])
        if key in HIDE or m["metric"].endswith("_1h") and m["metric"] != "ReferenceRateUSD_1h":
            continue
        ser = series.get(key, [])
        step = max(1, len(ser) // 120)                                  # sparkline downsample
        spark = [v for _, v in ser[::step]] if ser else []
        label, unit = LABELS.get(key, (m["metric"], ""))
        first = ser[0][1] if ser else None; last = ser[-1][1] if ser else None
        groups.setdefault(m["source"], []).append({
            "metric": m["metric"], "label": label, "unit": unit, "last": last, "last_ts": int(m["last_ts"]),
            "first_ts": int(m["first_ts"]), "n": int(m["n"]), "chg": ((last / first - 1) if first and last and first != 0 else None),
            "spark": spark, "stale_h": (now - int(m["last_ts"])) / 3600})
    order = ["binance", "premium", "deribit", "okx", "bybit", "coinmetrics", "mempool", "blockchain", "cftc", "defillama", "fng", "polymarket"]
    out = [{"source": s, "label": SOURCE_KO.get(s, s), "metrics": sorted(groups[s], key=lambda x: x["label"])} for s in order if s in groups]
    out += [{"source": s, "label": SOURCE_KO.get(s, s), "metrics": groups[s]} for s in groups if s not in order]
    total = sum(int(m["n"]) for m in meta)
    return {"groups": out, "days": days, "rows_total": total, "metrics_total": sum(len(g["metrics"]) for g in out), "ts": now}
