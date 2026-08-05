"""git 추적 first-party 소스에 `encoding=` 없는 텍스트 파일 I/O가 0건임을 단언한다.

로케일 행위 테스트(test_encoding_locale.py)는 실동작 증거를 주지만 실행 경로로 덮이는
사이트만 본다. viz 2곳과 dataset_build 7곳처럼 테스트에서 실행되지 않는 사이트까지
균일하게 커버하는 수단은 이 전수 가드뿐이다.

탐색 경계로 디렉터리 denylist가 아니라 git 추적 목록을 쓴다 — `uv sync`가 만드는
`ml/.venv/`에 Python 1,246개·동종 위반 41건이 있고, gitignore된 산출물 디렉터리는
git 추적 목록이 구조적으로 배제한다. denylist는 새 산출물이 생길 때마다 샌다.
"""

import ast
import subprocess
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]

# 텍스트 파일 I/O가 아닌 `.open()` 수신자 — **명시 예외 목록**이다.
# 모르는 수신자는 기본적으로 위반으로 잡는다(누락은 조용하지만 오탐은 시끄럽다).
NON_TEXT_OPEN_RECEIVERS = frozenset({"Image", "tarfile"})

# git이 없거나 목록이 비면 가드가 조용히 통과한다 — 알려진 파일로 경계 산출을 검증한다.
SENTINEL_SOURCES = ("tools/warp_gate_report.py", "handwriting/dataset_build.py")


def _tracked_sources() -> list[Path]:
    """git 추적 first-party 파이썬 소스(tests/ 제외)."""
    listed = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ML_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ML_ROOT / rel for rel in listed if not rel.startswith("tests/")]


def _call_kind(call: ast.Call) -> str | None:
    """텍스트 I/O 호출이면 그 형태를, 아니면 None을 준다."""
    fn = call.func
    if isinstance(fn, ast.Name):
        return "bare_open" if fn.id == "open" else None
    if not isinstance(fn, ast.Attribute):
        return None
    if fn.attr in ("read_text", "write_text"):
        return fn.attr
    if fn.attr != "open":
        return None
    receiver = fn.value
    name = receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", None)
    return None if name in NON_TEXT_OPEN_RECEIVERS else "attr_open"


def _mode_arg(call: ast.Call, kind: str) -> ast.expr | None:
    """mode 인자를 뽑는다 — 위치가 호출 형태마다 다르다.

    bare `open(file, mode)`는 args[1], `path.open(mode)`는 args[0]. 이를 무시하면
    handwriting/bank_id.py의 `open(path, "rb")`가 텍스트로 오판된다.
    """
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    if kind == "bare_open" and len(call.args) >= 2:
        return call.args[1]
    if kind == "attr_open" and call.args:
        return call.args[0]
    return None


def _violations(path: Path) -> list[str]:
    """`encoding=` 없는 텍스트 I/O 위치를 `파일:줄 (형태)`로 낸다."""
    rel = path.relative_to(ML_ROOT)
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        kind = _call_kind(node)
        if kind is None:
            continue
        mode = _mode_arg(node, kind)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        found.append(f"{rel}:{node.lineno} ({kind})")
    return found


def test_tracked_sources_are_discoverable():
    sources = _tracked_sources()
    assert sources, "git 추적 .py 목록이 비었다 — 가드가 조용히 통과하는 상태다"
    rels = {str(p.relative_to(ML_ROOT)) for p in sources}
    for sentinel in SENTINEL_SOURCES:
        assert sentinel in rels, f"{sentinel}이 탐색 범위에 없다 — 경계 산출이 깨졌다"
    assert not [p for p in sources if ".venv" in p.parts], ".venv가 탐색에 섞였다"
    assert not [r for r in rels if r.startswith("tests/")], "tests/가 탐색에 섞였다"


def test_no_unspecified_encoding_in_first_party_sources():
    violations = sorted(v for path in _tracked_sources() for v in _violations(path))
    assert not violations, "encoding= 미지정 텍스트 I/O:\n" + "\n".join(violations)
