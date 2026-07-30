"""retrieval artifact 지문 — '이 잡이 어느 retrieval 상태로 추론됐나'의 이진 판정 근거.

지문 입력은 셋이다 — 뱅크 행(key·label·해당 emb 행 바이트) · 모델 파일 다이제스트 ·
배포 코드 SHA. 뱅크만 해시하면 결과가 다른 두 상태가 같은 지문을 갖는다:
  · bank_update apply는 같은 crop_ref를 현재 모델로 다시 임베딩한다(keys 동일, emb 변경).
  · ft_prod.pt만 교체하면 bank.npz는 바이트 단위로 동일한데 쿼리 임베딩이 달라진다.
  · 파일이 안 바뀌고 코드만 배포돼도 전처리·후보 선택이 달라진다(deploy.yml이 배포마다
    ml-worker를 재시작한다).

bank.npz 파일 자체는 해시하지 않는다 — npz는 zip 컨테이너라 다이제스트에 엔트리
타임스탬프·압축 파라미터가 섞이고, 내용이 같은 재작성(rsync·백업 복원·save_bank_atomic
재실행)이 다른 지문을 내 멀쩡한 재평가가 stale로 기각된다. 배열 내용만 해시하면
false-stale이 구조적으로 생기지 않는다.

계층 분리(handwriting/warp_gate.py와 동일 규약):
  · bank_rows·retrieval_fingerprint — stdlib 전용 순수함수. paddle-free venv에서 단위테스트.
  · file_digest·code_version·compute_retrieval_version — 파일·git에 닿는 글루.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

# 이 모듈이 사는 ml 루트. code_version의 기본 repo_dir로 쓴다 — cwd를 쓰면 워커가 다른
# 디렉터리에서 기동됐을 때 엉뚱한 레포의 SHA를 조용히 집을 수 있다(fail-open).
ML_ROOT = Path(__file__).resolve().parent.parent

# 지문 길이 — 리포트 표에 그대로 실리므로 짧게. 12 hex = 48bit로 충돌 위험은 무시 가능.
FINGERPRINT_LEN = 12
_CHUNK = 1 << 20
_GIT_TIMEOUT_SEC = 10.0
# git rev-parse HEAD의 정상 출력 형식 — 이 형식이 아니면 신뢰하지 않는다(M2).
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def bank_rows(keys, labs, emb) -> list[tuple[str, str, bytes]]:
    """(key, label, emb 행 바이트) 묶음을 만든다 — key↔label↔emb 행 대응의 유일한 지점.

    numpy를 import하지 않는다(emb 행의 .tobytes()만 호출) — 코어 paddle-free 규약.

    Args:
        keys: 뱅크 key 열.
        labs: 뱅크 label 열.
        emb: (n, d) 임베딩. 행 단위로 순회 가능해야 한다.

    Returns:
        행마다 (key, label, emb 행 바이트) 3튜플.

    Raises:
        ValueError: 세 열의 길이가 다를 때(zip strict).
    """
    return [(str(k), str(lb), row.tobytes()) for k, lb, row in zip(keys, labs, emb, strict=True)]


def _feed(h, part: bytes) -> None:
    """길이 접두를 붙여 해시에 넣는다 — 구분자 없이 이어 붙이면 경계가 모호해진다.

    ("ab","c")와 ("a","bc")가 같은 바이트가 되는 것을 막는다. 라벨에 임의 문자가 들어갈 수
    있으므로 구분자 문자 대신 길이로 경계를 짓는다.
    """
    h.update(len(part).to_bytes(8, "big"))
    h.update(part)


def retrieval_fingerprint(
    rows: list[tuple[str, str, bytes]], model_digest: str, code_version: str
) -> str:
    """뱅크 행 + 모델 다이제스트 + 코드 SHA를 sha256해 12자 지문을 만든다.

    rows를 key로 정렬하므로 행 순서만 다른 뱅크는 같은 지문을 낸다. 라벨 집합은 담지 않는다
    — 리포트에 필요한 판정은 '현재와 같은가'의 이진값이다.

    Raises:
        ValueError: key가 중복될 때(뱅크 keys=UNIQUE 계약 위반이 지문에 묻히지 않도록),
            또는 model_digest·code_version이 빈 문자열일 때(코드 상태를 모르는데 지문이
            나오는 fail-open을 막는다).
    """
    if not model_digest:
        raise ValueError(
            "model_digest는 빈 문자열일 수 없다 — 모델 상태 불명으로 지문을 만들 수 없다"
        )
    if not code_version:
        raise ValueError(
            "code_version은 빈 문자열일 수 없다 — 코드 상태 불명으로 지문을 만들 수 없다"
        )
    h = hashlib.sha256()
    seen: set[str] = set()
    for key, label, emb_row in sorted(rows, key=lambda r: r[0]):
        if key in seen:
            raise ValueError(f"뱅크 key 중복: {key!r} — 지문에 묻히면 계약 위반이 보이지 않는다")
        seen.add(key)
        _feed(h, key.encode())
        _feed(h, label.encode())
        _feed(h, emb_row)
    _feed(h, model_digest.encode())
    _feed(h, code_version.encode())
    return h.hexdigest()[:FINGERPRINT_LEN]


def file_digest(path) -> str:
    """파일 sha256(hex)을 스트리밍으로 계산한다 — ft_prod.pt는 347MB다."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def code_version(repo_dir=ML_ROOT) -> str | None:
    """배포 코드 SHA(git rev-parse HEAD). 얻지 못하면 None — 자리표시자를 만들지 않는다.

    서버 레포는 태그 checkout이라 detached HEAD지만 rev-parse는 정상 동작한다.
    실패 시 None을 돌려주는 이유: "unknown" 같은 값을 넣으면 서로 다른 코드 상태가 한 지문으로
    합쳐져 fail-open이 된다. 키가 없으면 그 잡은 unknown 코호트로 가고, 그게 정직한 표현이다.
    실패 사유는 stderr 한 줄로 남긴다(handwriting/의 다른 모듈과 동일하게 print 사용) — 워커
    fail-safe 계약상 예외를 던지지 않으므로, 이 한 줄이 launchd `ml-worker.err.log`에서
    원인을 추적하는 유일한 창구다.

    이 함수는 **추론이 실행된 머신에서** 호출해야 유효하다 — repo_dir 기본값(ML_ROOT)은 이
    코드가 실행 중인 머신의 SHA이므로, 분석 도구(curation_report 등)를 로컬에서 호출하면
    원격에서 실제로 추론에 쓰인 SHA와 다를 수 있다(잡이 조용히 stale로 오분류될 위험).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"code_version: git 실행 실패({exc!r}) — retrieval_version 생략", file=sys.stderr)
        return None
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        print(
            f"code_version: git rev-parse HEAD 실패(returncode={proc.returncode}, "
            f"repo_dir={repo_dir}): {stderr} — retrieval_version 생략",
            file=sys.stderr,
        )
        return None
    sha = proc.stdout.decode(errors="replace").strip()
    if not _SHA_RE.match(sha):
        print(
            f"code_version: git 출력이 SHA 형식이 아님({sha!r}) — retrieval_version 생략",
            file=sys.stderr,
        )
        return None
    return sha


def compute_retrieval_version(model_path, keys, labs, emb, *, repo_dir=ML_ROOT) -> str | None:
    """뱅크 배열 + 모델 파일 + 코드 SHA로 지문을 계산한다. 코드 SHA가 없으면 None.

    **추론이 실행된 머신에서** 호출해야 유효하다 — repo_dir 기본값(ML_ROOT)은 이 코드가
    실행 중인 머신의 SHA를 집는다. 분석 도구를 원격 추론 머신이 아닌 로컬에서 호출하면
    전 잡이 조용히 stale로 오분류될 수 있다(code_version 참조).

    Raises:
        OSError: model_path를 읽을 수 없을 때(file_digest).
        ValueError: keys·labs·emb의 길이가 다르거나(bank_rows) 뱅크 key가 중복될 때
            (retrieval_fingerprint).
    """
    version = code_version(repo_dir)
    if version is None:
        return None
    return retrieval_fingerprint(bank_rows(keys, labs, emb), file_digest(model_path), version)
