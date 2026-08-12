"""오늘 한 줄 요약 — 탭 5개를 다 돌지 않고 상태를 파악하기 위한 것.

원칙 두 가지.
- **모르면 모른다고 쓴다.** 취약성 지수가 '적용 밖'이면 숫자를 내밀지 않는다.
  요약이 확신을 과장하면 대시보드 전체의 신뢰가 깎인다.
- 순수함수로 둔다. 데이터를 받아 문자열 목록을 만들 뿐 화면을 그리지 않는다.
"""
from __future__ import annotations

import pandas as pd


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def build(*, close: pd.Series | None = None,
          drawdown: pd.Series | None = None,
          vulnerability: pd.Series | None = None,
          applicable: bool | None = None,
          regime: str | None = None,
          regime_days: int | None = None,
          foreign_flow: pd.Series | None = None,
          credit_spread: pd.Series | None = None,
          yield_curve: pd.Series | None = None,
          bsi: pd.Series | None = None,
          meta: dict | None = None) -> list[tuple[str, str, str]]:
    """(레벨, 라벨, 값) 목록. 레벨 = ok | warn | bad | info."""
    out: list[tuple[str, str, str]] = []

    if close is not None and not close.empty and len(close) > 1:
        chg = close.iloc[-1] / close.iloc[-2] - 1
        out.append(("ok" if chg >= 0 else "bad", "KOSPI",
                    f"{close.iloc[-1]:,.0f} ({_fmt_pct(chg)})"))

    if drawdown is not None and not drawdown.empty:
        dd = float(drawdown.iloc[-1])
        lvl = "ok" if dd > -0.05 else ("warn" if dd > -0.15 else "bad")
        out.append((lvl, "고점 대비", f"{dd * 100:.1f}%"))

    if vulnerability is not None and not vulnerability.dropna().empty:
        v = float(vulnerability.dropna().iloc[-1])
        if applicable is False:
            # ★ 이미 조정 중이면 이 지수는 해석 대상이 아니다. 숫자를 내밀지 않는다.
            out.append(("info", "취약성", "적용 밖 (이미 조정 진행 중)"))
        else:
            lvl = "bad" if v >= 0.65 else ("warn" if v >= 0.55 else "ok")
            out.append((lvl, "취약성", f"{v:.2f}"))

    if regime:
        lvl = {"확장": "ok", "회복 시도": "ok", "후퇴": "warn",
               "수축": "bad"}.get(regime, "info")
        tail = f" ({regime_days}일째)" if regime_days else ""
        out.append((lvl, "레짐", f"{regime}{tail}"))

    if foreign_flow is not None and not foreign_flow.empty:
        total = float(foreign_flow.tail(5).sum())
        # 연속일은 **마지막 날의 부호**를 기준으로 센다.
        # 5일 합계 부호로 세면, 마지막 날이 반대 방향일 때 '0일 연속'이라는
        # 말이 안 되는 문구가 나온다.
        vals = [v for v in foreign_flow.tolist() if v == v]
        streak, last_sign = 0, None
        for v in reversed(vals):
            sign = 1 if v > 0 else (-1 if v < 0 else 0)
            if sign == 0:
                break
            if last_sign is None:
                last_sign = sign
            if sign != last_sign:
                break
            streak += 1
        detail = ""
        if streak and last_sign is not None:
            detail = f" · {streak}일 연속 {'순매수' if last_sign > 0 else '순매도'}"
        out.append(("ok" if total >= 0 else "warn", "외국인 5일",
                    f"{total / 1e8:+,.0f}억{detail}"))

    if credit_spread is not None and not credit_spread.empty:
        cs = float(credit_spread.iloc[-1])
        prev = float(credit_spread.iloc[-21]) if len(credit_spread) > 21 else cs
        out.append(("warn" if cs > prev else "ok", "신용스프레드",
                    f"{cs:.2f}%p ({cs - prev:+.2f} vs 1개월 전)"))

    if yield_curve is not None and not yield_curve.empty:
        yc = float(yield_curve.iloc[-1])
        out.append(("bad" if yc < 0 else "ok", "장단기",
                    f"{yc:+.2f}%p" + (" — 역전" if yc < 0 else "")))

    if bsi is not None and not bsi.empty:
        b = float(bsi.iloc[-1])
        out.append(("warn" if b < 100 else "ok", "BSI",
                    f"{b:.0f}" + (" (비관 우위)" if b < 100 else "")))

    if meta:
        failed = [s for s, m in meta.items() if m.get("status") != "ok"]
        if failed:
            out.append(("bad", "수집 실패", ", ".join(sorted(failed))))

    return out
