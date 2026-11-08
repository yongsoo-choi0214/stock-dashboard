"""OHLCV 기반 오실레이터 — Slow Stochastic, CCI, DMI/ADX, OBV.

technical.py(§5.6, 검증 완료 코드)는 건드리지 않고 별도 모듈로 둔다.
모든 함수는 순수함수이며 입력 index 를 보존한다. 평활은 국내 HTS(키움 등) 관행:
- DMI/ADX: Wilder (alpha = 1/n)  — RSI 와 같은 이유로 span=n 이 아니다(§7.1)
- Slow Stochastic: Fast %K 를 SMA(k) → Slow %K, 다시 SMA(d) → Slow %D
- CCI: typical price 의 SMA 대비 편차 / (0.015 × 평균절대편차)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def stoch_slow(high: pd.Series, low: pd.Series, close: pd.Series,
               n: int = 5, k: int = 3, d: int = 3) -> pd.DataFrame:
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    fast_k = 100.0 * (close - ll) / (hh - ll).replace(0.0, np.nan)
    slow_k = fast_k.rolling(k).mean()
    return pd.DataFrame({"k": slow_k, "d": slow_k.rolling(d).mean()})


def cci(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = tp.rolling(n).mean()
    mad = tp.rolling(n).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    return ((tp - ma) / (0.015 * mad.replace(0.0, np.nan))).rename(f"cci_{n}")


def dmi(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 14) -> pd.DataFrame:
    """PDI / MDI / ADX (Wilder)."""
    up = high.diff()
    dn = -low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, n)
    pdi = 100.0 * _wilder(pdm, n) / atr
    mdi = 100.0 * _wilder(mdm, n) / atr
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    out = pd.DataFrame({"pdi": pdi, "mdi": mdi, "adx": _wilder(dx, n)})
    out.iloc[:n] = np.nan                       # 워밍업
    return out


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff()).fillna(0.0) * volume).cumsum().rename("obv")


def cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift() <= b.shift())


def cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift() >= b.shift())
