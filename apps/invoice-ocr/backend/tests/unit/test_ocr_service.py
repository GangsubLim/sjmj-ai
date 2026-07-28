from contextlib import contextmanager

import pytest

from app.core.errors import AppError
from app.services.ocr_service import OcrService, _validated_suffix


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("a.jpg", ".jpg"),
        ("a.jpeg", ".jpeg"),
        ("a.png", ".png"),
        ("A.PNG", ".png"),
        ("IMG_0001.JPG", ".jpg"),
        ("IMG 0001.JPG", ".jpg"),  # 공백(U+0020)은 제어문자 경계 바깥 — 흔한 실사용 파일명
        ("사진.Jpeg", ".jpeg"),
    ],
)
def test_returns_lowercased_suffix_for_allowed_extensions(filename, expected):
    assert _validated_suffix(filename) == expected


@pytest.mark.parametrize("filename", ["photo", "", "x.sh", "x.php", "x.jpg.sh", "x.webp"])
def test_rejects_missing_or_disallowed_suffix(filename):
    with pytest.raises(AppError) as ei:
        _validated_suffix(filename)
    assert ei.value.status == 400
    assert ei.value.code == "VALIDATION_ERROR"
    assert ei.value.details == {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."}


@pytest.mark.parametrize(
    "filename", ["x'\n.jpg", "x\x00.jpg", "x\x1f.jpg", "x\x7f.jpg", "\x00.png"]
)
def test_rejects_control_characters_anywhere_in_filename(filename):
    with pytest.raises(AppError) as ei:
        _validated_suffix(filename)
    assert ei.value.status == 400
    assert ei.value.code == "VALIDATION_ERROR"
    assert ei.value.details == {"photo": "파일명에 제어문자를 사용할 수 없습니다."}


def test_control_char_check_precedes_suffix_check():
    """제어문자 + 비허용 확장자 조합은 제어문자 메시지로 거부돼야 한다(검사 순서 고정).

    두 검사가 모두 걸리는 입력이라야 순서를 실제로 고정한다 — 허용 확장자를 쓰면
    순서를 뒤집어도 같은 메시지가 나와 회귀를 못 잡는다.
    """
    with pytest.raises(AppError) as ei:
        _validated_suffix("scan\x07.sh")
    assert ei.value.details == {"photo": "파일명에 제어문자를 사용할 수 없습니다."}


class _StubRepo:
    """find_job을 제공하지 않는 스텁 — crop_image가 job_exists만 쓰는지 실증한다."""

    def __init__(self, exists: bool):
        self._exists = exists

    def job_exists(self, job_id: int) -> bool:
        return self._exists


def _make_crop(root, job_id: int, row: int):
    """$SJMJ_DATA_DIR/ocr_crops/job-{id}/row-{n}.png를 만들고 그 경로를 반환한다."""
    crop_dir = root / "ocr_crops" / f"job-{job_id}"
    crop_dir.mkdir(parents=True, exist_ok=True)
    path = crop_dir / f"row-{row}.png"
    path.write_bytes(b"\x89PNG")
    return path


def test_crop_image_uses_job_exists_not_find_job(tmp_path, monkeypatch):
    """crop_image는 result_json 파싱하는 find_job이 아니라 경량 job_exists를 써야 한다.

    _StubRepo에 find_job이 없으므로, crop_image가 find_job을 호출하면
    AttributeError로 즉시 드러난다(성능 회귀 가드).
    """
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    expected = _make_crop(tmp_path, 1, 0)
    service = OcrService(repo=_StubRepo(True))
    # endswith가 아니라 조립된 전체 경로와 동등 비교한다 — 계약은 절대경로 반환이고
    # (라우터가 FileResponse에 그대로 넘긴다) 디스크 레이아웃도 워커와 공유하는 계약이다.
    # endswith였다면 상대경로·파일명만 반환으로 회귀해도 통과해 운영에서 cwd 의존으로 깨진다.
    assert service.crop_image(1, 0) == str(expected)


def test_crop_image_404_when_job_missing_via_job_exists(tmp_path, monkeypatch):
    """잡 부재 404가 파일 부재 404에 가려지지 않게 crop 파일을 먼저 만들어 둔다.

    파일이 없으면 job_exists 가드를 통째로 지워도 뒤따르는 is_file 가드가 같은 status·code로
    404를 내 통과한다 — 두 갈래는 message로만 구별된다.
    """
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    _make_crop(tmp_path, 999, 0)
    service = OcrService(repo=_StubRepo(False))
    with pytest.raises(AppError) as ei:
        service.crop_image(999, 0)
    assert ei.value.status == 404
    assert ei.value.code == "NOT_FOUND"
    assert ei.value.message == "OCR 잡을 찾을 수 없습니다."


def test_crop_image_404_when_file_missing(tmp_path, monkeypatch):
    """잡은 있는데 crop PNG가 없는 갈래(is_file 가드)."""
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    service = OcrService(repo=_StubRepo(True))
    with pytest.raises(AppError) as ei:
        service.crop_image(1, 0)
    assert ei.value.status == 404
    assert ei.value.code == "NOT_FOUND"
    assert ei.value.message == "crop 이미지가 없습니다."


class _SpyInvoiceService:
    """confirm이 invoice 생성에 넘기는 payload를 그대로 붙잡는 스파이."""

    def __init__(self):
        self.payload = None

    def create(self, payload: dict) -> dict:
        self.payload = payload
        return {"id": 7}


class _ConfirmRepo:
    """confirm 경로용 스텁 — 잡은 done, 아직 미확정."""

    def __init__(self, result_json: dict):
        self._result_json = result_json
        self.correction = None

    def claim_job(self, job_id: int) -> dict:
        return {
            "id": job_id,
            "status": "done",
            "invoice_id": None,
            "result_json": self._result_json,
        }

    def link_invoice(self, job_id: int, invoice_id: int) -> int:
        return 1

    def insert_correction(self, job_id: int, invoice_id: int, correction: dict) -> int:
        self.correction = correction
        return 1


class _NoopCurationRepo:
    def insert_training_pairs(self, pairs: list[dict]) -> None:
        pass


@contextmanager
def _noop_transaction():
    yield


def test_confirm_strips_ocr_only_keys_at_the_invoice_service_seam():
    """crop_ref·label_source는 invoice 생성 payload에 실리지 않아야 한다.

    DB 관측으로는 이 불변식을 고정할 수 없다 — InvoiceRepository.insert_item이 명시 바인드
    파라미터만 쓰므로 strip을 통째로 지워도 SQL과 저장 결과가 동일하다. 관측 가능한 seam은
    invoice_service.create에 넘어간 payload뿐이다.
    """
    spy = _SpyInvoiceService()
    repo = _ConfirmRepo({"rows": [{"crop_ref": "job-1/row-0", "item_top5": [], "supply": 100}]})
    service = OcrService(
        repo=repo,
        invoice_service=spy,
        transaction=_noop_transaction,
        curation_repo=_NoopCurationRepo(),
    )
    payload = {
        "issue_date": "2026-05-15",
        "recipient": "한양운수",
        "items": [
            {
                "name": "타이어",
                "supply": 100,
                "crop_ref": "job-1/row-0",
                "label_source": "top1_kept",
            }
        ],
    }

    service.confirm(1, payload)

    assert set(spy.payload["items"][0]) == {"name", "supply"}
    # 원본 payload는 손상되지 않아야 한다 — build_correction이 label_source를 여기서 읽는다.
    assert payload["items"][0]["label_source"] == "top1_kept"
    assert repo.correction["lines"][0]["label_source"] == "top1_kept"
