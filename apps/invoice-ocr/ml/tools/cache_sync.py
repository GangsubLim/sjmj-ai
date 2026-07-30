"""원격 산출물 → 로컬 캐시 동기화 공통 글루(blank_crop_report·warp_gate_report 공유).

두 도구의 fetch는 같은 절차다 — 원격 crop 루트에서 산출 목록을 ls로 받고, tar로 끌어와
캐시 하위 디렉터리를 통째로 갈아끼우고, 데이터 매니페스트와 meta.json을 쓴다. 이 절차가
도구마다 복제돼 있으면 결함도 함께 복제된다(실제로 rmtree 결함이 두 벌로 복제돼 있었다).

실제 ssh 호출은 단위테스트 비대상이고, 캐시 조작(디렉터리 리셋·매니페스트 쓰기)만
테스트한다 — run_ssh를 대역으로 갈아끼우면 호출자(CLI)의 fetch 경로 전체가 돈다.
"""

import contextlib
import io
import json
import shlex
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from tools.remote import run_ssh, source_env

META_NAME = "meta.json"


class CacheError(RuntimeError):
    """로컬 캐시를 안전하게 갈아끼울 수 없는 상태(삭제 실패·심볼릭 링크 등)."""


def remote_file_list(host: str, worker_env: str, pattern: str) -> list[str]:
    """원격 crop 루트(`$SJMJ_DATA_DIR/ocr_crops`)에서 pattern에 맞는 산출 목록을 받는다."""
    # `cd X && ls ... || true`는 cd 실패까지 exit 0으로 덮어 '빈 목록'을 정상 반환한다 —
    # 그러면 fetch는 0건으로 성공하고 리포트는 전 건을 '없음'으로 태연히 찍는다.
    # 디렉터리 존재를 먼저 단언해 run_ssh()가 예외를 던지게 한다.
    script = (
        f"{source_env(worker_env)}"
        '[ -d "$SJMJ_DATA_DIR/ocr_crops" ] || '
        '{ echo "ocr_crops 없음: $SJMJ_DATA_DIR" >&2; exit 3; }; '
        f'cd "$SJMJ_DATA_DIR/ocr_crops"; ls -d {pattern} 2>/dev/null || true'
    )
    return [ln for ln in run_ssh(host, script).decode().split("\n") if ln.strip()]


def invalidate_manifest(cache: Path, data_name: str) -> None:
    """데이터 매니페스트와 meta.json을 먼저 지운다 — 부분 실패가 하이브리드 캐시를 남기지 않게.

    fetch는 산출 디렉터리를 먼저 비우고 매니페스트를 마지막에 쓴다. 중간에 ssh·tar가
    타임아웃·중단되면 산출은 새 상태(비었거나 반쯤)인데 meta는 **이전 회차** 그대로 남아,
    리포트 헤더가 옛 fetched_at을 '동기화' 시각으로 찍고 신규 쌍은 어디에도 안 나타난다.
    진입 시 무효화해 두면 그 상태가 "fetch를 실행할 것"으로 유도된다(load_cache_meta).
    """
    for name in (data_name, META_NAME):
        (cache / name).unlink(missing_ok=True)


def reset_dir(path: Path) -> None:
    """디렉터리를 통째로 비우고 다시 만든다 — 삭제 실패를 삼키지 않는다.

    `shutil.rmtree(..., ignore_errors=True)`는 삭제 실패를 전량 먹는다. 특히 path가
    심볼릭 링크면 rmtree는 `OSError: Cannot call rmtree on a symbolic link`를 내는데,
    그것이 삼켜지면 ① 링크가 남아 옛 산출이 전량 생존하고 ② `mkdir(exist_ok=True)`도
    `is_dir()`가 링크를 따라가 통과하며 ③ 이어지는 추출이 링크 **대상**(=캐시 바깥)에
    쓴다. 권한·EBUSY 실패도 같은 모양으로 무증상이다 — "옛 산출을 지운다"는 계약이
    실패 경로에서 성립하지 않으면 stale 산출이 임계 근거를 오염시킨다.

    Raises:
        CacheError: path가 심볼릭 링크일 때.
        OSError: 권한·EBUSY 등으로 삭제에 실패했을 때(더 이상 삼키지 않는다).
    """
    if path.is_symlink():
        raise CacheError(
            f"캐시 하위가 심볼릭 링크다({path} → {path.readlink()}) — 옛 산출을 지울 수 "
            "없고 추출이 링크 대상(캐시 바깥)에 쓰인다. 링크를 직접 제거한 뒤 다시 실행할 것."
        )
    # 첫 fetch라 지울 것이 없는 것은 실패가 아니다 — 그 외 실패는 전부 올린다.
    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(path)
    path.mkdir(parents=True)


def sync_remote_files(host: str, worker_env: str, *, pattern: str, dest: Path) -> list[str]:
    """원격 목록을 받아 dest를 통째로 갈아끼운다.

    이전 fetch 산출을 남겨두면 원격에서 사라지거나 재처리된 잡의 옛 산출이 그대로 평가돼
    임계 근거가 조용히 오염된다 — 그래서 매번 dest를 비우고 새로 받는다. 지우는 범위는
    도구가 만든 dest 하위뿐이다(사용자 지정 --cache 상위는 건드리지 않는다).

    호출자가 찍는 건수는 **원격 ls 개수**라 로컬 추출이 반쪽이어도 성공처럼 보인다 —
    추출 후 로컬 파일 수를 세어 어긋나면 경고한다.

    Returns:
        원격 ls가 돌려준 파일 목록.
    """
    names = remote_file_list(host, worker_env, pattern)
    reset_dir(dest)
    if names:
        # 빈 목록이면 원격 tar가 인자 없이 죽는다 — 호출자의 fetch 경고가 대신 말한다.
        # 파일명은 원격 ls 산출이지만 원격 셸에 다시 들어가므로 방어적으로 quote한다.
        args = " ".join(shlex.quote(n) for n in names)
        tar_script = f'{source_env(worker_env)}tar -C "$SJMJ_DATA_DIR/ocr_crops" -cf - {args}'
        with tarfile.open(fileobj=io.BytesIO(run_ssh(host, tar_script))) as tf:
            tf.extractall(dest, filter="data")
    n_local = sum(1 for p in dest.rglob("*") if p.is_file())
    if n_local != len(names):
        print(
            f"⚠️  원격 목록 {len(names)}건인데 로컬에 {n_local}건만 풀렸다({dest}) — "
            "추출이 부분 실패했을 수 있다. fetch를 다시 실행할 것."
        )
    return names


def write_manifest(
    cache: Path,
    data_name: str,
    data: list[dict],
    *,
    host: str,
    counts: dict[str, int],
    extra: dict | None = None,
) -> dict:
    """데이터 매니페스트와 meta.json을 쓰고 meta를 돌려준다.

    Args:
        cache: 캐시 디렉터리.
        data_name: 데이터 매니페스트 파일명(jobs.json·pairs.json).
        data: 매니페스트에 실을 레코드.
        host: 이 캐시를 만든 ssh 호스트.
        counts: meta에 펼쳐 실을 건수 집계.
        extra: 도구별 추가 meta 키(생략하면 meta 모양이 종전과 같다). 쓰기 커맨드가
            fetch 시점의 대상 신원을 대조할 때 쓴다(blank_crop_report의 backend_env).
    """
    meta = {
        "fetched_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "host": host,
        **counts,
        **(extra or {}),
    }
    (cache / data_name).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (cache / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def load_cache_meta(cache: Path, data_name: str, *, tool: str) -> dict:
    """report/apply에 필요한 캐시 파일을 확인하고 meta.json을 읽는다.

    Raises:
        SystemExit: 캐시 파일이 없을 때. fetch 미실행이 가장 흔한 원인인데 맨
            FileNotFoundError는 그 사실도, 다음에 뭘 해야 하는지도 알려주지 않는다.
    """
    missing = [name for name in (data_name, META_NAME) if not (cache / name).exists()]
    if missing:
        raise SystemExit(
            f"캐시가 없다({', '.join(missing)}) — 먼저 fetch를 실행할 것: "
            f"`python -m tools.{tool} --cache {cache} fetch`"
        )
    return json.loads((cache / META_NAME).read_text())
