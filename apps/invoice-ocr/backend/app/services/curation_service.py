"""CurationService — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지 경로 해석.

라우터(HTTP)와 repository(SQL) 사이의 정규화·비즈니스 로직 계층.
"""

import re
from pathlib import Path

from app import db
from app.config import crop_dir
from app.core.errors import conflict, not_found
from app.repositories.curation_repository import CurationRepository


def _normalize_label(label: str | None) -> str:
    """정식 라벨을 등록 기준 형태로 정규화한다(없으면 빈 문자열).

    정규화 규칙은 ml/tools/bank_update.partition_valid가 뱅크에 넣을 라벨을 고르는
    규칙과 같다 — strip 후 빈 문자열이면 등록 대상이 아니다.
    """
    return (label or "").strip()


# 크롭 좌표가 실제 행을 가리키는 형식인지 — ml/tools/bank_update.py의 CROP_REF_RE와 같은
# 규칙이다(두 트리는 서로를 import하지 못한다). 승계에 실패한 미결 쌍은 이 형식을 통과하지
# 못하는 좌표('job-42/orphan-{pair_id}')를 들고 있으므로, 그 자체가 "행과 끊어졌다"는
# 표식이다. exclusion_reason을 마커로 쓰지 않는다 — update_pair가 사람 배제 시 사유를
# NULL로 지우므로 안정적 표식이 아니다(spec §6-1).
_ROW_CROP_REF_RE = re.compile(r"^job-\d+/row-\d+$")


def _has_row_crop(crop_ref: str | None) -> bool:
    """쌍의 좌표가 실제 검출 행을 가리키는지 판정한다."""
    return bool(_ROW_CROP_REF_RE.fullmatch(crop_ref or ""))


class CurationService:
    """큐레이션 도메인 서비스."""

    def __init__(self, repo=None, item_repo=None, *, transaction=None):
        """저장소와 트랜잭션 컨텍스트를 주입받아 초기화한다(미지정 시 기본 구현).

        item_repo는 InvoiceService와 같은 형태의 생성자 주입이다 — 라우터가 넘긴다.
        """
        self.repo = repo or CurationRepository()
        self.item_repo = item_repo
        self._transaction = transaction or db.transaction

    def list_jobs(self, page: int, limit: int) -> tuple[list[dict], int]:
        """검수 큐(페이지)를 조회하고 표시용 타입으로 정규화한다."""
        offset = (page - 1) * limit
        rows, total = self.repo.list_jobs(limit, offset)
        jobs = [
            {
                "job_id": int(r["job_id"]),
                "invoice_id": r["invoice_id"],
                "curation_reviewed": bool(r["curation_reviewed"]),
                # created_at과 같은 취급 — 직렬화는 jsonable_encoder에 맡긴다.
                "curation_reviewed_at": r["curation_reviewed_at"],
                "pair_count": int(r["pair_count"]),
                "unreviewed_count": int(r["unreviewed_count"] or 0),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return jobs, total

    def get_detail(self, job_id: int) -> dict:
        """잡 상세(행별 top5 조인 포함)를 조회한다. 없으면 404."""
        detail = self.repo.find_job_detail(job_id)
        if detail is None:
            not_found("OCR 잡을 찾을 수 없습니다.")
        job = detail["job"]
        result = job.get("result_json") or {}
        # result_json은 ML 워커가 쓴 외부 데이터다 — rows가 null이거나 원소가 dict가 아니면
        # 잡 상세 전체가 500이 되어 그 잡의 검수가 완전히 막힌다. 아래 조인 실패와 같은
        # fail-safe(빈 행)로 닫는다.
        raw_rows = result.get("rows")
        rows = raw_rows if isinstance(raw_rows, list) else []
        rows_by_index = {r.get("row_index"): r for r in rows if isinstance(r, dict)}
        pairs = []
        for p in detail["pairs"]:
            # 미결 쌍은 어떤 경로로도 새 행과 조인되지 않는다 — 아무 조치가 없으면 옛 row-0
            # 미결 라벨 옆에 전혀 다른 줄의 crop과 top5가 붙어, 막으려던 오염을 화면에서
            # 재현한다. 조인 실패(row_index 부재)도 같은 fail-safe(빈 행)로 닫는다.
            crop_available = _has_row_crop(p["crop_ref"])
            row = (rows_by_index.get(int(p["row_index"])) or {}) if crop_available else {}
            pairs.append(
                {
                    "id": int(p["id"]),
                    "crop_ref": p["crop_ref"],
                    "row_index": int(p["row_index"]),
                    "draft_label": p["draft_label"],
                    "final_label": p["final_label"],
                    "canonical_label": p["canonical_label"],
                    "supply": p["supply"],
                    "status": p["status"],
                    "exclusion_reason": p["exclusion_reason"],
                    "reviewed_at": p["reviewed_at"],
                    "top5": row.get("item_top5") or [],
                    # item_conf_threshold 도입 이전 잡은 플래그가 없다 → 확신(하위호환).
                    "uncertain": bool(row.get("item_uncertain", False)),
                    # false면 프론트가 crop URL 자체를 만들지 않는다(spec 불변식 5).
                    "crop_available": crop_available,
                }
            )
        # 미결은 뒤로 — row_index 순서로는 실제 행 사이에 끼어 읽는 사람을 헷갈리게 한다.
        pairs = sorted(pairs, key=lambda x: (not x["crop_available"], x["row_index"], x["id"]))
        return {
            "job_id": int(job["id"]),
            "invoice_id": job["invoice_id"],
            "curation_reviewed": bool(job["curation_reviewed"]),
            "curation_reviewed_at": job["curation_reviewed_at"],
            "warp_ok": bool(result.get("warp_ok", False)),
            "job_token": job["job_token"],
            "created_at": job["created_at"],
            "pairs": pairs,
        }

    def patch_pair(self, pair_id: int, fields: dict, job_token: str) -> dict:
        """학습쌍을 부분 갱신하고 갱신된 쌍 + 잡의 게이트 상태·세대 토큰을 반환한다.

        **낙관적 잠금(spec §12).** 재처리 이전에 검수 화면을 열어둔 사용자가 옛 그림을
        근거로 새 쌍을 PATCH하면 잘못된 canonical_label이 영속된다. 잡 세대 토큰
        (ocr_jobs.updated_at)을 대조해 다르면 409로 거부한다. 대조와 갱신은 아래 기존
        트랜잭션 안에서 하며, 토큰 조회의 FOR UPDATE가 곧 락 순서의 시작점이다.

        수정은 그 잡의 검수 게이트를 **무조건** 해제한다(Issue #52 spec §3.4). 값이 실제로
        바뀌었는지는 판별하지 않는다 — 이미 미검수면 0 → 0 no-op이고, 제외했다 되돌린
        경우도 재확인 대상으로 본다(오클릭 방어가 이 변경의 취지다).

        정식 라벨의 사전 등록은 여기서 하지 않는다(spec §3.3) — mark_reviewed가 단일 등록
        지점이다(ADR 0008).

        Args:
            pair_id: 갱신할 학습쌍 id.
            fields: 화이트리스트 갱신 필드(status/canonical_label).
            job_token: 클라이언트가 잡 상세에서 받아 되보낸 세대 토큰.

        Raises:
            AppError: 쌍이 없으면 404, 토큰이 현재 값과 다르면 409.
        """
        prev = self.repo.find_pair(pair_id)  # non-locking read — 트랜잭션 밖
        if prev is None:
            not_found("학습쌍을 찾을 수 없습니다.")
        job_id = int(prev["job_id"])
        with self._transaction():
            # ①→③ 순서는 락 순서 불변식이다(spec §4.2). ocr_jobs(부모) → training_pairs(자식).
            current = self.repo.get_job_token(job_id)  # ① ocr_jobs — 행잠금 + 세대 대조
            if current != job_token:
                conflict("다른 곳에서 이 잡이 변경되었습니다. 새로고침한 뒤 다시 시도하세요.")
            self.repo.release_gate(job_id)  # ② ocr_jobs — 게이트 해제
            self.repo.update_pair(pair_id, fields)  # ③ training_pairs — reviewed_at → NULL
            updated = self.repo.find_pair(pair_id)
            new_token = self.repo.get_job_token(job_id)
        return {
            "id": int(updated["id"]),
            "crop_ref": updated["crop_ref"],
            "job_id": job_id,
            "row_index": int(updated["row_index"]),
            "draft_label": updated["draft_label"],
            "final_label": updated["final_label"],
            "canonical_label": updated["canonical_label"],
            "supply": updated["supply"],
            "status": updated["status"],
            "exclusion_reason": updated["exclusion_reason"],
            "reviewed_at": updated["reviewed_at"],
            # 상수 False — 해제 규칙이 무조건이라(§3.4) 방금 0을 쓴 값을 되읽는 것과 결과가
            # 같다. 규칙이 조건부로 바뀌면 이 상수부터 깨져야 한다(재조회로 되돌릴 것).
            "job_curation_reviewed": False,
            # 연속 편집을 이어갈 수 있도록 갱신된 토큰을 돌려준다(spec §12).
            "job_token": new_token,
        }

    def mark_reviewed(self, job_id: int, job_token: str) -> dict:
        """잡을 검수완료로 표시한다. 없으면 404. 같은 세대·done 상태에서만 멱등.

        그 잡의 included 정식 라벨은 자동완성 사전에 등록된다(ADR 0008 단방향 정합).

        **낙관적 잠금(spec §12).** 이 경로는 게이트를 닫고 미검수 쌍 전량에 reviewed_at을
        찍으므로, 재처리 이전에 열어둔 화면이 그대로 보내면 새로 생긴 미결 쌍이 사람 눈에
        닿기 전에 검수 큐에서 사라지고 --reembed-job 가드까지 통과한다(§7 · §11-1). 세대가
        다르면 409로 거부한다 — PATCH만 막으면 방어가 반쪽이다.

        상태가 done이 아니면 역시 409다. 재처리 큐에 든 잡의 검수 완료는 워커가 곧 덮어쓸
        사실이라, reprocess 엔드포인트와 같은 규칙을 쓴다.

        Args:
            job_id: 검수 완료로 표시할 OCR 잡 id.
            job_token: 클라이언트가 잡 상세에서 받아 되보낸 세대 토큰.

        Raises:
            AppError: 잡이 없으면 404, 상태가 done이 아니거나 토큰이 다르면 409.
        """
        with self._transaction():
            # ①→③ 순서가 락 순서다. ocr_jobs(부모) → training_pairs(자식).
            job = self.repo.find_job_for_update(job_id)  # ① ocr_jobs — 행잠금 + 존재 확인
            if job is None:
                not_found("OCR 잡을 찾을 수 없습니다.")
            if job["status"] != "done":
                conflict("아직 처리 중인 잡입니다. 처리가 끝난 뒤 다시 시도하세요.")
            if self.repo.get_job_token(job_id) != job_token:  # ② 세대 대조
                conflict("다른 곳에서 이 잡이 변경되었습니다. 새로고침한 뒤 다시 시도하세요.")
            self.repo.mark_reviewed(job_id)  # ③ ocr_jobs + training_pairs
            # dedup은 정규화 후에 한다 — 원본 기준이면 "휠"과 " 휠 "가 각각 살아남아
            # 같은 ensure_exists("휠")를 두 번 발행한다(락 위생 목적이 무너진다).
            labels = (_normalize_label(x) for x in self.repo.list_included_labels(job_id))
            for label in dict.fromkeys(labels):
                self._register_label(label)
        return {"job_id": job_id, "curation_reviewed": True}

    def request_reprocess(self, job_id: int) -> dict:
        """확정 완료된 잡을 현재 엔진으로 다시 판정하도록 큐에 넣는다. 없으면 404.

        status만 전이하고 result_json은 건드리지 않는다 — 재처리 판별의 근거이자 실패 시
        롤백 대상이다(spec §10). 크롭 갱신·라벨 승계는 ml-worker가 한 트랜잭션으로 한다
        (ADR 0010) — backend는 잡을 다시 큐에 넣는 것만 한다.

        배치는 런북에서 잡 id 목록으로 이 엔드포인트를 반복 호출한다 — 배치 전용
        엔드포인트를 만들지 않는다(부분 실패 시 어디까지 걸렸는지가 오히려 명확하다).

        Args:
            job_id: 재처리할 OCR 잡 id.

        Returns:
            {"job_id", "status"} — 전이 후 상태.

        Raises:
            AppError: 잡이 없으면 404, done이 아니면 409(중복 요청 차단).
        """
        with self._transaction():
            job = self.repo.find_job_for_update(job_id)
            if job is None:
                not_found("OCR 잡을 찾을 수 없습니다.")
            if job["status"] != "done":
                conflict("재처리할 수 없는 잡입니다(추론이 끝난 잡만 다시 처리할 수 있습니다).")
            self.repo.requeue_for_reprocess(job_id)
        return {"job_id": job_id, "status": "pending"}

    def original_image(self, job_id: int) -> str:
        """원본 업로드 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self.repo.get_image_path(job_id)
        if not path or not Path(path).is_file():
            not_found("원본 이미지가 없습니다.")
        return path

    def warped_image(self, job_id: int) -> str:
        """워프된 전표 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = crop_dir(job_id) / "warped.png"
        if not path.is_file():
            not_found("워프 이미지가 없습니다.")
        return str(path)

    def _register_label(self, label: str | None) -> None:
        """정규화한 정식 라벨을 자동완성 사전에 등록한다(빈 값은 건너뛴다).

        호출자는 mark_reviewed 하나뿐이다(spec §3.3으로 patch_pair 경로가 제거됨).
        list_included_labels의 SQL이 NULL을 걸러 실제로는 None이 도달하지 않지만,
        canonical_label이 nullable이므로 타입 경계는 _normalize_label이 흡수한 채로 둔다.
        게다가 유일 호출자 mark_reviewed가 이미 _normalize_label을 적용한 정규화된
        str만 넘기므로, None은 SQL과 호출자 양쪽에서 두 겹으로 도달 불가하다 — SQL만
        바꾼다고 None이 다시 도달하는 건 아니다.
        """
        if self.item_repo is None:
            return
        name = _normalize_label(label)
        if name:
            self.item_repo.ensure_exists(name)
