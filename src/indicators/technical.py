"""기술적 지표. 모든 함수는 순수함수이며 입력 Series의 index를 보존한다.

CLAUDE.md §5.6 의 검증 완료 코드. 리팩터링 금지 — 변경 시 pytest 필수.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    """지수이동평균. alpha = 2/(span+1), 재귀식 정의(adjust=False)."""
    return s.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI. alpha = 1/n  (span=n 이 아님 — §7.1 참조)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, 100.0)   # 연속 상승 구간
    out.iloc[:n] = np.nan                    # 워밍업
    return out.rename(f"rsi_{n}")


def disparity(close: pd.Series, n: int = 20) -> pd.Series:
    """이격도 = 종가 / SMA(n) * 100."""
    return (close / close.rolling(n).mean() * 100.0).rename(f"disparity_{n}")


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return pd.DataFrame({
        "mid": mid, "upper": mid + k * sd, "lower": mid - k * sd,
        "pct_b": (close - (mid - k * sd)) / (2 * k * sd),
    })


def zscore(s: pd.Series, window: int = 252) -> pd.Series:
    """롤링 z-score. 레짐 무관 비교를 위한 정규화."""
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std(ddof=0)
    return ((s - mu) / sd.replace(0.0, np.nan)).rename(f"{s.name}_z")


def pct_rank(s: pd.Series, window: int = 252) -> pd.Series:
    """롤링 백분위 순위 [0,1]. 임계값 하드코딩 대신 사용 권장."""
    return s.rolling(window).rank(pct=True).rename(f"{s.name}_pr")
