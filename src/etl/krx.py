"""한국 지수/수급 (CLAUDE.md §5.5).

★ 명세와의 차이 — pykrx 1.2+ 는 data.krx.co.kr 로그인을 요구한다.
   Phase 1에서 공개 JSON 엔드포인트를 직접 호출해도 `400 LOGOUT` 이 돌아오는 것을
   확인했다. 클라이언트 문제가 아니라 서버 측 강제다.

   - 지수 OHLCV : FinanceDataReader 로 대체 (인증 불필요). ticker 는 §3.2 계약대로
                  `KRX.{code}` 를 유지하고 내부에서만 FDR 심볼로 매핑한다.
   - 투자자 수급 : 대체 경로가 없다. KRX_ID/KRX_PW 가 있어야 동작한다.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from config import settings
from src.etl.base import Fetcher, retry

# §3.2 의 KRX 코드 → FinanceDataReader 심볼
FDR_SYMBOL = {"1001": "KS11", "2001": "KQ11", "1028": "KS200"}

#: FinanceDataReader 는 영문 컬럼을 준다
COLMAP = {"Open": "open", "High": "high", "Low": "low",
          "Close": "close", "Volume": "volume"}

#: pykrx 는 한글 컬럼을 준다. 둘을 섞어 쓰면 조용히 KeyError 가 난다.
KRX_COLMAP = {"시가": "open", "고가": "high", "저가": "low",
              "종가": "close", "거래량": "volume"}


@retry(times=3)
def _fdr_read(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import FinanceDataReader as fdr

    return fdr.DataReader(symbol, start.date().isoformat(), end.date().isoformat())


class KrxIndexFetcher(Fetcher):
    source, target, keys = "krx_index", "prices", ["date", "ticker"]
    full_refresh = True

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for item in settings.series_for("krx_index"):
            code = str(item["ticker"])
            symbol = FDR_SYMBOL.get(code)
            if symbol is None:
                print(f"  [{code}] FDR 심볼 매핑 없음 — 건너뜀")
                continue
            try:
                raw = _fdr_read(symbol, start, end)
            except Exception as e:
                # 한 지수의 실패가 전체를 죽이지 않는다 (설계원칙 5)
                print(f"  [{code}] FAIL {type(e).__name__}: {e}")
                continue
            if raw.empty:
                print(f"  [{code}] 데이터 없음")
                continue

            sub = raw.loc[:, [c for c in COLMAP if c in raw.columns]]
            sub = sub.rename(columns=COLMAP).dropna(subset=["close"])
            idx = pd.to_datetime(sub.index)
            if isinstance(idx.dtype, pd.DatetimeTZDtype):
                idx = idx.tz_localize(None)

            df = sub.reset_index(drop=True)
            df.insert(0, "ticker", f"KRX.{code}")
            df.insert(0, "date", idx.normalize())
            frames.append(df)
            print(f"  [KRX.{code}] {item['name']} ({symbol}) {len(df)}행  "
                  f"{df['date'].min().date()}~{df['date'].max().date()}  "
                  f"last close={df['close'].iloc[-1]:,.2f}")
            time.sleep(settings.KRX_SLEEP)   # §7.5 과다 요청 방지

        if not frames:
            return pd.DataFrame(columns=["date", "ticker", *COLMAP.values()])
        return pd.concat(frames, ignore_index=True)


class KrxFlowFetcher(Fetcher):
    """투자자별 **일간** 순매수 대금(원). KRX_ID/KRX_PW 필요.

    ★ get_market_trading_value_by_**investor** 를 쓰면 안 된다.
      그쪽은 요청 구간 전체를 하나로 합산해 13개 투자자 × 1행만 돌려준다.
      1년 단위로 잘라 호출하면 '연 1행'짜리 시계열이 되어 쓸모가 없다.
      by_**date** 가 날짜별 행을 준다. 컬럼이 곧 투자자다.

    full_refresh 를 켜지 않는 이유: 스크래핑이라 호출 비용이 크다(§7.5).
    lookback 재수집으로 최근 구간만 갱신한다.
    """

    source, target, keys = "krx_flow", "flows", ["date", "market", "investor"]

    # '전체'는 매수-매도 합이라 항상 0이다. 저장할 이유가 없다.
    DROP_COLS = {"전체"}

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            raise RuntimeError(
                "KRX_ID/KRX_PW 가 없어 투자자 수급을 받을 수 없습니다. "
                "data.krx.co.kr 회원가입 후 .env 에 기록하세요 (docs/SETUP_KEYS.md)."
            )
        from pykrx import stock

        frames: list[pd.DataFrame] = []
        for market in ("KOSPI", "KOSDAQ"):
            got = 0
            # 장기 구간은 1년 단위로 쪼개 요청한다 (§7.5)
            for s, e in _yearly_chunks(start, end):
                try:
                    raw = stock.get_market_trading_value_by_date(
                        s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), market)
                except Exception as ex:
                    print(f"  [{market}] {s.date()}~{e.date()} "
                          f"FAIL {type(ex).__name__}: {ex}")
                    continue
                finally:
                    time.sleep(settings.KRX_SLEEP)

                if raw is None or raw.empty:
                    continue

                cols = [c for c in raw.columns if c not in self.DROP_COLS]
                # 와이드(날짜×투자자) → 롱포맷 (§3.3)
                long = (raw[cols]
                        .rename_axis("date").reset_index()
                        .melt(id_vars="date", var_name="investor",
                              value_name="net_value"))
                long["market"] = market
                long["net_value"] = pd.to_numeric(long["net_value"],
                                                  errors="coerce")
                long = long.dropna(subset=["net_value"])
                frames.append(long)
                got += len(long)

            print(f"  [{market}] {got}행")

        if not frames:
            return pd.DataFrame(
                columns=["date", "market", "investor", "net_value"])
        return pd.concat(frames, ignore_index=True)


def _yearly_chunks(start: pd.Timestamp, end: pd.Timestamp):
    cur = start
    while cur <= end:
        nxt = min(cur + pd.DateOffset(years=1) - pd.Timedelta(days=1), end)
        yield cur, nxt
        cur = nxt + pd.Timedelta(days=1)


if __name__ == "__main__":
    KrxIndexFetcher().run()


class KrxAuxFetcher(Fetcher):
    """지수 부가 정보 — 시가총액·거래대금을 macro 에 넣는다.

    ECOS 802Y001 은 유가증권시장 시총만 준다. 코스닥 시총이 없어서
    코스닥 취약성 지수를 만들 수 없었다. FDR 은 두 시장 모두 2005년부터 준다.

    금액은 **조원**으로 통일한다 — ECOS 계열과 같은 단위여야 지표가 성립한다.
    """

    source, target, keys = "krx_aux", "macro", ["date", "series_id"]
    full_refresh = True

    #: FDR 컬럼 → (series_id 접미사, 원 단위 → 조원 배수)
    FIELDS = {"MarCap": ("marcap", 1e-12), "Amount": ("amount", 1e-12)}

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for item in settings.series_for("krx_index"):
            code = str(item["ticker"])
            symbol = FDR_SYMBOL.get(code)
            if symbol is None:
                continue
            try:
                raw = _fdr_read(symbol, start, end)
            except Exception as e:
                print(f"  [{code}] FAIL {type(e).__name__}: {e}")
                continue
            if raw.empty:
                continue

            idx = pd.to_datetime(raw.index)
            if isinstance(idx.dtype, pd.DatetimeTZDtype):
                idx = idx.tz_localize(None)

            for col, (suffix, scale) in self.FIELDS.items():
                if col not in raw.columns:
                    continue
                s = pd.to_numeric(raw[col], errors="coerce").dropna()
                if s.empty:
                    continue
                frames.append(pd.DataFrame({
                    "date": idx[raw.index.isin(s.index)].normalize(),
                    "series_id": f"krx.{code}_{suffix}",
                    "value": s.to_numpy(dtype="float64") * scale,
                }))
                print(f"  [krx.{code}_{suffix}] {len(s)}행  "
                      f"last={s.iloc[-1] * scale:,.1f} 조원")
            time.sleep(settings.KRX_SLEEP)

        if not frames:
            return pd.DataFrame(columns=["date", "series_id", "value"])
        return pd.concat(frames, ignore_index=True)


class KrxFundamentalFetcher(Fetcher):
    """지수 밸류에이션 — PER / PBR / 배당수익률. KRX_ID/KRX_PW 필요.

    시가총액은 있었지만 **이익 대비가 없어** '비싼가'를 판단할 근거가 없었다.
    밸류에이션은 조정의 고전적 설명변수다.

    pykrx get_index_fundamental 은 로그인이 필요하고 스크래핑이라 비싸다
    → full_refresh 없이 lookback 증분으로 돈다.
    """

    source, target, keys = "krx_fundamental", "macro", ["date", "series_id"]

    COLMAP = {"PER": "per", "PBR": "pbr", "배당수익률": "divyield"}
    PREFIX = "krx."          # 이 fetcher 가 소유한 series_id 접두사
    SUFFIXES = ("_per", "_pbr", "_divyield")

    def start_from(self, lookback_days: int) -> pd.Timestamp:
        """★ 기본 구현은 macro 테이블 **전체**의 최신일을 본다.

        macro 에는 다른 소스가 매일 넣는 계열이 많아, 이 fetcher 가 처음
        도는 날에도 '최신일 - 30일'로 잡혀 한 달치만 받게 된다.
        자기 계열만 보고 판단해야 첫 수집에서 전체 히스토리를 가져온다.
        """
        from src import store

        df = store.read(self.target)
        if df.empty:
            return pd.Timestamp(settings.DEFAULT_START)
        mine = df[df["series_id"].str.startswith(self.PREFIX)
                  & df["series_id"].str.endswith(self.SUFFIXES)]
        if mine.empty:
            return pd.Timestamp(settings.DEFAULT_START)
        return pd.Timestamp(mine["date"].max()) - pd.Timedelta(days=lookback_days)

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            raise RuntimeError(
                "KRX_ID/KRX_PW 가 없어 밸류에이션을 받을 수 없습니다 "
                "(docs/SETUP_KEYS.md).")
        from pykrx import stock

        frames: list[pd.DataFrame] = []
        for item in settings.series_for("krx_index"):
            code = str(item["ticker"])
            got = 0
            for s, e in _yearly_chunks(start, end):
                try:
                    raw = stock.get_index_fundamental(
                        s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), code)
                except Exception as ex:
                    print(f"  [{code}] {s.date()}~{e.date()} "
                          f"FAIL {type(ex).__name__}: {ex}")
                    continue
                finally:
                    time.sleep(settings.KRX_SLEEP)
                if raw is None or raw.empty:
                    continue

                idx = pd.to_datetime(raw.index)
                for col, suffix in self.COLMAP.items():
                    if col not in raw.columns:
                        continue
                    v = pd.to_numeric(raw[col], errors="coerce")
                    # PER 0 은 '적자라 산출 불가'를 뜻한다. 값이 아니라 결측이다.
                    v = v.where(v > 0)
                    v = v.dropna()
                    if v.empty:
                        continue
                    frames.append(pd.DataFrame({
                        "date": idx[raw.index.isin(v.index)].normalize(),
                        "series_id": f"krx.{code}_{suffix}",
                        "value": v.to_numpy(dtype="float64"),
                    }))
                    got += len(v)
            print(f"  [krx.{code}] {got}행")

        if not frames:
            return pd.DataFrame(columns=["date", "series_id", "value"])
        return pd.concat(frames, ignore_index=True)


class KrxSectorFetcher(Fetcher):
    """코스피 규모별·업종별 지수 — 시장 폭(breadth) 계산용. KRX 로그인 필요.

    **왜 전종목이 아니라 지수인가.** 폭을 정확히 재려면 942개 종목 전부가
    필요하지만 20년치면 5백만 행이라 커밋할 크기가 아니다(CLAUDE.md §2).
    업종 44개 + 규모 3개면 25만 행으로 '몇 %의 업종이 추세 위인가',
    '소형주가 대형주 대비 어떤가'를 만들 수 있다. 정밀도를 조금 내주고
    저장 비용을 20분의 1로 줄이는 거래다.

    지수당 약 12초라 전체 백필이 10분 가까이 걸린다 → full_refresh 없이
    lookback 증분으로 돈다. start_from 도 자기 계열 기준으로 본다.
    """

    source, target, keys = "krx_sector", "prices", ["date", "ticker"]

    #: ★ 지수 47개를 기본 간격(0.3초)으로 긁다가 KRX 에 차단당했다.
    #: 16개까지 들어온 뒤 서버가 JSON 대신 에러페이지를 돌려주기 시작했고,
    #: pykrx 는 import 단계에서 그걸 만나 JSONDecodeError 로 죽는다.
    #: 백필은 한 번만 하면 되므로 느려도 안전한 쪽을 택한다.
    SLEEP = 1.5

    def _last_dates(self) -> dict[str, pd.Timestamp]:
        """티커별 마지막 관측일. 없는 티커는 빠진다."""
        from src import store

        df = store.read(self.target)
        if df.empty:
            return {}
        mine = {f"KRX.{i['ticker']}" for i in settings.series_for("krx_sector")}
        df = df[df["ticker"].isin(mine)]
        return {} if df.empty else df.groupby("ticker")["date"].max().to_dict()

    def start_from(self, lookback_days: int) -> pd.Timestamp:
        """전체 시작점은 가장 뒤처진 티커에 맞춘다 — 실제 판단은 fetch 안에서
        티커별로 한다(아래 주석 참조)."""
        return pd.Timestamp(settings.DEFAULT_START)

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            raise RuntimeError("KRX_ID/KRX_PW 가 없어 업종지수를 받을 수 없습니다.")
        from pykrx import stock

        items = settings.series_for("krx_sector")
        last = self._last_dates()
        frames, failed = [], []
        for n, item in enumerate(items, 1):
            code = str(item["ticker"])
            # ★ 티커별로 시작점을 따로 잡는다. 47개를 한 번에 긁다 세션이
            #   끊기면 앞쪽만 들어오는데, 전체 최신일 기준으로 재실행하면
            #   빠진 티커가 '최근 30일'만 받고 히스토리를 영영 못 채운다.
            #   실제로 1002~1017 만 들어오고 나머지가 통째로 빠졌었다.
            prev = last.get(f"KRX.{code}")
            t0 = (prev - pd.Timedelta(days=settings.LOOKBACK_DAYS)
                  if prev is not None else start)
            if t0 >= end:
                continue
            try:
                raw = stock.get_index_ohlcv(t0.strftime("%Y%m%d"),
                                            end.strftime("%Y%m%d"), code)
            except Exception as e:
                failed.append(f"{code}({type(e).__name__})")
                # 차단이 시작되면 나머지도 전부 실패한다. 계속 두드리면
                # 차단만 길어지므로 연속 실패 3회에서 멈추고 다음 실행에 재개한다.
                if len(failed) >= 3 and len(failed) >= n - len(frames):
                    print(f"  연속 실패 — KRX 차단으로 보고 중단합니다 "
                          f"({len(frames)}개 수집). 다음 실행에서 재개됩니다.")
                    break
                continue
            finally:
                time.sleep(self.SLEEP)
            if raw is None or raw.empty:
                continue

            # ★ pykrx 는 한글 컬럼이다. FDR 용 COLMAP 을 쓰면 KeyError 가 난다.
            sub = raw.loc[:, [c for c in KRX_COLMAP if c in raw.columns]]
            sub = sub.rename(columns=KRX_COLMAP).dropna(subset=["close"])
            # 업종지수는 거래량이 0 인 날이 있다 — 종가만 있으면 폭 계산에 충분
            idx = pd.to_datetime(sub.index)
            df = sub.reset_index(drop=True)
            df.insert(0, "ticker", f"KRX.{code}")
            df.insert(0, "date", idx.normalize())
            for c in KRX_COLMAP.values():
                if c not in df.columns:
                    df[c] = pd.NA
            frames.append(df)
            if n % 10 == 0:
                print(f"  {n}/{len(items)} …")

        print(f"  지수 {len(frames)}/{len(items)}개 수집"
              + (f", 실패 {failed}" if failed else ""))
        if not frames:
            return pd.DataFrame(columns=["date", "ticker", *KRX_COLMAP.values()])
        return pd.concat(frames, ignore_index=True)
