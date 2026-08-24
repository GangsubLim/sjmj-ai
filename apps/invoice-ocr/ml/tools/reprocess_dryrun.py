"""재처리 드라이런 — 커밋하지 않고 승계·미결 예상치를 낸다(Issue #100 · spec §3.3).

무변경은 "지우는 로직"이 아니라 만들지 않는 구조로 성립한다: 부르는 DB 메서드는
fetch_pairs·fetch_image_path 둘뿐이고, 크롭은 TemporaryDirectory 안에만 만들어
크롭 루트에 job-N.tmp/job-N.old가 생길 자리 자체가 없다(spec §4).

⚠️ 모듈 레벨에 모델 의존(torch/mlx/cv2)을 두지 않는다 — worker.main.load_models와
   handwriting.infer_job은 build_infer_fn 안에서 지연 import한다(worker/main.py와 동일 규약).

tools가 worker를 끄는 첫 사례다(방향은 tools → worker 단방향) — 승계 계획 조립을 복제하지
않기 위한 의존이며, 반대 방향(worker → tools) 의존은 만들지 않는다.
"""

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

# 산출 기본 경로 — ml/.gitignore의 results/ 아래라 커밋 대상이 아니다.
DEFAULT_OUT = Path("results/dryrun/forecast.jsonl")
# 붕괴로 은퇴시킨 잡의 error 값. 런북이 이 문자열로 grep한다.
DEGENERATE = "degenerate"
# 재시도 가능한 비0은 이것 하나뿐이다 — sysexits.h의 EX_TEMPFAIL. 1을 쓰지 않는 이유는
# 파이썬 미처리 예외의 기본 종료 코드가 1이라(실측), DB env 누락·모델 적재 실패 같은
# 재시도 불가 실패가 런북 until 루프에서 무한 재시도되기 때문이다.
EXIT_DEGENERATE = 75
# 사용자 오류·재개 귀속 거부. 재실행해도 같은 결과라 루프가 수렴하지 않으므로 갈라 둔다.
EXIT_USAGE = 2


@dataclass(frozen=True)
class JobForecast:
    """잡 1건의 예측. error가 실리면 예측 불가이며 나머지 수치는 pair_count만 유효하다."""

    job_id: int
    new_row_count: int
    pair_count: int
    relinked: int
    orphaned: int
    error: str | None = None


@dataclass(frozen=True)
class BatchSummary:
    """배치 합계. 분모(pair_count)에는 예측에 성공한 잡만 들어간다."""

    job_count: int
    pair_count: int
    relinked: int
    orphaned: int
    orphan_ratio: float
    failed: int


@dataclass(frozen=True)
class RunMeta:
    """--out 첫 줄에 실리는 실행 메타 — 재개를 이번 배치·이번 코드에 귀속시킨다(spec §3.4).

    job_ids는 정렬·중복 제거된 대상 집합이라 인자 순서가 달라도 같은 배치로 본다.
    code_version은 bank_id.code_version() — 추론 머신의 git SHA다. 뱅크 지문을 쓰지 않는
    이유는 spec §9에 있다(뱅크가 달라도 RelinkPlan은 바뀌지 않는다).
    """

    job_ids: tuple[int, ...]
    code_version: str | None


def summarize(forecasts: list[JobForecast]) -> BatchSummary:
    """잡별 예측을 배치 합계로 접는다 — 예측 불가 잡은 분모에서 뺀다.

    Args:
        forecasts: 잡별 예측 목록.

    Returns:
        배치 합계. pair_count가 0이면 orphan_ratio는 0.0이다.
    """
    ok = [f for f in forecasts if f.error is None]
    pair_count = sum(f.pair_count for f in ok)
    orphaned = sum(f.orphaned for f in ok)
    return BatchSummary(
        job_count=len(ok),
        pair_count=pair_count,
        relinked=sum(f.relinked for f in ok),
        orphaned=orphaned,
        orphan_ratio=(orphaned / pair_count if pair_count else 0.0),
        failed=len(forecasts) - len(ok),
    )


def _row(forecast: JobForecast) -> str:
    if forecast.error is not None:
        cells = f"{forecast.job_id:>5}{'-':>10}{forecast.pair_count:>7}{'-':>8}{'-':>8}{'-':>10}"
        return f"{cells}   예측 불가: {forecast.error}"
    ratio = forecast.orphaned / forecast.pair_count if forecast.pair_count else 0.0
    return (
        f"{forecast.job_id:>5}{forecast.new_row_count:>10}{forecast.pair_count:>7}"
        f"{forecast.relinked:>8}{forecast.orphaned:>8}{ratio * 100:>9.1f}%"
    )


def render(forecasts: list[JobForecast], summary: BatchSummary) -> str:
    """사람이 읽는 표를 만든다 — 자동 중단 게이트는 두지 않는다(이슈 AC: 판단은 사람이).

    new_rows 합과 예측 불가 잡의 pair 합은 여기서 forecasts로 직접 센다 — BatchSummary는
    spec §3.3의 필드만 들고 있고, 두 값은 표시용이라 합계 타입을 늘리지 않는다.

    Args:
        forecasts: 잡별 예측 목록.
        summary: summarize 결과.

    Returns:
        헤더·잡별 행·합계·예측 불가 행으로 이뤄진 여러 줄 문자열.
    """
    ok = [f for f in forecasts if f.error is None]
    lines = [f"{'job':>5}{'new_rows':>10}{'pairs':>7}{'relink':>8}{'orphan':>8}{'orphan%':>10}"]
    lines += [_row(f) for f in sorted(forecasts, key=lambda f: f.job_id)]
    lines.append("─" * 48)
    lines.append(
        f"{'합계':>5}{sum(f.new_row_count for f in ok):>9}{summary.pair_count:>7}"
        f"{summary.relinked:>8}{summary.orphaned:>8}"
        f"{summary.orphan_ratio * 100:>9.1f}%   (잡 {summary.job_count}건)"
    )
    if summary.failed:
        failed_pairs = sum(f.pair_count for f in forecasts if f.error is not None)
        lines.append(
            f"{'예측 불가':>4}{'-':>8}{failed_pairs:>7}{'-':>8}{'-':>8}{'-':>10}"
            f"   (잡 {summary.failed}건 — 위 분모에서 빠짐)"
        )
    return "\n".join(lines)


def meta_line(meta: RunMeta) -> str:
    """RunMeta를 --out 첫 줄 형식으로 직렬화한다."""
    return json.dumps(
        {"job_ids": list(meta.job_ids), "code_version": meta.code_version}, ensure_ascii=False
    )


def parse_meta(line: str) -> RunMeta:
    """--out 첫 줄을 RunMeta로 되돌린다."""
    data = json.loads(line)
    return RunMeta(job_ids=tuple(data["job_ids"]), code_version=data["code_version"])


def record_line(forecast: JobForecast) -> str:
    """JobForecast를 --out 레코드 한 줄로 직렬화한다."""
    return json.dumps(asdict(forecast), ensure_ascii=False)


def parse_done(lines: Iterable[str]) -> dict[int, JobForecast]:
    """이미 예측된 잡을 읽는다 — 재개가 건너뛸 집합이다.

    메타 줄과 빈 줄은 건너뛴다(판별자는 job_id 키의 유무다).

    Args:
        lines: --out의 줄들.

    Returns:
        job_id → JobForecast.
    """
    done: dict[int, JobForecast] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        data = json.loads(line)
        if "job_id" not in data:
            continue
        done[data["job_id"]] = JobForecast(**data)
    return done
