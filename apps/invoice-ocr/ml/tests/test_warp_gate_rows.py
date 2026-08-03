"""tools.warp_gate_rows — 재워프·행 재현 글루(합성 데이터만, 실데이터 비의존)."""

import pytest

pytest.importorskip("cv2", exc_type=ImportError)
np = pytest.importorskip("numpy")

from tools.warp_gate_rows import (  # noqa: E402
    STATUS_OK,
    STATUS_QUAD_MISSING,
    STATUS_UPLOAD_MISSING,
    STATUS_UPLOAD_UNREADABLE,
    job_metrics,
    rewarp_job,
)

_PAGE_BLUE = (255, 120, 40)


def _write_png(path, bgr):
    import cv2

    cv2.imwrite(str(path), bgr)


def _photo_with_tilted_grid(angle_deg=4.0):
    """빈 종이를 촬영한 원본 사진을 흉내내는 합성 입력 — 축정렬 파랑 테두리(=quad) +
    quad와 무관한 각도로 기운 내부 격자선.

    테두리는 축정렬로 그려서 코너검출(form_quad_robust)이 직결로 찾게 하고, warp는 quad
    코너만 dst 사각형에 맞추므로 결과 테두리는 항상 축정렬로 나온다(원근변환의 정의) —
    잔여 기울기를 재현하려면 quad 자체가 아니라 quad *내부* 내용물을 quad와 무관한 각도로
    그려야 한다(rectify.py 모듈 docstring이 말하는 "4-코너 워프는 모서리만 맞춘다" 문제의
    재현). 이 잔여 기울기가 rewarp의 deskew 단계(rotate+deskew_angle)가 실제로 지우는
    대상이다.
    """
    import cv2

    from handwriting.grid_v4 import WARP_H, WARP_W

    canvas_w, canvas_h = WARP_W + 100, WARP_H + 100
    page_x0, page_y0 = 50, 50
    img = np.full((canvas_h, canvas_w, 3), 255, np.uint8)
    cv2.rectangle(img, (page_x0, page_y0), (page_x0 + WARP_W, page_y0 + WARP_H), _PAGE_BLUE, 15)

    overlay = np.full((canvas_h, canvas_w, 3), 255, np.uint8)
    y_start, pitch, n_lines, thickness = page_y0 + 620, 83, 16, 10
    for k in range(n_lines):
        y = y_start + k * pitch
        cv2.line(overlay, (page_x0 + 20, y), (page_x0 + WARP_W - 20, y), _PAGE_BLUE, thickness)
    center = (page_x0 + WARP_W / 2, page_y0 + WARP_H / 2)
    rot = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    tilted = cv2.warpAffine(
        overlay, rot, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )
    mask = np.any(tilted != 255, axis=2)
    img[mask] = tilted[mask]
    return img


def test_rewarp_job_reports_missing_upload_without_reading_anything(tmp_path):
    def _spy(_path):
        raise AssertionError("missing upload must short-circuit before any read")

    status, warped = rewarp_job(tmp_path / "nope.jpg", loader=_spy)
    assert status == STATUS_UPLOAD_MISSING
    assert warped is None


def test_rewarp_job_reports_unreadable_upload(tmp_path):
    # 파일은 존재하고 실제로는 읽을 수 있는 유효 이미지지만, 주입한 loader가 대신
    # 읽기 실패를 낸다 — loader 인자가 실제로 쓰이는지(무시하고 기본 로더로 대체하지
    # 않는지)를 증명한다. 그래야 62잡 전수 리포트가 중간에 죽지 않아야 한다는 계약을
    # loader 구현체 무관하게 검증한 것이 된다.
    valid = tmp_path / "broken.jpg"
    _write_png(valid, np.full((10, 10, 3), 255, np.uint8))

    def _boom(_path):
        raise OSError("synthetic decode failure")

    status, warped = rewarp_job(valid, loader=_boom)
    assert status == STATUS_UPLOAD_UNREADABLE
    assert warped is None


def test_rewarp_job_reports_unreadable_upload_on_decompression_bomb(tmp_path, monkeypatch):
    # PIL.Image.DecompressionBombError는 Exception 직계라 OSError로 안 잡힌다 — 좁힌
    # except 튜플이 이 타입을 놓치면 초대형 업로드 1장이 전수 리포트를 중간에 죽인다.
    # 기본 loader(load_bgr_path → PIL.Image.open) 경로로 실제 예외 타입을 재현한다.
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    huge = tmp_path / "huge.png"
    _write_png(huge, np.full((50, 50, 3), 255, np.uint8))

    status, warped = rewarp_job(huge)
    assert status == STATUS_UPLOAD_UNREADABLE
    assert warped is None


def test_rewarp_job_reports_quad_missing_when_the_sheet_has_no_form(tmp_path):
    # monkeypatch로 _form_quad를 대신하지 않는다 — 실측: 흰 배경만 있는 이미지에서
    # form_quad_robust는 실제로 None을 반환한다. 이 경로가 운영과 동일한 quad 검출을
    # 그대로 통과해야 quad 미검출을 흉내내는 변이(_form_quad 조작)도 함께 잡힌다.
    blank = tmp_path / "blank.png"
    _write_png(blank, np.full((400, 300, 3), 255, np.uint8))
    status, warped = rewarp_job(blank)
    assert status == STATUS_QUAD_MISSING
    assert warped is None


def test_rewarp_job_returns_the_template_sized_rewarp_via_injected_loader(tmp_path):
    from handwriting.grid_v4 import WARP_H, WARP_W

    photo = _photo_with_tilted_grid()
    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    status, warped = rewarp_job(stub, loader=lambda _path: photo)

    assert status == STATUS_OK
    assert warped.shape == (WARP_H, WARP_W, 3)


def test_rewarp_job_deskews_the_residual_tilt_inside_the_detected_quad(tmp_path):
    from handwriting.rectify import deskew_angle

    photo = _photo_with_tilted_grid()
    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    status, warped = rewarp_job(stub, loader=lambda _path: photo)

    assert status == STATUS_OK
    # 테두리(quad)는 warp만으로 항상 축정렬이 되므로, deskew 단계가 생략돼도 shape는
    # 안 바뀐다 — 그 대신 quad 내부에 준 잔여 기울기(약 4도)가 그대로 남는다. deskew가
    # 실행됐다면 이 잔여 기울기가 거의 0으로 지워져 있어야 한다.
    assert abs(deskew_angle(warped)) < 1.0


def test_job_metrics_carries_both_mask_axes(make_warped):
    m = job_metrics(make_warped())
    assert set(m) == {"std", "enh"}
    assert m["std"]["hline_count"] == 16
    assert m["enh"]["hline_count"] > 0


def test_job_metrics_enh_axis_uses_the_enhanced_mask_not_std(make_warped):
    # make_warped()의 강한 파랑(255,120,40)은 표준·enh 마스크 모두를 통과시켜 두 축이 우연히
    # 같은 값이 나온다 — enhanced=True 배선이 빠져도 위 테스트의 "enh > 0"는 못 잡는다.
    # b−r=10의 옅은 파랑은 표준 마스크에서는 0선, enh 마스크에서만 16선이 잡히는 입력
    # (plan "좌표 재검증 결과" §2 실측치)이라 std/enh 배선 자체를 강제한다. 격자 좌표
    # (y_start/pitch/thickness)는 make_warped 기본값과 동일하게 두고 color만 옅은 파랑으로
    # 바꿔 test_warp_gate.py의 _faint_grid()와 동일 입력을 재현한다.
    img = make_warped(color=(250, 120, 240))

    m = job_metrics(img)
    assert m["std"]["hline_count"] == 0
    assert m["enh"]["hline_count"] == 16
