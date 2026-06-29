import pytest

from app.core.errors import AppError, conflict


def test_conflict_raises_409():
    with pytest.raises(AppError) as exc:
        conflict("이미 확정된 잡입니다.")
    assert exc.value.status == 409
    assert exc.value.code == "CONFLICT"
    assert exc.value.message == "이미 확정된 잡입니다."
