"""KRX 로그인 정보를 .env 에 안전하게 기록한다.

**이 스크립트는 회원님 터미널에서 직접 실행하세요.**
비밀번호는 getpass 로 받아 화면에 찍히지 않고, 셸 히스토리에도 남지 않으며,
표준출력으로 나가지 않습니다. 곧바로 .env 에만 기록됩니다.

    cd c:/Research/stock-dashboard
    .venv/Scripts/python.exe scripts/set_krx_login.py

기존 .env 의 다른 키(FRED/ECOS)는 건드리지 않습니다.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
MANAGED = ("KRX_ID", "KRX_PW")


def read_env() -> list[str]:
    if not ENV.exists():
        return []
    return ENV.read_text(encoding="utf-8").splitlines()


def write_env(lines: list[str], values: dict[str, str]) -> None:
    """관리 대상 키만 교체하고 나머지 줄은 원본 그대로 보존한다."""
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line else ""
        if name in values:
            out.append(f"{name}={values[name]}")
            seen.add(name)
        else:
            out.append(line)
    for name, val in values.items():
        if name not in seen:
            out.append(f"{name}={val}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    print(f"\n대상 파일: {ENV}")
    print("(.gitignore 에 등록되어 있어 커밋되지 않습니다)\n")
    print("data.krx.co.kr 계정 정보를 입력하세요. 비밀번호는 화면에 표시되지 않습니다.")
    print("취소하려면 Ctrl+C.\n")

    try:
        uid = input("KRX 아이디: ").strip()
        if not uid:
            print("아이디가 비어 있습니다. 중단합니다.")
            return 1
        pw = getpass.getpass("KRX 비밀번호: ")
        pw2 = getpass.getpass("한 번 더 입력: ")
    except (KeyboardInterrupt, EOFError):
        print("\n취소했습니다.")
        return 1

    if pw != pw2:
        print("두 비밀번호가 다릅니다. 다시 실행하세요.")
        return 1
    if not pw:
        print("비밀번호가 비어 있습니다. 중단합니다.")
        return 1

    write_env(read_env(), {"KRX_ID": uid, "KRX_PW": pw})
    print(f"\n저장했습니다. (아이디 {uid}, 비밀번호 {len(pw)}자)")
    print("\n다음:")
    print("  .venv/Scripts/python.exe -m src.etl.check_keys")
    print("  .venv/Scripts/python.exe -m src.etl.run_all --only krx_flow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
