"""지표 스냅샷 — 그날 실제로 무엇이라 말했는지 박제한다.

**왜 필요한가.** 지금 대시보드의 모든 값은 매번 전체 히스토리에서 다시 계산된다.
코드나 방법론이 바뀌면 **과거의 판단도 함께 바뀐다**. 실제로 이 프로젝트에서
ALFRED 시점 데이터를 넣자 같은 날짜의 OOS 성능이 -0.468 에서 -0.304 로 움직였다.
그렇게 되면 "그때 내 지표가 뭐라고 했나"를 영영 확인할 수 없다.

그래서 매일 값을 파일에 남긴다. 규칙은 하나다 — **한번 기록한 날짜는 고치지 않는다.**
그게 없으면 이건 기록이 아니라 그냥 다시 계산한 값이다.
"""
from __future__ import annotations

import pandas as pd

from src import store

TARGET = "snapshots"


def record(rows: dict[str, float], *, date: pd.Timestamp | None = None,
           overwrite: bool = False) -> pd.DataFrame:
    """오늘의 지표 값을 기록한다.

    rows: {metric: value}
    overwrite=False 면 **이미 기록된 (date, metric) 은 손대지 않는다.**
    과거를 고쳐 쓸 수 있으면 기록의 의미가 없다.
    """
    date = pd.Timestamp(date or pd.Timestamp.today()).normalize()
    new = pd.DataFrame({
        "date": date,
        "metric": list(rows),
        "value": [float(v) for v in rows.values()],
    })
    if new.empty:
        return store.read(TARGET)

    existing = store.read(TARGET)
    if existing.empty:
        store.write(TARGET, new)
        return store.read(TARGET)

    if overwrite:
        # 의도적 정정: 새 값이 이긴다
        merged = pd.concat([existing, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date", "metric"], keep="last")
    else:
        # 기본: 이미 기록된 (date, metric) 은 손대지 않는다
        seen = set(zip(existing["date"], existing["metric"]))
        fresh = new[[(d, mt) not in seen
                     for d, mt in zip(new["date"], new["metric"])]]
        if fresh.empty:
            return existing
        merged = pd.concat([existing, fresh], ignore_index=True)

    store.write(TARGET, merged)
    return store.read(TARGET)


def series(metric: str) -> pd.Series:
    """기록된 지표 하나의 시계열."""
    df = store.read(TARGET)
    s = df[df["metric"] == metric].set_index("date")["value"].sort_index()
    return s.rename(metric)


def compare(metric: str, recomputed: pd.Series) -> pd.DataFrame:
    """당시 기록값 vs 지금 다시 계산한 값.

    둘이 벌어지면 방법론이 바뀐 것이다. 그 사실 자체가 정보다 —
    조용히 바뀌는 것보다 드러나는 편이 낫다.
    """
    rec = series(metric)
    if rec.empty:
        return pd.DataFrame()
    df = pd.concat({"기록값": rec, "재계산": recomputed.reindex(rec.index)},
                   axis=1).dropna()
    df["차이"] = df["재계산"] - df["기록값"]
    return df


def collect() -> dict[str, float]:
    """현재 데이터에서 기록할 지표들을 모은다. 실패한 항목은 건너뛴다."""
    from src.indicators import liquidity as lq
    from src.research import regime, vulnerability as vu

    macro, prices, flows = (store.read("macro"), store.read("prices"),
                            store.read("flows"))
    out: dict[str, float] = {}
    if prices.empty:
        return out

    def ms(sid: str) -> pd.Series:
        s = macro[macro["series_id"] == sid].set_index("date")["value"]
        return s.sort_index().astype("float64")

    close = prices[prices["ticker"] == "KRX.1001"].set_index("date")["close"]
    if close.empty:
        return out
    close = close.sort_index().astype("float64")

    out["kospi_close"] = float(close.iloc[-1])
    out["kospi_drawdown"] = float(vu.drawdown(close).iloc[-1])

    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    netliq = None
    if all(not ms(s).empty for s in need):
        netliq = lq.us_net_liquidity(*[ms(s) for s in need])
        if not netliq.empty:
            out["net_liquidity"] = float(netliq.iloc[-1])
        reg = regime.classify(netliq, close)
        if not reg.empty:
            cur = regime.current(reg)
            out["regime_liq_pr"] = cur["liq_pr"]
            out["regime_mom_pr"] = cur["mom_pr"]

    foreign = None
    if not flows.empty:
        sel = flows[(flows["market"] == "KOSPI") &
                    (flows["investor"] == "외국인합계")]
        if not sel.empty:
            foreign = sel.set_index("date")["net_value"].sort_index() / 1e12

    def opt(sid):
        s = ms(sid)
        return None if s.empty else s

    def diff(a, b):
        x, y = ms(a), ms(b)
        return None if x.empty or y.empty else (x - y).dropna()

    comp = vu.build_components(close, turnover=opt("ecos.kospi_value"),
                               foreign_flow=foreign,
                               market_cap=opt("ecos.kospi_marcap"),
                               net_liquidity=netliq,
                               yield_curve=diff("ecos.ktb10y", "ecos.ktb3y"),
                               exports=opt("ecos.exports"),
                               margin_debt=opt("ecos.margin_debt"),
                               pbr=opt("krx.1001_pbr"),
                               credit_spread=diff("ecos.corp_aa", "ecos.ktb3y"))
    idx = vu.build_index(comp).dropna()
    if not idx.empty:
        out["vulnerability"] = float(idx.iloc[-1])
        out["vulnerability_applicable"] = float(bool(vu.near_high(close).iloc[-1]))
    return out
