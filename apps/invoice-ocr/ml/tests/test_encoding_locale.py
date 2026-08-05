"""비UTF-8 로케일에서 한글 파일 I/O가 왕복하는지 서브프로세스로 단언한다.

`LC_ALL=C` + `PYTHONUTF8=0`이면 `locale.getpreferredencoding(False)`가 `US-ASCII`로
떨어진다(PEP 540 UTF-8 모드 비활성). `LC_ALL=C` 단독은 UTF-8 모드가 자동 활성화돼
GREEN이 되므로 재현이 안 된다. `locale.getpreferredencoding` monkeypatch도 무효다 —
C 레벨 io가 참조하지 않는다.

드라이버는 stdout에 한글을 내지 않는다(ASCII stdout에서 죽는다). 산출 파일 검증은
부모 프로세스가 UTF-8로 읽어서 한다. 드라이버 소스는 임시 .py 파일로 쓴다 —
`python -c`는 인자 디코딩이 로케일에 걸려 한글 리터럴을 담을 수 없다(PEP 3120에 따라
.py 소스는 로케일과 무관하게 UTF-8로 읽힌다).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]

# PYTHONUTF8=0이 없으면 PEP 540 UTF-8 모드가 자동 활성화돼 재현이 무효가 된다.
NON_UTF8_ENV = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0"}


def run_driver(source: str, *argv: str) -> subprocess.CompletedProcess:
    """비UTF-8 로케일 서브프로세스에서 드라이버를 실행한다.

    Args:
        source: 드라이버 파이썬 소스. stdout에 한글을 내면 안 된다.
        argv: 드라이버에 넘길 인자.
    """
    env = {**os.environ, **NON_UTF8_ENV, "PYTHONPATH": str(ML_ROOT)}  # PATH 보존 — 통째로
    # 갈아끼우면 python이 안 뜬다. PYTHONPATH는 별도로 지정한다 — 드라이버는 스크립트
    # 파일로 실행되므로 sys.path[0]이 tests/가 되고, cwd=ML_ROOT만으로는 tools 패키지가
    # 잡히지 않는다(pytest ini의 pythonpath=["."]는 pytest 프로세스에만 적용되고
    # subprocess에는 전파되지 않는다).
    driver = ML_ROOT / "tests" / "_locale_driver_tmp.py"
    driver.write_text(source, encoding="utf-8")
    try:
        return subprocess.run(
            [sys.executable, str(driver), *argv],
            cwd=ML_ROOT,  # pythonpath = ["."] 전제 — tools 패키지를 찾으려면 ml 루트여야 한다
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        driver.unlink(missing_ok=True)


def test_locale_is_actually_non_utf8():
    # 재현이 성립하는지 먼저 확인한다 — US-ASCII가 아니면 아래 테스트들이 무의미하게 통과한다.
    proc = run_driver("import locale, sys\nsys.stdout.write(locale.getpreferredencoding(False))\n")
    assert proc.returncode == 0, proc.stderr
    assert "ascii" in proc.stdout.lower(), f"재현 로케일이 무효다: {proc.stdout!r}"


def test_cache_sync_round_trips_korean(tmp_path):
    proc = run_driver(
        "import sys\n"
        "from pathlib import Path\n"
        "from tools.cache_sync import load_cache_meta, write_manifest\n"
        "cache = Path(sys.argv[1])\n"
        "write_manifest(cache, 'jobs.json', [{'label': '큐레이션'}], host='맥미니',\n"
        "               counts={'n_jobs': 1})\n"
        "meta = load_cache_meta(cache, 'jobs.json', tool='cache_sync')\n"
        "assert meta['host'] == '맥미니', 'meta 왕복 실패'\n",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))[0]["label"] == "큐레이션"
    )
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["host"] == "맥미니"


def test_curation_images_index_writes_korean(tmp_path):
    proc = run_driver(
        "import sys\n"
        "from pathlib import Path\n"
        "from tools.curation_report import _write_images_index\n"
        "row = {'job_id': 1, 'crop_ref': '1_r0', 'answer': '공임', 'final_label': '공임',\n"
        "       'draft_label': '공임', 'label_bucket': 'exact', 'amount_bucket': 'exact',\n"
        "       'supply': 1000, 'amount_raw': '1,000'}\n"
        "_write_images_index(Path(sys.argv[1]), [row], [1])\n",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    text = (tmp_path / "images_index.md").read_text(encoding="utf-8")
    assert text.startswith("# 큐레이션 크롭 검수 인덱스")
    assert "공임" in text


def test_warp_gate_baseline_reads_korean(tmp_path):
    baseline = tmp_path / "snapshot.json"
    baseline.write_text(json.dumps({"labels": ["옅은 파랑"]}, ensure_ascii=False), encoding="utf-8")
    proc = run_driver(
        "import sys\n"
        "from pathlib import Path\n"
        "from tools.warp_gate_report import _load_baseline\n"
        "snapshot = _load_baseline(Path(sys.argv[1]))\n"
        "assert snapshot['labels'][0] == '옅은 파랑', '베이스라인 읽기 실패'\n",
        str(baseline),
    )
    assert proc.returncode == 0, proc.stderr
