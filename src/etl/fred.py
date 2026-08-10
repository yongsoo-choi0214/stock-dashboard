"""미국 매크로 — FRED (CLAUDE.md §5.3).

두 가지 경로를 지원한다.
  1. FRED_API_KEY 가 있으면 fredapi (공식 API)
  2. 없으면 fredgraph.csv 공개 엔드포인트 (키 불필요, Phase 1에서 200 응답 확인)

§7.4: WALCL만 백만 USD 단위다. series.yaml 의 scale 로 십억으로 맞춘다.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from config import settings
from src.etl.base import Fetcher, retry

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@retry(times=3)
def _fetch_csv(series_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """키 없이 쓰는 공개 CSV 엔드포인트. 결측은 '.' 으로 온다."""
    params = {
        "id": series_id,
        "cosd": start.date().isoformat(),
        "coed": end.date().isoformat(),
    }
    r = requests.get(CSV_URL, params=params, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), na_values=["."])
    date_col, value_col = df.columns[0], df.columns[1]
    s = pd.Series(
        pd.to_numeric(df[value_col], errors="coerce").values,
        index=pd.to_datetime(df[date_col]),
        name=series_id,
    )
    return s.dropna()


@retry(times=3)
def _fetch_api(series_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """공식 API. 키가 있을 때 사용 — 레이트리밋·메타데이터 측면에서 더 안전하다."""
    from fredapi import Fred

    fred = Fred(api_key=settings.FRED_API_KEY)
    s = fred.get_series(series_id, observation_start=start, observation_end=end)
    return pd.Series(s).dropna()


def _check_range(sid: str, scaled, item: dict) -> None:
    """단위 정규화 결과가 상식 범위인지 확인 (§7.4 재발 방지).

    FRED는 시리즈별로 million/billion 이 뒤섞여 있고, 틀려도 예외가 나지 않는다.
    순유동성이 수십만 조 단위로 나오는 형태로만 드러나므로 여기서 잡는다.
    """
    rng = item.get("expect_range")
    if not rng:
        return
    lo, hi = float(rng[0]), float(rng[1])
    vmin, vmax = float(scaled.min()), float(scaled.max())
    if vmin < lo or vmax > hi:
        print(f"  !! [{sid}] 범위 이탈: [{vmin:,.2f}, {vmax:,.2f}] "
              f"⊄ 기대범위 [{lo:,.0f}, {hi:,.0f}] {item['unit_out']} "
              f"— series.yaml 의 scale 을 의심하세요")


class FredFetcher(Fetcher):
    source, target, keys = "fred", "macro", ["date", "series_id"]
    # 시리즈 7건 × CSV 1회 = 수 초. 매번 전체를 받는 편이 정정 반영·멱등성 모두 유리하다.
    full_refresh = True

    def __init__(self, use_api: bool | None = None):
        # None이면 키 유무로 자동 판단
        self.use_api = bool(settings.FRED_API_KEY) if use_api is None else use_api

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        mode = "fredapi" if self.use_api else "fredgraph.csv(no-key)"
        print(f"  mode={mode}")
        get = _fetch_api if self.use_api else _fetch_csv

        frames: list[pd.DataFrame] = []
        for item in settings.series_for("fred"):
            sid, scale = item["id"], float(item["scale"])
            try:
                s = get(sid, start, end)
            except Exception as e:
                # 한 시리즈의 실패가 전체를 죽이지 않는다 (설계원칙 5)
                print(f"  [{sid}] FAIL {type(e).__name__}: {e}")
                continue
            # fredgraph.csv 는 요청 구간에 관측치가 없으면 cosd 를 무시하고
            # 전체 시계열을 돌려준다(M2SL에서 1959년치가 딸려온 사례).
            # 반환 결과를 요청 구간으로 잘라 실행 간 결과를 결정적으로 만든다.
            s = s[(s.index >= start) & (s.index <= end)]
            if s.empty:
                print(f"  [{sid}] 데이터 없음")
                continue

            scaled = s.to_numpy(dtype="float64") * scale        # ★ §7.4 단위 정규화
            _check_range(sid, scaled, item)

            frames.append(pd.DataFrame({
                "date": pd.to_datetime(s.index),
                "series_id": f"fred.{sid}",
                "value": scaled,
            }))
            print(f"  [{sid}] {len(s)}행  "
                  f"{s.index.min().date()}~{s.index.max().date()}  "
                  f"last={s.iloc[-1] * scale:.3f} {item['unit_out']}")

        if not frames:
            return pd.DataFrame(columns=["date", "series_id", "value"])
        return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    FredFetcher().run()
