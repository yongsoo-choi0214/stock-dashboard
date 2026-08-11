"""알림 규칙 — 순수함수. 데이터를 받아 Alert 목록을 만들 뿐 전송하지 않는다.

설계
- 규칙은 '지금 이 상태가 참인가'를 판정한다(state). 전송 여부는 run.py 가
  직전 실행의 상태와 비교해 **새로 참이 된 것만** 고른다. 그래야 같은 알림이
  매일 오지 않는다.
- 임계값은 가능하면 롤링 백분위로 잡는다. 상수로 박으면 레짐이 바뀔 때
  무의미해진다(§7.5 와 같은 이유).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.indicators import technical as ta


@dataclass(frozen=True)
class Alert:
    key: str        # 상태 식별자. 같은 key 가 계속 참이면 재전송하지 않는다.
    level: str      # "info" | "warn" | "critical"
    title: str
    body: str

    def format(self) -> str:
        icon = {"info": "🔵", "warn": "🟡", "critical": "🔴"}.get(self.level, "•")
        return f"{icon} *{self.title}*\n{self.body}"


def rsi_extremes(close: pd.Series, name: str, ticker: str, n: int = 14,
                 upper: float = 70.0, lower: float = 30.0) -> list[Alert]:
    """RSI 과매수/과매도 진입."""
    r = ta.rsi(close, n).dropna()
    if r.empty:
        return []
    v = float(r.iloc[-1])
    if v >= upper:
        return [Alert(f"rsi:{ticker}:over", "warn", f"{name} 과매수",
                      f"RSI({n}) = {v:.1f} (≥{upper:.0f})\n종가 {close.iloc[-1]:,.2f}")]
    if v <= lower:
        return [Alert(f"rsi:{ticker}:under", "warn", f"{name} 과매도",
                      f"RSI({n}) = {v:.1f} (≤{lower:.0f})\n종가 {close.iloc[-1]:,.2f}")]
    return []


def regime_shift(regimes: pd.DataFrame) -> list[Alert]:
    """유동성×모멘텀 레짐. key 에 레짐명을 넣어 바뀔 때만 울리게 한다."""
    if regimes.empty:
        return []
    cur = regimes["regime"].iloc[-1]
    liq = float(regimes["liq_pr"].iloc[-1])
    mom = float(regimes["mom_pr"].iloc[-1])
    level = "critical" if cur == "수축" else "info"
    return [Alert(f"regime:{cur}", level, f"레짐 전환 → {cur}",
                  f"유동성Δ 백분위 {liq:.0%} · 모멘텀 백분위 {mom:.0%}")]


def liquidity_shock(net_liq: pd.Series, window: int = 104,
                    z_threshold: float = 2.0) -> list[Alert]:
    """순유동성 주간 변화량의 롤링 z-score 가 임계를 넘었을 때."""
    chg = net_liq.diff().dropna()
    if len(chg) < window:
        return []
    z = ta.zscore(chg.rename("liq"), window).dropna()
    if z.empty:
        return []
    v = float(z.iloc[-1])
    if abs(v) < z_threshold:
        return []
    direction = "유입" if v > 0 else "유출"
    return [Alert(f"liqshock:{'up' if v > 0 else 'down'}",
                  "warn", f"순유동성 급{direction}",
                  f"주간 변화 {chg.iloc[-1]:+,.0f}B$ (z={v:+.2f}, {window}주 기준)")]


def deposit_extreme(ratio: pd.Series, window: int = 60,
                    hi: float = 0.95, lo: float = 0.05) -> list[Alert]:
    """예탁금/시총 비율이 롤링 백분위 극단일 때."""
    pr = ta.pct_rank(ratio.rename("dep"), window).dropna()
    if pr.empty:
        return []
    v, cur = float(pr.iloc[-1]), float(ratio.iloc[-1])
    if v >= hi:
        return [Alert("deposit:high", "info", "예탁금 비중 상위 극단",
                      f"예탁금/시총 {cur:.2f}% — 최근 {window}개월 상위 {1 - v:.0%}")]
    if v <= lo:
        return [Alert("deposit:low", "info", "예탁금 비중 하위 극단",
                      f"예탁금/시총 {cur:.2f}% — 최근 {window}개월 하위 {v:.0%}")]
    return []


def vulnerability_high(index: pd.Series, near_high: pd.Series,
                       threshold: float = 0.65) -> list[Alert]:
    """취약성 지수가 임계를 넘었을 때. **고점 근처일 때만** 울린다.

    조건을 다는 이유가 이 규칙의 핵심이다. 이미 30% 빠진 자리에서 취약성이
    높다고 알리는 건 소음이다 — 그건 이미 아는 사실이다. 고점 근처에서
    높을 때만 새로운 정보다(검증: 조건 없으면 IC -0.126, 조건 붙이면 -0.434).
    """
    v = index.dropna()
    if v.empty:
        return []
    if not bool(near_high.reindex(v.index).ffill().iloc[-1]):
        return []          # 이미 조정 중 — 이 지수를 경보로 쓰면 안 된다
    cur = float(v.iloc[-1])
    if cur < threshold:
        return []
    return [Alert("vuln:high", "warn", "취약성 지수 경계 초과",
                  f"취약성 {cur:.2f} (≥{threshold:.2f}), 고점 근처.\n"
                  f"과거 같은 구간의 향후 60일 최대낙폭은 평균 -8% 수준이었습니다.")]


def data_staleness(meta: dict, max_lag_days: int = 5,
                   today: pd.Timestamp | None = None) -> list[Alert]:
    """수집 실패 / 데이터 지연.

    대시보드가 조용히 낡아가는 것이 가장 위험하다 — 화면은 멀쩡해 보인다.
    """
    today = today or pd.Timestamp.today().normalize()
    out: list[Alert] = []
    for source, m in sorted(meta.items()):
        if m.get("status") != "ok":
            out.append(Alert(f"etl:fail:{source}", "critical", f"수집 실패 — {source}",
                             str(m.get("error", "사유 미기록"))))
            continue
        md = m.get("max_date")
        if not md:
            continue
        lag = (today - pd.Timestamp(md)).days
        if lag > max_lag_days:
            out.append(Alert(f"etl:stale:{source}", "warn", f"데이터 지연 — {source}",
                             f"최신 관측 {md} ({lag}일 전)"))
    return out
