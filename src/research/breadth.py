"""시장 폭(breadth) — 조정 전 신호로 가장 고전적인 축.

**왜 중요한가.** 지수는 시가총액 가중이라 대형주 몇 개가 끌어올리면 신고가가
난다. 그동안 나머지 종목이 무너지고 있어도 지수에는 안 보인다. 1999년과
2007년 모두 지수 고점보다 **수개월 앞서** 폭이 먼저 꺾였다.

**왜 지수로 재는가.** 정확히 하려면 942개 전 종목이 필요하지만 20년치면
5백만 행이라 저장 비용이 크다(CLAUDE.md §2). 업종 44개 + 규모 3개로
'몇 %의 업종이 추세 위인가'를 재면 25만 행이면 된다. 종목 단위보다 둔하지만
**방향은 같고**, 무엇보다 매일 갱신할 수 있다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SIZE_TICKERS = {"large": "KRX.1002", "mid": "KRX.1003", "small": "KRX.1004"}


def sector_matrix(prices: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """업종 종가 행렬 (date × ticker)."""
    sub = prices[prices["ticker"].isin(tickers)]
    if sub.empty:
        return pd.DataFrame()
    return sub.pivot(index="date", columns="ticker", values="close").sort_index()


def pct_above_ma(mat: pd.DataFrame, window: int = 200,
                 min_series: int = 10) -> pd.Series:
    """이동평균 위에 있는 업종 비율 [0,1].

    breadth 의 표준 형태다. 지수가 오르는데 이 값이 떨어지면 **소수 업종만
    끌고 가는 중**이라는 뜻이고, 그게 고전적인 경고 신호다.
    """
    if mat.empty:
        return pd.Series(dtype="float64")
    ma = mat.rolling(window, min_periods=window).mean()
    above = (mat > ma)
    valid = ma.notna().sum(axis=1)
    out = above.where(ma.notna()).sum(axis=1) / valid.replace(0, np.nan)
    return out.where(valid >= min_series).rename(f"pct_above_ma{window}")


def advance_ratio(mat: pd.DataFrame, window: int = 20,
                  min_series: int = 10) -> pd.Series:
    """최근 window 일 동안 오른 업종 비율 [0,1]."""
    if mat.empty:
        return pd.Series(dtype="float64")
    chg = mat / mat.shift(window) - 1.0
    valid = chg.notna().sum(axis=1)
    out = (chg > 0).where(chg.notna()).sum(axis=1) / valid.replace(0, np.nan)
    return out.where(valid >= min_series).rename(f"advance_ratio_{window}")


def dispersion(mat: pd.DataFrame, window: int = 20,
               min_series: int = 10) -> pd.Series:
    """업종 간 수익률 표준편차 — 분산이 커지면 시장이 갈라지고 있다는 뜻."""
    if mat.empty:
        return pd.Series(dtype="float64")
    chg = mat / mat.shift(window) - 1.0
    out = chg.std(axis=1)
    return out.where(chg.notna().sum(axis=1) >= min_series).rename(
        f"dispersion_{window}")


def small_vs_large(prices: pd.DataFrame, window: int = 60) -> pd.Series:
    """소형주 대비 대형주 상대강도 (소형 - 대형, window 일 수익률 차).

    폭이 좁아지면 대형주만 오르므로 이 값이 음수로 벌어진다.
    업종 폭과 다른 각도에서 같은 현상을 본다.
    """
    def close(tk: str) -> pd.Series:
        s = prices[prices["ticker"] == tk].set_index("date")["close"]
        return s.sort_index().astype("float64")

    small, large = close(SIZE_TICKERS["small"]), close(SIZE_TICKERS["large"])
    if small.empty or large.empty:
        return pd.Series(dtype="float64")
    rs = (small / small.shift(window)) - (large / large.shift(window))
    return rs.dropna().rename(f"small_vs_large_{window}")


def divergence(index_close: pd.Series, breadth: pd.Series,
               window: int = 252) -> pd.Series:
    """지수는 고점 근처인데 폭은 바닥인 정도. [-1, 1]

    (지수의 롤링 백분위) − (폭의 롤링 백분위).
    +1 에 가까우면 **지수는 최고인데 폭은 최저** — 소수 종목만 끌고 가는 상태다.

    ★ 처음엔 '수익률 변화의 순위 차'로 짰는데 틀렸다. 선형 상승 구간에서는
      분모가 커져 수익률이 오히려 감소하므로 부호가 뒤집힌다. 레벨의 백분위를
      직접 비교하는 편이 정의에도 맞고 해석도 분명하다.
    """
    from src.indicators.technical import pct_rank

    px = index_close.dropna()
    # astype 없이 ffill 하면 bool/object 로 남아 pandas 가 downcast 경고를 낸다
    bd = breadth.dropna().astype("float64").reindex(px.index).ffill()
    px_pr = pct_rank(px.rename("px"), window)
    bd_pr = pct_rank(bd.rename("bd"), window)
    return (px_pr - bd_pr).dropna().rename(f"divergence_{window}")


# ------------------------------------------------------------------ 분산 매도일
def distribution_days(close: pd.Series, volume: pd.Series, *,
                      window: int = 25, drop: float = -0.002) -> pd.Series:
    """William O'Neil 의 '분산 매도일' 카운트.

    정의: 지수가 하락(-0.2% 이상)했는데 거래량이 전일보다 **늘어난** 날.
    기관이 조용히 물량을 내보내는 흔적으로 본다. 25일 안에 4~5회가 쌓이면
    경고로 읽는 것이 원 규칙이다.

    거래량이 0 인 날(업종지수 등)은 셈에서 빠진다.
    """
    c = close.dropna().astype("float64").sort_index()
    v = volume.reindex(c.index).astype("float64")
    ret = c.pct_change()
    vol_up = v > v.shift(1)
    is_dist = (ret <= drop) & vol_up & (v > 0)
    return is_dist.rolling(window).sum().rename(f"distribution_days_{window}")
