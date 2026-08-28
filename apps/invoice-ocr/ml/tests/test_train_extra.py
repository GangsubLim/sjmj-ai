"""train extra 계약 — 학습 의존은 로컬 실측 버전에 잠기고 CI에는 들어오지 않는다."""

import tomllib
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]

# 로컬 .venv 실측치(2026-08-28): torch 2.12.1 · torchvision 0.27.1 · transformers 5.12.1.
# 상한을 그 minor에 고정해 학습 환경이 ft_prod.pt를 적재하는 추론 환경과 갈리지 않게 한다.
EXPECTED_TRAIN = [
    "torch>=2.12,<2.13",
    "torchvision>=0.27,<0.28",
    "transformers>=5.12,<5.13",
]


def _pyproject() -> dict:
    with (ML_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_core_dependencies_stay_pillow_only_so_torch_never_leaks_into_ci():
    """CI는 `uv sync --frozen --extra worker --extra cv`만 돌린다 — core dependencies는 extra와
    무관하게 항상 설치되므로, torch가 여기로 옮겨오면 train extra 분리가 무의미해진다."""
    deps = _pyproject()["project"]["dependencies"]

    assert deps == ["pillow>=10.0"]


def test_train_extra_pins_the_torch_stack_to_the_measured_local_versions():
    extras = _pyproject()["project"]["optional-dependencies"]

    assert extras["train"] == EXPECTED_TRAIN


def test_train_extra_does_not_change_the_existing_extras():
    extras = _pyproject()["project"]["optional-dependencies"]

    assert extras["cv"] == ["opencv-python-headless>=4.10,<5", "numpy>=1.26,<3"]
    assert extras["worker"] == ["sqlalchemy>=2.0", "pymysql>=1.1"]
    assert extras["dl"] == ["onnxruntime==1.22.0"]


def test_the_lock_file_records_the_train_extra_specifiers():
    """lock이 pyproject를 따라오지 않으면 CI의 uv sync --frozen이 깨진다."""
    lock = (ML_ROOT / "uv.lock").read_text(encoding="utf-8")

    for spec in EXPECTED_TRAIN:
        name, bounds = spec.split(">=", 1)
        entry = f'name = "{name}", marker = "extra == \'train\'", specifier = ">={bounds}"'
        assert entry in lock


def test_ci_installs_only_the_worker_and_cv_extras_for_ml():
    """학습 의존은 CI에 들어오지 않는다 — torch 설치는 ml 잡을 수 분대로 늘린다."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uv sync --frozen --extra worker --extra cv" in ci
    assert "--extra train" not in ci
    assert "torch" not in ci
