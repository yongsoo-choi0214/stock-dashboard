"""ETL 엔트리포인트. 소스별 예외를 격리하고 _meta.json 을 갱신한다 (설계원칙 5).

사용:
    python -m src.etl.run_all
    python -m src.etl.run_all --only fred
    python -m src.etl.run_all --lookback 90
"""
from __future__ import annotations

import argparse
import sys
import traceback

from src import store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def build_registry() -> dict:
    """사용 가능한 fetcher 만 등록. 미구현 모듈은 조용히 건너뛴다."""
    reg: dict = {}

    from src.etl.fred import FredFetcher
    reg["fred"] = FredFetcher

    try:
        from src.etl.ecos import EcosFetcher
        reg["ecos"] = EcosFetcher
    except ImportError:
        pass

    try:
        from src.etl.krx import KrxIndexFetcher, KrxFlowFetcher
        reg["krx_index"] = KrxIndexFetcher
        reg["krx_flow"] = KrxFlowFetcher
    except ImportError:
        pass

    try:
        from src.etl.us_equity import UsEquityFetcher
        reg["us_equity"] = UsEquityFetcher
    except ImportError:
        pass

    return reg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="실행할 소스 이름 (미지정 시 전체)")
    ap.add_argument("--lookback", type=int, default=None,
                    help="재수집 구간(일). 기본값은 settings.LOOKBACK_DAYS")
    a = ap.parse_args()

    registry = build_registry()
    names = a.only or list(registry)
    unknown = [n for n in names if n not in registry]
    if unknown:
        print(f"알 수 없는 소스: {unknown}  (가능: {list(registry)})")
        return 2

    failed = []
    for name in names:
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
        try:
            fetcher = registry[name]()
            kw = {"lookback_days": a.lookback} if a.lookback is not None else {}
            fetcher.run(**kw)
        except Exception as e:
            # 한 소스의 실패가 전체를 죽이지 않는다
            print(f"[{name}] FAILED {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
            store.update_meta(name, "fail", error=f"{type(e).__name__}: {e}")
            failed.append(name)

    print(f"\n{'=' * 60}\n완료: {len(names) - len(failed)}/{len(names)} 성공")
    if failed:
        print(f"실패: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
