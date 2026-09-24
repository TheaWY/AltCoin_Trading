"""Indicator library: everything the system looks at when it judges a coin.

One registry, used three ways:
  * research: pump/dump precursor studies stream through every indicator
    (one frame at a time, so 14 days x 500+ coins x 100+ indicators fits in
    memory) and report which ones actually move before big moves
  * live: snapshot() computes the latest value of every indicator for every
    coin and stores it in coin_snapshot (dashboard, rider, future rules)
  * catalog(): name, category and a Korean description for each

Inputs (all minute-aligned, bars <= t only, nothing from the future):
  1m bars (prices_1m), perp_1m (mark/index basis, funding), perp_5m (open
  interest, circulating supply, long/short ratios, taker ratio; shifted one
  period so a 5m bucket is only used after it closes), book_5m (spread,
  depth, imbalance), liquidations (long/short USD per minute), and the daily
  panel (previous completed day only) for multi-day context.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

BTC = "BTC/USDT"
DAY = 86400


@dataclass(frozen=True)
class Ind:
    name: str
    cat: str
    ko: str
    fn: Callable[["Ctx"], pd.DataFrame]
    needs: str = "bars"  # bars | perp1 | perp5 | book | liq | daily


class Ctx:
    """Lazy cache of intermediate series shared by many indicators."""

    def __init__(self, p: dict[str, pd.DataFrame], aux: dict[str, pd.DataFrame] | None = None,
                 daily: dict[str, pd.DataFrame] | None = None):
        self.p = p
        self.aux = aux or {}
        self.daily = daily
        self._c: dict[str, Any] = {}

    def get(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    @property
    def c(self) -> pd.DataFrame:
        return self.p["close"]

    @property
    def lr(self) -> pd.DataFrame:
        return self.get("lr", lambda: np.log(self.c / self.c.shift(1)))

    @property
    def qv(self) -> pd.DataFrame:
        return self.p["quote_volume"]

    @property
    def sigma(self) -> pd.DataFrame:  # per-minute vol, known before the current bar
        return self.get("sigma", lambda: self.lr.rolling(240, min_periods=60).std().shift(1))

    @property
    def btc_lr(self) -> pd.Series:
        return self.get("btc_lr", lambda: self.lr[BTC] if BTC in self.lr else self.lr.median(axis=1))

    def ret(self, w: int) -> pd.DataFrame:
        return self.get(f"ret{w}", lambda: np.log(self.c / self.c.shift(w)))

    def qv_sum(self, w: int) -> pd.DataFrame:
        return self.get(f"qv{w}", lambda: self.qv.rolling(w, min_periods=max(1, w // 2)).sum())

    def base_qv(self) -> pd.DataFrame:  # 4h per-minute average, excluding the recent hour
        return self.get("base_qv", lambda: self.qv.rolling(240, min_periods=120).mean().shift(60))

    def a(self, key: str) -> pd.DataFrame:
        """Aux frame aligned to the bar grid (NaN frame when missing)."""
        f = self.aux.get(key)
        if f is None:
            return pd.DataFrame(np.nan, index=self.c.index, columns=self.c.columns, dtype="float32")
        return f.reindex(index=self.c.index, columns=self.c.columns)

    def d(self, key: str) -> pd.DataFrame:
        """Daily frame from the PREVIOUS completed UTC day, spread over minutes."""
        def build():
            if not self.daily:
                return None
            return self.daily[key]
        f = self.get(f"daily_{key}", build)
        if f is None:
            return pd.DataFrame(np.nan, index=self.c.index, columns=self.c.columns)
        # a minute on day D sees the row of day D-1, the last day that is complete
        days = (np.asarray(self.c.index) // DAY) * DAY - DAY
        return f.reindex(index=days).reindex(columns=self.c.columns).set_axis(self.c.index, axis=0)


def _clean(x: pd.DataFrame) -> pd.DataFrame:
    return x.replace([np.inf, -np.inf], np.nan).astype("float32")


def _rsi(c: pd.DataFrame, n: int) -> pd.DataFrame:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _run_length(up: pd.DataFrame) -> pd.DataFrame:
    a = up.to_numpy()
    out = np.zeros_like(a, dtype="float32")
    for i in range(1, len(a)):
        out[i] = np.where(a[i] > 0, out[i - 1] + 1, 0)
    return pd.DataFrame(out, index=up.index, columns=up.columns)


# ------------------------------------------------------------------ builders

def _z(x: Ctx, w: int) -> pd.DataFrame:
    return x.ret(w) / (x.sigma * np.sqrt(w))


def _rel_btc(x: Ctx, w: int) -> pd.DataFrame:
    b = x.btc_lr.rolling(w, min_periods=max(1, w // 2)).sum()
    return x.ret(w).sub(b, axis=0)


def _beta(x: Ctx, w: int = 240) -> pd.DataFrame:
    b = x.btc_lr
    cov = x.lr.mul(b, axis=0).rolling(w, min_periods=w // 2).mean() - \
        x.lr.rolling(w, min_periods=w // 2).mean().mul(b.rolling(w, min_periods=w // 2).mean(), axis=0)
    return cov.div(b.rolling(w, min_periods=w // 2).var(), axis=0)


def _corr(x: Ctx, w: int = 240) -> pd.DataFrame:
    return x.lr.rolling(w, min_periods=w // 2).corr(x.btc_lr)


def _ema_dist(x: Ctx, n: int) -> pd.DataFrame:
    return np.log(x.c / x.c.ewm(span=n, adjust=False, min_periods=n).mean())


def _range_pos(x: Ctx, w: int) -> pd.DataFrame:
    hi = x.p["high"].rolling(w, min_periods=w // 2).max()
    lo = x.p["low"].rolling(w, min_periods=w // 2).min()
    return (x.c - lo) / (hi - lo)


def _rv(x: Ctx, w: int) -> pd.DataFrame:
    return x.lr.rolling(w, min_periods=w // 2).std()


def _vol_ratio(x: Ctx, w: int) -> pd.DataFrame:
    return x.qv_sum(w) / (x.base_qv() * w)


def _taker(x: Ctx, w: int) -> pd.DataFrame:
    return x.p["taker_buy_quote"].rolling(w, min_periods=max(1, w // 2)).sum() / x.qv_sum(w)


def _cvd(x: Ctx, w: int) -> pd.DataFrame:
    tb = x.p["taker_buy_quote"]
    return (2 * tb - x.qv).rolling(w, min_periods=max(1, w // 2)).sum() / x.qv_sum(w)


def _vwap_dist(x: Ctx, w: int) -> pd.DataFrame:
    typ = (x.p["high"] + x.p["low"] + x.c) / 3
    vwap = (typ * x.qv).rolling(w, min_periods=w // 2).sum() / x.qv_sum(w)
    return np.log(x.c / vwap)


def _candle(x: Ctx, kind: str, w: int = 15) -> pd.DataFrame:
    o, h, lo, c = x.p["open"], x.p["high"], x.p["low"], x.c
    rng = (h - lo).replace(0, np.nan)
    if kind == "body":
        v = (c - o).abs() / rng
    elif kind == "upper":
        v = (h - np.maximum(o, c)) / rng
    else:
        v = (np.minimum(o, c) - lo) / rng
    return v.rolling(w, min_periods=w // 2).mean()


def _oi_chg(x: Ctx, w: int) -> pd.DataFrame:
    oi = x.a("oi_usd")
    return np.log(oi / oi.shift(w))


def _mcap(x: Ctx) -> pd.DataFrame:
    return x.a("supply") * x.c


def _liq(x: Ctx, side: str, w: int) -> pd.DataFrame:
    return x.a(f"liq_{side}").fillna(0).rolling(w, min_periods=1).sum()


def _market(x: Ctx, kind: str) -> pd.DataFrame:
    r60 = x.ret(60)
    if kind == "breadth":
        s = (r60 > 0).where(r60.notna()).mean(axis=1)
    elif kind == "median":
        s = r60.median(axis=1)
    elif kind == "dispersion":
        s = r60.std(axis=1)
    elif kind == "btc60":
        s = x.btc_lr.rolling(60, min_periods=30).sum()
    else:
        s = x.btc_lr.rolling(1440, min_periods=600).sum()
    return pd.DataFrame(np.repeat(s.to_numpy()[:, None], x.c.shape[1], axis=1), index=x.c.index, columns=x.c.columns)


def _time(x: Ctx, kind: str) -> pd.DataFrame:
    t = np.asarray(x.c.index)
    v = (t % DAY) // 3600 if kind == "hour" else ((t // DAY) + 3) % 7  # 1970-01-01 was a Thursday -> Mon=0
    return pd.DataFrame(np.repeat(v[:, None].astype("float32"), x.c.shape[1], axis=1), index=x.c.index,
                        columns=x.c.columns)


# ------------------------------------------------------------------ registry

def _registry() -> list[Ind]:
    R: list[Ind] = []
    add = lambda name, cat, ko, fn, needs="bars": R.append(Ind(name, cat, ko, fn, needs))  # noqa: E731

    for w, lab in ((1, "1분"), (3, "3분"), (5, "5분"), (15, "15분"), (30, "30분"), (60, "1시간"),
                   (240, "4시간"), (720, "12시간"), (1440, "24시간")):
        add(f"ret_{w}", "모멘텀", f"{lab} 수익률", lambda x, w=w: x.ret(w))
    for w in (5, 15, 60):
        add(f"z_{w}", "모멘텀", f"{w}분 수익률 ÷ 평소 변동성 (얼마나 이례적인 움직임인지)", lambda x, w=w: _z(x, w))
    for w in (15, 60, 240, 1440):
        add(f"rel_btc_{w}", "상대강도", f"{w}분 BTC 대비 초과수익", lambda x, w=w: _rel_btc(x, w))
    add("xs_rank_60", "상대강도", "1시간 수익률 전체 코인 중 순위 (1=최고)",
        lambda x: x.ret(60).rank(axis=1, pct=True))
    add("xs_rank_1440", "상대강도", "24시간 수익률 전체 코인 중 순위", lambda x: x.ret(1440).rank(axis=1, pct=True))
    add("beta_btc", "상대강도", "BTC 베타 (4시간)", _beta)
    add("corr_btc", "상대강도", "BTC 상관계수 (4시간)", _corr)

    for n in (20, 60, 240):
        add(f"ema_dist_{n}", "추세", f"EMA{n}(1분) 대비 이격", lambda x, n=n: _ema_dist(x, n))
    add("ema_spread_20_60", "추세", "EMA20-EMA60 간격 (단기 추세 방향)",
        lambda x: np.log(x.c.ewm(span=20, adjust=False).mean() / x.c.ewm(span=60, adjust=False).mean()))
    add("macd_hist", "추세", "MACD 히스토그램 (60/130/45분, 가격 대비)",
        lambda x: (lambda m: (m - m.ewm(span=45, adjust=False).mean()) / x.c)(
            x.c.ewm(span=60, adjust=False).mean() - x.c.ewm(span=130, adjust=False).mean()))
    for w in (60, 240, 1440):
        add(f"range_pos_{w}", "가격위치", f"{w}분 고저 범위 안 위치 (0=저점, 1=고점)", lambda x, w=w: _range_pos(x, w))
    add("dist_high_240", "가격위치", "4시간 고점 대비", lambda x: np.log(x.c / x.p["high"].rolling(240, min_periods=120).max()))
    add("dist_high_1440", "가격위치", "24시간 고점 대비", lambda x: np.log(x.c / x.p["high"].rolling(1440, min_periods=600).max()))
    add("dist_low_1440", "가격위치", "24시간 저점 대비", lambda x: np.log(x.c / x.p["low"].rolling(1440, min_periods=600).min()))

    add("rsi_14", "오실레이터", "RSI 14 (1분봉)", lambda x: _rsi(x.c, 14))
    add("rsi_60", "오실레이터", "RSI 60 (1분봉, 약 1시간)", lambda x: _rsi(x.c, 60))
    add("stoch_60", "오실레이터", "스토캐스틱 %K 60분", lambda x: _range_pos(x, 60) * 100)
    add("cci_60", "오실레이터", "CCI 60분", lambda x: (lambda t: (t - t.rolling(60, min_periods=30).mean()) /
                                                       (0.015 * t.rolling(60, min_periods=30).std()))(
        (x.p["high"] + x.p["low"] + x.c) / 3))
    add("mfi_60", "오실레이터", "MFI 60분 (거래대금 가중 RSI)", lambda x: (lambda t: (lambda pos, neg: 100 - 100 / (
        1 + pos.rolling(60, min_periods=30).sum() / neg.rolling(60, min_periods=30).sum().replace(0, np.nan)))(
        (t.diff() > 0) * x.qv, (t.diff() < 0) * x.qv))((x.p["high"] + x.p["low"] + x.c) / 3))

    for w in (15, 60, 240, 1440):
        add(f"rv_{w}", "변동성", f"{w}분 실현변동성 (1분 수익률 표준편차)", lambda x, w=w: _rv(x, w))
    add("vol_compress", "변동성", "1시간 변동성 ÷ 24시간 변동성 (낮으면 수축)", lambda x: _rv(x, 60) / _rv(x, 1440))
    add("parkinson_60", "변동성", "파킨슨 변동성 60분 (고저 기반)",
        lambda x: np.sqrt((np.log(x.p["high"] / x.p["low"]) ** 2).rolling(60, min_periods=30).mean() / (4 * np.log(2))))
    add("atr_60", "변동성", "ATR 60분 (가격 대비)", lambda x: ((x.p["high"] - x.p["low"]) / x.c).rolling(60, min_periods=30).mean())
    add("bb_width_240", "변동성", "볼린저 밴드 폭 (4시간)",
        lambda x: 4 * x.c.rolling(240, min_periods=120).std() / x.c.rolling(240, min_periods=120).mean())
    add("bb_pctb_240", "변동성", "볼린저 %B (4시간, 1 이상이면 상단 돌파)",
        lambda x: (lambda m, s: (x.c - (m - 2 * s)) / (4 * s))(x.c.rolling(240, min_periods=120).mean(),
                                                               x.c.rolling(240, min_periods=120).std()))
    add("range_expansion_15", "변동성", "최근 15분 고저폭 ÷ 4시간 평균 15분 고저폭",
        lambda x: (lambda r: r / r.rolling(240, min_periods=120).mean().shift(15))(
            np.log(x.p["high"].rolling(15).max() / x.p["low"].rolling(15).min())))

    add("body_15", "캔들", "15분 평균 몸통 비율 (몸통 ÷ 고저폭)", lambda x: _candle(x, "body"))
    add("upper_wick_15", "캔들", "15분 평균 윗꼬리 비율", lambda x: _candle(x, "upper"))
    add("lower_wick_15", "캔들", "15분 평균 아랫꼬리 비율", lambda x: _candle(x, "lower"))
    add("green_ratio_15", "캔들", "최근 15분 중 양봉 비율", lambda x: (x.lr > 0).astype("float32").rolling(15, min_periods=8).mean())
    add("green_run", "캔들", "연속 양봉 수", lambda x: _run_length((x.lr > 0).astype("float32")))
    add("red_run", "캔들", "연속 음봉 수", lambda x: _run_length((x.lr < 0).astype("float32")))
    return R


def _registry2() -> list[Ind]:
    R: list[Ind] = []
    add = lambda name, cat, ko, fn, needs="bars": R.append(Ind(name, cat, ko, fn, needs))  # noqa: E731

    add("dvol_60", "거래량", "1시간 거래대금 (USD, log)", lambda x: np.log1p(x.qv_sum(60)))
    add("dvol_1440", "거래량", "24시간 거래대금 (USD, log)", lambda x: np.log1p(x.qv_sum(1440)))
    for w in (1, 5, 15, 60):
        add(f"vol_ratio_{w}", "거래량", f"최근 {w}분 거래대금 ÷ 평소(4시간 평균) 같은 시간", lambda x, w=w: _vol_ratio(x, w))
    add("vol_z_60", "거래량", "1시간 거래대금의 24시간 분포 내 z점수",
        lambda x: (lambda s: (s - s.rolling(1440, min_periods=600).mean()) / s.rolling(1440, min_periods=600).std())(x.qv_sum(60)))
    add("vol_trend", "거래량", "1시간 거래대금 ÷ 직전 4시간 평균 시간당 거래대금",
        lambda x: x.qv_sum(60) / (x.qv_sum(240).shift(60) / 4))
    add("trades_ratio_15", "거래량", "15분 체결 건수 ÷ 평소",
        lambda x: x.p["trades"].rolling(15).sum() / (x.p["trades"].rolling(240, min_periods=120).mean().shift(15) * 15))
    add("trades_ratio_60", "거래량", "1시간 체결 건수 ÷ 평소",
        lambda x: x.p["trades"].rolling(60).sum() / (x.p["trades"].rolling(240, min_periods=120).mean().shift(60) * 60))
    add("avg_trade_ratio_15", "거래량", "15분 평균 체결 크기 ÷ 평소 (큰손 유입)",
        lambda x: (lambda a: a.rolling(15).mean() / a.rolling(240, min_periods=120).mean().shift(15))(
            x.qv / x.p["trades"].replace(0, np.nan)))
    add("amihud_60", "거래량", "비유동성: |수익률| ÷ 거래대금 (1시간, 높으면 적은 돈에 크게 움직임)",
        lambda x: (x.lr.abs() / x.qv.replace(0, np.nan)).rolling(60, min_periods=30).mean() * 1e6)
    add("obv_slope_60", "거래량", "OBV 1시간 변화 ÷ 1시간 거래대금",
        lambda x: (np.sign(x.lr) * x.qv).rolling(60, min_periods=30).sum() / x.qv_sum(60))

    for w in (5, 15, 60, 240):
        add(f"taker_{w}", "주문흐름", f"{w}분 시장가 매수 비중", lambda x, w=w: _taker(x, w))
    for w in (60, 240):
        add(f"cvd_{w}", "주문흐름", f"{w}분 누적 순매수(CVD) ÷ 거래대금", lambda x, w=w: _cvd(x, w))
    add("taker_shift", "주문흐름", "15분 매수비중 - 4시간 매수비중 (최근 매수 쏠림)", lambda x: _taker(x, 15) - _taker(x, 240))

    for w in (60, 240, 1440):
        add(f"vwap_dist_{w}", "가격위치", f"{w}분 VWAP 대비 이격", lambda x, w=w: _vwap_dist(x, w))

    add("basis_bps", "파생", "선물-현물 괴리 (bps, 마크 vs 인덱스)", lambda x: x.a("basis_bps"), "perp1")
    add("basis_chg_60", "파생", "괴리 1시간 변화", lambda x: x.a("basis_bps") - x.a("basis_bps").shift(60), "perp1")
    add("funding", "파생", "현재 펀딩비 (8시간)", lambda x: x.a("funding"), "perp1")
    add("funding_z", "파생", "펀딩비 z점수 (보유 기간 내)",
        lambda x: (lambda f: (f - f.expanding(min_periods=600).mean()) / f.expanding(min_periods=600).std())(x.a("funding")), "perp1")
    add("mins_to_funding", "파생", "다음 펀딩까지 남은 분",
        lambda x: (x.a("next_funding") - np.asarray(x.c.index)[:, None]) / 60, "perp1")
    add("oi_usd", "파생", "미결제약정 (USD, log)", lambda x: np.log1p(x.a("oi_usd")), "perp5")
    for w, lab in ((15, "15분"), (60, "1시간"), (240, "4시간"), (1440, "24시간")):
        add(f"oi_chg_{w}", "파생", f"미결제약정 {lab} 변화", lambda x, w=w: _oi_chg(x, w), "perp5")
    add("oi_to_mcap", "파생", "미결제약정 ÷ 시가총액 (레버리지 쏠림)", lambda x: x.a("oi_usd") / _mcap(x), "perp5")
    add("oi_to_dvol", "파생", "미결제약정 ÷ 24시간 거래대금", lambda x: x.a("oi_usd") / x.qv_sum(1440), "perp5")
    add("mcap", "파생", "시가총액 (유통량×가격, log)", lambda x: np.log1p(_mcap(x)), "perp5")
    add("oi_price_div_60", "파생", "1시간 미결제약정 변화 - 가격 변화 (가격 안 오르는데 OI 증가 = 숏 누적)",
        lambda x: _oi_chg(x, 60) - x.ret(60), "perp5")
    add("ls_global", "포지션", "전체 계정 롱/숏 비율", lambda x: x.a("ls_global"), "perp5")
    add("ls_global_chg_60", "포지션", "전체 롱/숏 비율 1시간 변화",
        lambda x: np.log(x.a("ls_global") / x.a("ls_global").shift(60)), "perp5")
    add("ls_top_pos", "포지션", "상위 트레이더 포지션 롱/숏 비율", lambda x: x.a("ls_top_pos"), "perp5")
    add("ls_top_acct", "포지션", "상위 트레이더 계정 롱/숏 비율", lambda x: x.a("ls_top_acct"), "perp5")
    add("smart_vs_crowd", "포지션", "상위 트레이더 롱숏 ÷ 전체 롱숏 (고수가 더 롱이면 >1)",
        lambda x: x.a("ls_top_pos") / x.a("ls_global"), "perp5")
    add("taker_ratio_fut", "포지션", "선물 시장가 매수/매도 비율 (5분)", lambda x: x.a("taker_ratio"), "perp5")

    add("spread_bps", "호가", "매수-매도 호가 스프레드 (bps)", lambda x: x.a("spread_bps"), "book")
    add("book_imb_05", "호가", "±0.5% 호가 잔량 불균형 (+면 매수벽)", lambda x: x.a("imb_05"), "book")
    add("book_imb_2", "호가", "±2% 호가 잔량 불균형", lambda x: x.a("imb_2"), "book")
    add("depth_05", "호가", "±0.5% 호가 잔량 합계 (USD, log)", lambda x: np.log1p(x.a("depth_05")), "book")
    add("depth_to_dvol", "호가", "±0.5% 잔량 ÷ 1시간 거래대금 (얇을수록 잘 튐)", lambda x: x.a("depth_05") / x.qv_sum(60), "book")
    add("microprice_bps", "호가", "마이크로프라이스 - 중간가 (bps, 다음 틱 방향)", lambda x: x.a("micro_bps"), "book")

    add("liq_long_60", "청산", "1시간 롱 청산액 ÷ 거래대금", lambda x: _liq(x, "long", 60) / x.qv_sum(60), "liq")
    add("liq_short_60", "청산", "1시간 숏 청산액 ÷ 거래대금", lambda x: _liq(x, "short", 60) / x.qv_sum(60), "liq")
    add("liq_net_60", "청산", "1시간 (숏-롱) 청산 ÷ 거래대금 (+면 숏 스퀴즈)",
        lambda x: (_liq(x, "short", 60) - _liq(x, "long", 60)) / x.qv_sum(60), "liq")
    add("liq_total_1440", "청산", "24시간 총 청산액 ÷ 거래대금",
        lambda x: (_liq(x, "short", 1440) + _liq(x, "long", 1440)) / x.qv_sum(1440), "liq")

    add("mkt_breadth_60", "시장", "1시간 동안 오른 코인 비율", lambda x: _market(x, "breadth"))
    add("mkt_median_60", "시장", "전체 코인 1시간 수익률 중앙값", lambda x: _market(x, "median"))
    add("mkt_dispersion_60", "시장", "코인 간 1시간 수익률 분산", lambda x: _market(x, "dispersion"))
    add("btc_ret_60", "시장", "BTC 1시간 수익률", lambda x: _market(x, "btc60"))
    add("btc_ret_1440", "시장", "BTC 24시간 수익률", lambda x: _market(x, "btc1440"))
    add("hour_utc", "시간", "UTC 시각 (0~23)", lambda x: _time(x, "hour"))
    add("weekday", "시간", "요일 (월=0)", lambda x: _time(x, "weekday"))
    return R


DAILY_TEXT = {
    "ret_3d": "3일 수익률", "ret_7d": "7일 수익률", "ret_30d": "30일 수익률",
    "dist_ma20d": "20일 이동평균 대비 이격", "dist_ma50d": "50일 이동평균 대비 이격",
    "dist_ma200d": "200일 이동평균 대비 이격", "rsi_14d": "일봉 RSI 14", "atr_14d": "일봉 ATR 14 (가격 대비)",
    "dist_high_30d": "30일 고점 대비", "dist_high_90d": "90일 고점 대비", "age_days": "상장 후 경과일 (데이터 기준)",
    "dvol_30d": "30일 평균 일 거래대금 (log)", "vol_30d": "30일 일간 변동성",
}


def daily_frames(d: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c, h, lo, qv = d["close"], d["high"], d["low"], d["qv"]
    lr = np.log(c / c.shift(1))
    prev = c.shift(1)
    tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()]).groupby(level=0).max()
    return {
        "ret_3d": np.log(c / c.shift(3)), "ret_7d": np.log(c / c.shift(7)), "ret_30d": np.log(c / c.shift(30)),
        "dist_ma20d": np.log(c / c.rolling(20, min_periods=15).mean()),
        "dist_ma50d": np.log(c / c.rolling(50, min_periods=40).mean()),
        "dist_ma200d": np.log(c / c.rolling(200, min_periods=150).mean()),
        "rsi_14d": _rsi(c, 14), "atr_14d": tr.rolling(14, min_periods=10).mean() / c,
        "dist_high_30d": np.log(c / h.rolling(30, min_periods=20).max()),
        "dist_high_90d": np.log(c / h.rolling(90, min_periods=60).max()),
        "age_days": c.notna().cumsum().where(c.notna()),
        "dvol_30d": np.log1p(qv.rolling(30, min_periods=20).mean()),
        "vol_30d": lr.rolling(30, min_periods=20).std(),
    }


def _registry3() -> list[Ind]:
    return [Ind(k, "일봉", v, (lambda x, k=k: x.d(k)), "daily") for k, v in DAILY_TEXT.items()]


REGISTRY: list[Ind] = _registry() + _registry2() + _registry3()


def catalog() -> list[dict[str, str]]:
    return [{"name": i.name, "category": i.cat, "ko": i.ko, "source": i.needs} for i in REGISTRY]


def iter_indicators(ctx: Ctx, only: set[str] | None = None) -> Iterator[tuple[Ind, pd.DataFrame]]:
    """Yield (indicator, frame) one at a time. Frames are float32 and cleaned;
    an indicator that fails (missing input) yields an all-NaN frame."""
    for ind in REGISTRY:
        if only is not None and ind.name not in only:
            continue
        try:
            f = _clean(ind.fn(ctx))
        except Exception:  # noqa: BLE001
            f = pd.DataFrame(np.nan, index=ctx.c.index, columns=ctx.c.columns, dtype="float32")
        yield ind, f


# ------------------------------------------------------------------ loading

def load_aux(storage: Any, since_ts: int, index: pd.Index, columns: pd.Index) -> dict[str, pd.DataFrame]:
    """perp_1m / perp_5m / book_5m / liquidations pivoted onto the minute grid.
    perp_5m and book_5m are forward-filled (at most 30 min); perp_5m rows are
    shifted +5 min so each bucket is used only once it is complete."""
    out: dict[str, pd.DataFrame] = {}

    def pivot(sql: str, cols: list[str], shift_s: int = 0, ffill: int = 30) -> None:
        try:
            with storage._connect() as c:  # noqa: SLF001
                rows = c.execute(sql, (since_ts,)).fetchall()
        except Exception:  # noqa: BLE001
            return
        if not rows:
            return
        df = pd.DataFrame([dict(r) for r in rows])
        df["ts"] = (df["ts"].astype("int64") + shift_s) // 60 * 60
        for col in cols:
            if col not in df:
                continue
            w = df.pivot_table(index="ts", columns="symbol", values=col, aggfunc="last")
            w = w.reindex(index=index.union(w.index)).ffill(limit=ffill).reindex(index=index, columns=columns)
            out[col] = w.astype("float32")

    pivot("SELECT symbol, ts, basis_bps, funding, next_funding FROM perp_1m WHERE ts >= ?",
          ["basis_bps", "funding", "next_funding"], ffill=10)
    pivot("SELECT symbol, ts, oi_usd, supply, ls_global, ls_top_pos, ls_top_acct, taker_ratio FROM perp_5m WHERE ts >= ?",
          ["oi_usd", "supply", "ls_global", "ls_top_pos", "ls_top_acct", "taker_ratio"], shift_s=300)
    pivot("SELECT symbol, ts, spread_bps, imb_05, imb_2, bid_05 + ask_05 AS depth_05, micro_bps FROM book_5m WHERE ts >= ?",
          ["spread_bps", "imb_05", "imb_2", "depth_05", "micro_bps"])
    try:
        with storage._connect() as c:  # noqa: SLF001
            rows = c.execute("SELECT symbol, (timestamp / 60) * 60 AS ts, side, SUM(notional) AS usd FROM liquidations "
                             "WHERE timestamp >= ? GROUP BY 1, 2, 3", (since_ts,)).fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            for side in ("long", "short"):
                w = df[df["side"] == side].pivot_table(index="ts", columns="symbol", values="usd", aggfunc="sum")
                out[f"liq_{side}"] = w.reindex(index=index, columns=columns).fillna(0).astype("float32")
    except Exception:  # noqa: BLE001
        pass
    return out


def build_ctx(storage: Any, minutes: int, now: int | None = None, symbols: list[str] | None = None,
              with_daily: bool = True) -> Ctx:
    from src.research import pump_study as ps

    now = int(now or time.time())
    since = now - minutes * 60
    p = ps.load_panel(storage, since, symbols)
    p = {k: v.astype("float32") for k, v in p.items()}
    aux = load_aux(storage, since - 3 * DAY if minutes < 3 * 1440 else since, p["close"].index, p["close"].columns)
    daily = None
    if with_daily:
        try:
            from src.research import swing_study as ss

            daily = daily_frames(ss.load_daily(storage))
        except Exception:  # noqa: BLE001
            daily = None
    return Ctx(p, aux, daily)


SNAPSHOT_SQL = """CREATE TABLE IF NOT EXISTS coin_snapshot (
    symbol TEXT PRIMARY KEY, ts BIGINT NOT NULL, data TEXT NOT NULL)"""


def snapshot(storage: Any, minutes: int = 1500) -> dict[str, Any]:
    """Latest value of every indicator for every coin -> coin_snapshot."""
    import json

    t0 = time.time()
    ctx = build_ctx(storage, minutes)
    c = ctx.c
    if c.empty:
        return {"coins": 0}
    # the newest row can be missing coins whose bar has not arrived yet; use
    # each coin's own last complete minute
    last_idx = c.notna().to_numpy()[::-1].argmax(axis=0)
    rows_at = len(c) - 1 - last_idx
    vals: dict[str, dict[str, float]] = {s: {} for s in c.columns}
    for ind, f in iter_indicators(ctx):
        a = f.to_numpy()
        for j, s in enumerate(c.columns):
            v = a[rows_at[j], j]
            if np.isfinite(v):
                vals[s][ind.name] = round(float(v), 6)
    ts = {s: int(c.index[rows_at[j]]) for j, s in enumerate(c.columns)}
    rows = [(s, ts[s], json.dumps(v)) for s, v in vals.items() if v]
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(SNAPSHOT_SQL)
        if getattr(storage, "is_postgres", False):
            with conn.raw.cursor() as cur:
                cur.executemany("INSERT INTO coin_snapshot (symbol, ts, data) VALUES (%s,%s,%s) "
                                "ON CONFLICT (symbol) DO UPDATE SET ts = EXCLUDED.ts, data = EXCLUDED.data", rows)
        else:
            conn.executemany("INSERT OR REPLACE INTO coin_snapshot (symbol, ts, data) VALUES (?,?,?)", rows)
    return {"coins": len(rows), "indicators": len(REGISTRY), "elapsed_s": round(time.time() - t0, 1)}
