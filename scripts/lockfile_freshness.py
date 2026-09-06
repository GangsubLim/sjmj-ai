"""lockfile 신규·출처변경 의존성의 registry publish 신선도 게이트.

설치 이전에 실행되는 것이 존재 이유이므로 stdlib만 사용하고 Python 3.9 호환을 유지한다
(macmini self-hosted runner의 시스템 python3가 3.9.6).

같은 제약 때문에 **단일 파일 유지가 의도**다 — 이 스크립트는 의존성 설치는 물론 패키지 임포트
경로 구성도 없이 `python3 scripts/lockfile_freshness.py` 한 번으로 실행돼야 하므로, 길이가
늘어도 헬퍼 모듈로 쪼개지 않는다.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

MAX_AGE_DAYS = 7
BACKEND_UV_LOCK_PATH = "apps/invoice-ocr/backend/uv.lock"
ML_UV_LOCK_PATH = "apps/invoice-ocr/ml/uv.lock"
NPM_LOCK_PATH = "apps/invoice-ocr/frontend/package-lock.json"
ALLOWLIST_PATH = "scripts/lockfile_allowlist.json"

PYPI_REGISTRY = "https://pypi.org/simple"
NPM_REGISTRY_PREFIX = "https://registry.npmjs.org/"

REGISTRY_SOURCE = "registry"
LOCAL_SOURCE = "local"
UNKNOWN_SOURCE = "unknown"

REASON_EXOTIC = "exotic"
REASON_UNVERIFIABLE = "unverifiable"
REASON_FRESH = "fresh"

_UV_SOURCE_RE = re.compile(r'^\{\s*(?P<kind>[a-z-]+)\s*=\s*"(?P<value>[^"]*)"')

# 이름·버전은 조회 URL에 삽입되므로 화이트리스트로만 통과시킨다. `/`만 인코딩하는 방식은
# `#`(fragment)·`?`(query)·`..`(traversal)를 그대로 흘려 **다른 패키지의 메타데이터**를
# 조회하게 만든다(예: "react#x"가 react의 publish 시각으로 판정돼 게이트를 통과).
# npm의 대문자 금지는 **신규 등록 이름**에만 적용된다 — 2017년 이전 공개 패키지(`JSONStream`
# 등)는 그대로 살아 있고 transitive로 유입될 수 있다. 이 검증은 파서 층이라 allowlist보다 먼저
# 돌아 면제로 풀 수 없으므로, 거부하면 CI·배포가 스크립트 수정 전까지 영구 차단된다. 대문자는
# 경로 세그먼트에서 URL-safe라 허용해도 아래 traversal 방어가 약해지지 않는다.
_NPM_NAME_RE = re.compile(
    r"^(@[A-Za-z0-9-~][A-Za-z0-9-._~]*/)?[A-Za-z0-9-~][A-Za-z0-9-._~]*$"
)
_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")  # PEP 508
# 첫 글자를 영숫자로 못박아 version 축의 traversal도 닫는다. `quote`는 `.`을 인코딩하지 않으므로
# `.`·`..`만으로 이뤄진 version은 조회 URL에 경로 세그먼트로 살아남고(`/pypi/<name>/./json`),
# 경로를 정규화하는 서버·CDN에서 **핀된 버전이 아닌 릴리스**의 publish 시각으로 판정된다.
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")

_NAME_PATTERNS = {"npm": _NPM_NAME_RE, "pypi": _PYPI_NAME_RE}


class LockfileFormatError(Exception):
    """lockfile을 신뢰할 수 없는 형태로 판단했을 때 발생한다(fail-closed)."""


def _check_identifiers(
    ecosystem: str,
    name: str,
    version: str,
    error: type[Exception],
    *,
    allow_empty_version: bool = False,
) -> None:
    """이름·버전을 화이트리스트 문자셋으로 검증한다(파서 층·조회 직전 공용).

    Args:
        ecosystem: `npm` 또는 `pypi`.
        name: 패키지 이름.
        version: 버전 문자열.
        error: 위반 시 raise할 예외 클래스 — 층마다 다르다(파서는 lockfile 형식 오류,
            조회는 registry 조회 오류).
        allow_empty_version: 빈 version을 통과시킬지. 파서 층은 True다 — npm `link: true`
            entry에는 version이 없고 registry 조회 대상도 아니다.

    Raises:
        Exception: `error`로 전달된 클래스. 알 수 없는 ecosystem이거나 문자셋 위반일 때.
    """
    pattern = _NAME_PATTERNS.get(ecosystem)
    if pattern is None:
        raise error(f"알 수 없는 ecosystem: {ecosystem!r}")
    if not pattern.match(name):
        raise error(f"{ecosystem} 패키지 이름 형식 위반: {name!r}")
    if version == "" and allow_empty_version:
        return
    if not _VERSION_RE.match(version):
        raise error(f"{ecosystem} 패키지 version 형식 위반: {name}@{version!r}")


def _unquote(raw: str) -> str:
    """`key = "value"`의 우변에서 따옴표를 제거한다.

    Args:
        raw: `=` 우변 문자열.

    Returns:
        따옴표를 제거한 값.
    """
    return raw.strip().strip('"')


def _normalize_uv_source(raw: str) -> str:
    """uv.lock의 `source = { ... }` 인라인 테이블을 비교 가능한 문자열로 정규화한다.

    Args:
        raw: `source = ` 우변 원문.

    Returns:
        `registry` / `local` / `git:<url>` / `path:<경로>` / `url:<url>` / `unknown`.
    """
    matched = _UV_SOURCE_RE.match(raw.strip())
    if matched is None:
        return UNKNOWN_SOURCE
    kind = matched.group("kind")
    value = matched.group("value")
    if kind == "registry":
        # 사설 인덱스로 바뀐 경우를 registry로 오인하지 않는다(fail-closed).
        return REGISTRY_SOURCE if value == PYPI_REGISTRY else "url:" + value
    if kind in ("virtual", "editable"):
        # spec §6 규칙 2의 확정 결정 — `virtual`·`editable`(npm은 `link: true`)은 값과 무관하게
        # `local`로 보고 판정에서 제외한다. 바로 아래 `directory`/`path`가 `path:`로 차단되는
        # 것과 비대칭이지만 의도된 것이다. 리뷰에서 재차 제기되지 않도록 여기 남긴다.
        return LOCAL_SOURCE
    if kind == "git":
        return "git:" + value
    if kind in ("directory", "path"):
        return "path:" + value
    if kind == "url":
        return "url:" + value
    return UNKNOWN_SOURCE


def _uv_array_table_name(line: str) -> str | None:
    """컬럼0 테이블 헤더 줄이 `[[...]]` 배열 테이블이면 안쪽 이름을 돌려준다.

    TOML은 테이블 헤더 뒤의 인라인 주석(`[[package]] # x`)과 괄호 안쪽 공백(`[[ package ]]`)을
    모두 허용한다. 정확 문자열 비교로 판정하면 그런 블록이 `[[package]]`로 인식되지 않아
    name/version/source 줄이 전부 버려지고, 예외도 finding도 없이 패키지가 사라진다
    (uv는 정상 설치하는데 게이트만 못 보는 false green).

    Args:
        line: 컬럼0에서 `[`로 시작하는 원문 줄.

    Returns:
        배열 테이블 이름. 단일 대괄호 테이블(`[package.metadata]` 등)이면 None.

    Raises:
        LockfileFormatError: `[[`로 시작하는데 `]]`로 닫히지 않거나, 닫은 뒤에 주석이 아닌
            내용이 남아 있을 때(형태를 신뢰할 수 없으므로 fail-closed).
    """
    stripped = line.strip()
    if not stripped.startswith("[["):
        return None
    end = stripped.find("]]")
    if end < 0:
        raise LockfileFormatError(f"닫히지 않은 배열 테이블 헤더: {line!r}")
    trailing = stripped[end + 2 :].strip()
    if trailing and not trailing.startswith("#"):
        raise LockfileFormatError(f"해석할 수 없는 배열 테이블 헤더: {line!r}")
    return stripped[2:end].strip()


def _uv_header_version(lines: list[str]) -> str | None:
    """uv.lock 첫 테이블 이전 헤더에서 `version` 값을 읽는다.

    Args:
        lines: lockfile 전체 줄.

    Returns:
        헤더 version 문자열. 없으면 None.
    """
    for line in lines:
        if line.startswith("["):
            return None
        if line.startswith("version = "):
            return _unquote(line.split("=", 1)[1])
    return None


def parse_uv_lock(text: str) -> dict[str, str]:
    """uv.lock을 `{"pypi:<name>@<version>": <정규화 출처>}`로 파싱한다.

    `[[package]]` 테이블 경계를 추적하는 stateful 파서다. `[package.metadata]`의 requires-dist
    항목처럼 들여쓰기된 `source = ` 줄을 패키지로 오인하지 않는다.

    Args:
        text: uv.lock 전문.

    Returns:
        키→출처 매핑.

    Raises:
        LockfileFormatError: 헤더 `version`이 1이 아니거나, name/version이 없는 패키지가 있거나,
            name/version이 화이트리스트 문자셋을 벗어나거나(`@`·URL 특수문자·경로 traversal),
            `package`가 아닌 배열 테이블(`[[...]]`)을 만났거나, 검사 대상 패키지가 하나도
            없을 때(0건 통과를 fail-closed로 막는다).
    """
    lines = text.splitlines()
    header_version = _uv_header_version(lines)
    if header_version != "1":
        raise LockfileFormatError(
            f"지원하지 않는 uv.lock version={header_version!r} — 파서를 갱신할 때까지 fail-closed"
        )

    packages: dict[str, str] = {}
    current: dict[str, str] = {}
    in_package = False

    def flush() -> None:
        if not in_package or not current:
            return
        name = current.get("name")
        version = current.get("version")
        if name is None or version is None:
            raise LockfileFormatError(
                f"name/version이 없는 [[package]] 블록: {current!r}"
            )
        # 화이트리스트는 "@"(키 경계 모호)와 URL 특수문자·경로 traversal을 함께 막는다.
        _check_identifiers(
            "pypi", name, version, LockfileFormatError, allow_empty_version=True
        )
        packages["pypi:" + name + "@" + version] = current.get("source", UNKNOWN_SOURCE)

    for line in lines:
        if line.startswith("["):
            flush()
            table = _uv_array_table_name(line)
            if table is not None and table != "package":
                # 모르는 배열 테이블을 조용히 건너뛰면 그 안의 패키지가 검사에서 사라진다.
                raise LockfileFormatError(f"알 수 없는 배열 테이블: {table!r}")
            in_package = table == "package"
            current = {}
            continue
        if not in_package:
            continue
        if line.startswith("name = "):
            current["name"] = _unquote(line.split("=", 1)[1])
        elif line.startswith("version = "):
            current["version"] = _unquote(line.split("=", 1)[1])
        elif line.startswith("source = "):
            current["source"] = _normalize_uv_source(line.split("=", 1)[1])
    flush()
    if not packages:
        raise LockfileFormatError("uv.lock에서 검사 대상 패키지를 찾지 못했다")
    return packages


def _normalize_npm_source(name: str, entry: dict) -> str:
    """package-lock.json entry의 출처를 비교 가능한 문자열로 정규화한다.

    registry 판정은 호스트만이 아니라 **tarball 경로가 그 패키지의 그 버전인지**까지 본다.
    호스트 접두만 보면 `node_modules/left-pad`(2018 publish) entry에 다른 패키지의
    tarball(`.../evil-new/-/evil-new-1.0.0.tgz`)을 넣는 것만으로 특수문자 없이 게이트를
    통과한다 — 늙은 left-pad의 publish 시각으로 판정되기 때문이다. 이름만 맞추고 **버전만**
    바꾸는 경로(`.../left-pad/-/left-pad-9.9.9.tgz`를 `version = "1.3.0"` entry에)도 같은
    우회다. 설치는 `resolved`+`integrity`가 결정하는데 신선도는 lockfile의 `version`으로
    판정하므로, 출처가 `registry`로 남으면 diff에도 안 잡히고 늙은 시각으로 통과한다.
    그래서 정본 형태와의 **정확 일치**를 요구한다. 실 lockfile의 registry 항목 412건은 전부
    이 형태라 오탐 비용이 없다(스코프 패키지는 tarball 파일명에서만 스코프가 빠진다:
    `@babel/code-frame/-/code-frame-7.26.2.tgz`).

    Args:
        name: lockfile 경로에서 얻은 패키지 이름.
        entry: `packages` 하위 entry 객체.

    Returns:
        `registry` / `local` / `git:<url>` / `url:<url>` / `unknown`.
    """
    if entry.get("link"):
        return LOCAL_SOURCE
    resolved = entry.get("resolved")
    if not resolved:
        # workspace 링크는 link:true로 표기된다. resolved 없는 node_modules entry는
        # 형태를 신뢰할 수 없으므로 차단 방향으로 둔다.
        return UNKNOWN_SOURCE
    if resolved.startswith("git+"):
        return "git:" + resolved[len("git+") :]
    if resolved.startswith("git://"):
        return "git:" + resolved
    if resolved.startswith(NPM_REGISTRY_PREFIX):
        unscoped = name.rsplit("/", 1)[-1]
        version = entry.get("version", "")
        canonical = f"{NPM_REGISTRY_PREFIX}{name}/-/{unscoped}-{version}.tgz"
        if resolved == canonical:
            return REGISTRY_SOURCE
        # registry 호스트지만 다른 패키지·다른 버전의 경로 → 신뢰할 수 없다(→ exotic 차단).
        return UNKNOWN_SOURCE
    return "url:" + resolved


_NPM_SUPPORTED_LOCKFILE_VERSIONS = (2, 3)


def _merge_npm_source(existing: str, incoming: str) -> str:
    """같은 `name@version` 키에 중복 등록된 두 출처를 병합한다(비-registry 우선, fail-closed).

    서로 다른 설치 경로가 우연히 같은 name/version으로 겹칠 때(npm workspaces·중첩
    node_modules에서 흔함), registry가 아닌 출처가 있으면 그 흔적을 잃지 않기 위해
    항상 비-registry 쪽을 남긴다. 동일 출처의 중복은 그대로 유지한다(무해한 병합).
    서로 다른 비-registry 출처 둘이 부딪히면(예: 기존 url: 출처에 새 git+ 경로가 추가)
    나중 값을 조용히 버리지 않고 `unknown`으로 강등한다 — unknown은 registry가 아니므로
    하위 classify에서 exotic(차단)으로 잡히고, 값이 바뀌므로 diff 층에서도 변경으로 잡힌다.

    Args:
        existing: 이미 기록된 출처.
        incoming: 새로 들어온 출처.

    Returns:
        병합된 출처. 둘 다 비-registry면서 서로 다르면 `unknown`.
    """
    if existing == incoming:
        return existing
    if existing == REGISTRY_SOURCE:
        return incoming
    if incoming == REGISTRY_SOURCE:
        return existing
    return UNKNOWN_SOURCE


def parse_npm_lock(text: str) -> dict[str, str]:
    """package-lock.json(v2/v3)을 `{"npm:<name>@<version>": <정규화 출처>}`로 파싱한다.

    경로에 `node_modules/`가 포함된 entry를 대상으로 하고, 마지막 `node_modules/` 이후를
    이름으로 취한다 — `packages/<workspace>/node_modules/<dep>` 같은 workspace 중첩 경로도
    prefix 매칭과 달리 놓치지 않는다. 같은 키가 서로 다른 경로에서 다른 출처로 중복되면
    `_merge_npm_source`로 병합한다(비-registry 우선). 단 `resolved`가 없는 `inBundle`
    entry는 부모 tarball의 내용물이라 삽입하지 않는다(spec §7.2).

    Args:
        text: package-lock.json 전문.

    Returns:
        키→출처 매핑.

    Raises:
        LockfileFormatError: JSON이 깨졌거나, `lockfileVersion`이 지원 범위(2·3) 밖이거나,
            `packages` 객체가 없거나, name/version이 화이트리스트 문자셋을 벗어나거나,
            검사 대상 패키지가 하나도 없을 때(0건 통과를 fail-closed로 막는다).
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LockfileFormatError(f"package-lock.json 파싱 실패: {exc}") from exc

    lockfile_version = data.get("lockfileVersion")
    if lockfile_version not in _NPM_SUPPORTED_LOCKFILE_VERSIONS:
        raise LockfileFormatError(
            f"지원하지 않는 package-lock.json lockfileVersion={lockfile_version!r} — "
            "파서를 갱신할 때까지 fail-closed"
        )

    entries = data.get("packages")
    if not isinstance(entries, dict):
        raise LockfileFormatError("package-lock.json에 packages 객체가 없다")

    packages: dict[str, str] = {}
    for path, entry in entries.items():
        if "node_modules/" not in path:
            continue
        name = path.rsplit("node_modules/", 1)[1]
        if not name:
            # "node_modules/"로 끝나는 경로는 이름이 비어 npm:@<version> 유령 키가 된다.
            continue
        if entry.get("inBundle") is True and "resolved" not in entry:
            # npm은 번들 의존성을 별도 fetch하지 않고 부모 tarball 내용물로 설치하므로
            # install-time 공급망 벡터는 부모(정상 검사 대상)에 귀속된다.
            # 출처 값 `bundled`를 두는 대신 삽입 자체를 건너뛰는 이유 — 파서는 설치 경로별
            # entry를 name@version 키로 합치고 `_merge_npm_source`가 비-registry를 남기므로,
            # 값을 두면 같은 키의 registry 설치가 덮여 검사에서 빠진다(실 lockfile의
            # tslib@2.8.1이 번들 내부와 최상위 양쪽에 존재해 이 충돌이 실재).
            # `inBundle` 없이 `resolved`만 없는 entry는 기존대로 unknown → exotic 차단이다.
            continue
        version = entry.get("version", "")
        # 화이트리스트는 "@"(키 경계 모호)와 URL 특수문자·경로 traversal을 함께 막는다.
        _check_identifiers(
            "npm", name, version, LockfileFormatError, allow_empty_version=True
        )
        key = "npm:" + name + "@" + version
        source = _normalize_npm_source(name, entry)
        packages[key] = (
            _merge_npm_source(packages[key], source) if key in packages else source
        )

    if not packages:
        raise LockfileFormatError(
            "package-lock.json에서 검사 대상 패키지를 찾지 못했다"
        )
    return packages


# classify의 차단 강도 — local은 무조건 통과, registry는 조건부 차단(fresh·unverifiable),
# 그 외(git·path·url·unknown)는 무조건 exotic 차단이라 가장 무겁다.
_HEAD_SOURCE_SEVERITY = {LOCAL_SOURCE: 0, REGISTRY_SOURCE: 1}


def _head_source_severity(source: str) -> int:
    """정규화된 head 출처의 차단 강도를 정수로 매긴다(클수록 무겁다).

    Args:
        source: 정규화된 head 출처.

    Returns:
        local 0, registry 1, 그 외 2.
    """
    return _HEAD_SOURCE_SEVERITY.get(source, 2)


def merge_changed_packages(
    target: dict[str, tuple[str | None, str]],
    incoming: dict[str, tuple[str | None, str]],
) -> dict[str, tuple[str | None, str]]:
    """축이 다른 lockfile의 검사 대상을 fail-closed로 누적한다.

    ecosystem 축이 둘 이상이면(backend·ml의 uv.lock) 같은 `pypi:<name>@<version>` 키가 서로
    다른 출처로 부딪힌다. `dict.update`로 덮으면 뒤 축의 판정이 앞 축의 차단 판정을 지워
    게이트가 조용히 통과한다(fail-open) — backend의 registry→git 스왑이 ml의 같은 키 registry
    추가에 덮여 종료코드 0이 나오는 경로가 실재한다. 그래서 판정이 더 무거운 쪽을 남긴다.
    강도가 같으면 먼저 들어온 쪽을 남긴다 — 차단 여부는 어느 쪽을 남겨도 같고 출력만
    결정적으로 고정된다.

    Args:
        target: 지금까지 누적한 키→(base 출처, head 출처). 제자리에서 갱신된다.
        incoming: 새 축의 검사 대상.

    Returns:
        갱신된 `target`.
    """
    for key, entry in incoming.items():
        existing = target.get(key)
        existing_rank = -1 if existing is None else _head_source_severity(existing[1])
        if _head_source_severity(entry[1]) > existing_rank:
            target[key] = entry
    return target


# 검사 대상 lockfile과 파서의 결선 — main과 테스트가 공유하는 단일 정본이다.
# 파서 함수를 참조하므로 두 파서 정의 뒤에 둔다.
LOCKFILES = (
    (BACKEND_UV_LOCK_PATH, parse_uv_lock),
    (ML_UV_LOCK_PATH, parse_uv_lock),
    (NPM_LOCK_PATH, parse_npm_lock),
)


def diff_changed_packages(
    base: dict[str, str], head: dict[str, str]
) -> dict[str, tuple[str | None, str]]:
    """검사 대상(신규 키 + 출처 변경 키)을 추린다.

    출처 변경을 함께 보지 않으면 registry→git 스왑이 게이트를 통과한다(키가 동일하기 때문).

    Args:
        base: base 커밋의 키→출처 매핑.
        head: head 작업트리의 키→출처 매핑.

    Returns:
        키 → (base 출처 또는 None, head 출처). base에만 있는 키는 포함하지 않는다.
    """
    changed: dict[str, tuple[str | None, str]] = {}
    for key, head_source in head.items():
        base_source = base.get(key)
        # `base_source is None`은 `!=`만으로도 항상 참이라 현재는 죽은 절이다. 그래도 남겨둔다 —
        # 파서가 훗날 base 쪽에서 실제 None을 반환하도록 바뀌어도(예: 파싱 실패를 예외 대신 None으로
        # 표현) 검사 대상에서 조용히 빠지지 않고 fail-closed로 걸리게 하려는 방어선이다.
        if base_source is None or base_source != head_source:
            changed[key] = (base_source, head_source)
    return changed


class Finding(NamedTuple):
    """차단 사유 1건."""

    key: str
    reason: str
    base_source: str | None
    head_source: str
    published_at: datetime | None
    age_days: float | None


def split_key(key: str) -> tuple[str, str, str]:
    """`<eco>:<name>@<version>` 키를 세 조각으로 나눈다.

    스코프 npm 패키지(`npm:@scope/name@1.0.0`)를 위해 버전은 마지막 `@` 기준으로 분리한다.

    Args:
        key: 네임스페이스 키.

    Returns:
        (ecosystem, name, version).
    """
    ecosystem, rest = key.split(":", 1)
    name, _, version = rest.rpartition("@")
    return ecosystem, name, version


def is_allowlisted(key: str, allowlist: dict[str, list[str]]) -> bool:
    """키가 해당 ecosystem의 allowlist에 걸리는지 판단한다.

    ecosystem 배열을 분리해서 보므로 npm 항목이 PyPI 동명 패키지를 면제하지 않는다. name/version
    분리는 `split_key`를 그대로 재사용한다 — classify()의 registry 조회와 다른 경로로 따로
    분리하면 두 분리 로직이 훗날 어긋날 여지가 생긴다.

    Args:
        key: 네임스페이스 키.
        allowlist: ecosystem → 면제 항목 배열.

    Returns:
        면제 대상이면 True.
    """
    ecosystem, name, version = split_key(key)
    entries = allowlist.get(ecosystem) or []
    return name in entries or f"{name}@{version}" in entries


def load_allowlist(path: Path) -> dict[str, list[str]]:
    """Allowlist JSON을 읽는다. 파일이 없으면 빈 allowlist로 취급한다.

    최상위가 dict가 아니거나, ecosystem 값이 배열이 아니거나, 배열 항목이 비어있지 않은
    문자열이 아니면 전부 fail-closed로 거부한다 — 느슨한 타입 강제(`list(items)`)는 버전 핀
    의도(`{"npm": {"foo": "1.0.0"}}`)를 이름 전체 면제로 확대하는 등 면제 범위를 조용히
    넓힐 수 있다.

    Args:
        path: allowlist 파일 경로.

    Returns:
        ecosystem → 면제 항목 배열.

    Raises:
        LockfileFormatError: JSON이 깨졌거나, 최상위가 객체가 아니거나, ecosystem 값이
            배열이 아니거나, 배열 항목이 비어있지 않은 문자열이 아닐 때.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LockfileFormatError(f"allowlist 파싱 실패: {exc}") from exc
    if not isinstance(payload, dict):
        raise LockfileFormatError(
            f"allowlist 최상위는 객체여야 한다: {type(payload).__name__}"
        )
    allowlist: dict[str, list[str]] = {}
    for ecosystem, items in payload.items():
        if not isinstance(items, list):
            raise LockfileFormatError(
                f"allowlist[{ecosystem!r}]는 배열이어야 한다: {type(items).__name__}"
            )
        for item in items:
            if not isinstance(item, str) or not item:
                raise LockfileFormatError(
                    f"allowlist[{ecosystem!r}] 항목은 비어있지 않은 문자열이어야 한다: {item!r}"
                )
        allowlist[str(ecosystem)] = list(items)
    return allowlist


def classify(
    changed: dict[str, tuple[str | None, str]],
    allowlist: dict[str, list[str]],
    fetch_publish_time: Callable[[str, str, str], datetime | None],
    now: datetime,
) -> list[Finding]:
    """검사 대상에 spec §6의 판정 규칙을 순서대로 적용한다.

    Args:
        changed: `diff_changed_packages` 결과.
        allowlist: ecosystem → 면제 항목 배열.
        fetch_publish_time: (ecosystem, name, version) → publish 시각(UTC aware) 또는 None.
        now: 기준 시각(UTC aware).

    Returns:
        차단 사유 목록. 비어 있으면 통과.

    Raises:
        LockfileFormatError: `fetch_publish_time`이 naive datetime(tzinfo 없음)을 반환했을 때.
            시각 비교는 전부 UTC aware여야 한다(spec §8).
    """
    findings: list[Finding] = []
    for key in sorted(changed):
        base_source, head_source = changed[key]
        if is_allowlisted(key, allowlist):
            continue
        if head_source == LOCAL_SOURCE:
            continue
        if head_source != REGISTRY_SOURCE:
            findings.append(
                Finding(key, REASON_EXOTIC, base_source, head_source, None, None)
            )
            continue
        ecosystem, name, version = split_key(key)
        published_at = fetch_publish_time(ecosystem, name, version)
        if published_at is None:
            findings.append(
                Finding(key, REASON_UNVERIFIABLE, base_source, head_source, None, None)
            )
            continue
        if published_at.tzinfo is None:
            raise LockfileFormatError(
                f"{key}: fetch_publish_time이 naive datetime을 반환했다 — UTC aware가 필요하다"
            )
        age = now - published_at
        if age < timedelta(days=MAX_AGE_DAYS):
            findings.append(
                Finding(
                    key,
                    REASON_FRESH,
                    base_source,
                    head_source,
                    published_at,
                    age / timedelta(days=1),
                )
            )
    return findings


def render_findings(findings: list[Finding]) -> str:
    """차단 사유를 사람이 읽을 수 있는 여러 줄 문자열로 만든다.

    Args:
        findings: 차단 사유 목록.

    Returns:
        출력용 문자열.
    """
    lines = []
    for finding in findings:
        detail = f"  {finding.key}  reason={finding.reason}"
        if (
            finding.base_source is not None
            and finding.base_source != finding.head_source
        ):
            detail += f"  출처변경: {finding.base_source} -> {finding.head_source}"
        else:
            detail += f"  출처: {finding.head_source}"
        if finding.published_at is not None and finding.age_days is not None:
            detail += f"  published={finding.published_at.isoformat()} age={finding.age_days:.2f}d"
        lines.append(detail)
    lines.append("")
    lines.append(
        f"면제가 필요하면 {ALLOWLIST_PATH}의 해당 ecosystem 배열에 "
        '"<name>" 또는 "<name>@<version>"을 추가하고 커밋한다.'
    )
    lines.append(
        "이름 단위 면제(<name>)는 해당 패키지의 모든 버전에 대해 출처 변경(exotic) 차단까지 "
        "함께 해제한다."
    )
    lines.append("allowlist 항목은 lockfile에 나타난 이름과 정확히 일치해야 한다.")
    return "\n".join(lines)


def _repo_root() -> Path:
    """레포 루트 경로를 반환한다.

    Returns:
        `scripts/`의 부모 디렉토리.
    """
    return Path(__file__).resolve().parent.parent


FETCH_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 10
RETRY_BACKOFF_SECONDS = 1.0
# 네트워크 사정으로 다음 시도가 성공할 수 있는 상태코드만 재시도한다. 400/401/403처럼 요청
# 자체가 거부된 경우는 3회를 채워도 결과가 같아 게이트 지연만 만든다.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
# npm packument는 패키지에 따라 수십 MB까지 커진다 — 무제한 read를 하지 않는다.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
USER_AGENT = "sjmj-ai-lockfile-freshness/1.0"
NPM_REGISTRY_API = "https://registry.npmjs.org/"
PYPI_JSON_API = "https://pypi.org/pypi/"

# 캐시 입도는 **조회 URL 단위**다 — npm은 이름 단위(packument 1건), PyPI는 name+version
# 단위(버전별 엔드포인트)로 자연히 갈리고, ecosystem이 같은 이름을 써도 서로 섞이지 않는다.
_HTTP_CACHE: dict[str, dict | None] = {}


class RegistryLookupError(Exception):
    """registry 조회를 신뢰할 수 없을 때 발생한다(fail-closed)."""


def reset_http_cache() -> None:
    """프로세스 내 HTTP 응답 캐시를 비운다."""
    _HTTP_CACHE.clear()


def _read_json_payload(response: object) -> dict:
    """응답 본문을 상한 안에서 읽어 JSON 객체로 만든다.

    Args:
        response: `urlopen` 응답 객체.

    Returns:
        파싱된 JSON 객체.

    Raises:
        RegistryLookupError: 본문이 상한을 넘거나 최상위가 JSON 객체가 아닐 때.
    """
    raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RegistryLookupError(
            f"registry 응답이 너무 큽니다(> {MAX_RESPONSE_BYTES} bytes)"
        )
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RegistryLookupError(
            f"registry 응답이 JSON 객체가 아닙니다: {type(payload).__name__}"
        )
    return payload


def _http_get_json(url: str) -> dict | None:
    """Registry JSON을 조회한다. 404는 None, 그 외 실패는 최대 3회 시도 후 예외.

    Args:
        url: 조회 URL.

    Returns:
        파싱된 JSON 객체. 404면 None.

    Raises:
        RegistryLookupError: 재시도 가치 없는 상태코드를 받았거나, 3회 시도 후에도 응답을
            얻지 못했거나, 응답이 상한 초과·비-객체일 때.
    """
    if url in _HTTP_CACHE:
        return _HTTP_CACHE[url]
    last_error: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                payload = _read_json_payload(response)
            _HTTP_CACHE[url] = payload
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                _HTTP_CACHE[url] = None
                return None
            if exc.code not in RETRYABLE_STATUS_CODES:
                raise RegistryLookupError(
                    f"registry 조회 거부(HTTP {exc.code}): {url}"
                ) from exc
            last_error = exc
        # InvalidURL 등 http.client.HTTPException 계열은 URLError/OSError/ValueError 어디에도
        # 속하지 않아, 빠뜨리면 재시도 루프와 main의 예외 경계를 그대로 탈출한다.
        except (
            urllib.error.URLError,
            OSError,
            ValueError,
            http.client.HTTPException,
        ) as exc:
            last_error = exc
        if attempt + 1 < FETCH_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise RegistryLookupError(
        f"registry 조회 실패({FETCH_ATTEMPTS}회 시도): {url} — {last_error}"
    )


def _parse_iso8601(raw: str) -> datetime:
    """registry가 준 ISO-8601 문자열을 UTC aware datetime으로 만든다.

    Python 3.9의 `fromisoformat`은 `Z` 접미를 파싱하지 못하므로 먼저 치환한다.

    Args:
        raw: ISO-8601 문자열.

    Returns:
        UTC aware datetime.

    Raises:
        RegistryLookupError: 형식이 깨졌거나 tz 정보가 없을 때.
    """
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RegistryLookupError(f"publish 시각 형식 파손: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise RegistryLookupError(f"publish 시각에 tz 정보가 없다: {raw!r}")
    # UP017(datetime.UTC)은 Python 3.11+ 전용 — 이 스크립트는 3.9 호환이 계약이다.
    return parsed.astimezone(timezone.utc)  # noqa: UP017


def _npm_publish_time(name: str, version: str) -> datetime | None:
    """레지스트리 packument의 `time[<version>]`에서 npm publish 시각을 얻는다.

    Args:
        name: 패키지 이름(스코프 포함 가능).
        version: 버전 문자열.

    Returns:
        UTC aware publish 시각. 404이거나 해당 버전 메타가 없으면 None.

    Raises:
        RegistryLookupError: 조회에 실패했거나 응답 형태가 예상 밖일 때.
    """
    # 스코프는 `/`를 %2F로 접어 단일 경로 세그먼트로 만든다(registry 규약). `@`는 인코딩하지
    # 않는다 — %40으로 접으면 registry가 스코프 패키지를 찾지 못한다.
    payload = _http_get_json(NPM_REGISTRY_API + quote(name, safe="@"))
    if payload is None:
        return None
    times = payload.get("time")
    if times is None:
        return None
    if not isinstance(times, dict):
        raise RegistryLookupError(
            f"npm time 필드가 객체가 아닙니다: {name} ({type(times).__name__})"
        )
    raw = times.get(version)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RegistryLookupError(
            f"npm publish 시각이 문자열이 아닙니다: {name}@{version}"
        )
    return _parse_iso8601(raw)


def _pypi_publish_time(name: str, version: str) -> datetime | None:
    """PyPI 버전별 엔드포인트의 `urls[].upload_time_iso_8601`에서 publish 시각을 얻는다.

    여러 아티팩트가 있으면 **가장 최근** 업로드 시각을 쓴다. PyPI는 기존 release에 배포물을
    나중에 추가 업로드하는 것을 허용하므로, 최솟값을 쓰면 옛 버전에 얹은 새 wheel이 "늙은
    패키지"로 판정돼 신선도 게이트를 그대로 통과한다(fail-open).

    Args:
        name: 패키지 이름.
        version: 버전 문자열.

    Returns:
        UTC aware publish 시각. 404이거나 `urls`가 비어 있으면 None.

    Raises:
        RegistryLookupError: 조회에 실패했거나 응답 형태가 예상 밖일 때.
    """
    payload = _http_get_json(
        f"{PYPI_JSON_API}{quote(name, safe='')}/{quote(version, safe='')}/json"
    )
    if payload is None:
        return None
    urls = payload.get("urls")
    if urls is None:
        return None
    if not isinstance(urls, list):
        raise RegistryLookupError(
            f"PyPI urls 필드가 배열이 아닙니다: {name} ({type(urls).__name__})"
        )
    stamps = []
    for entry in urls:
        if not isinstance(entry, dict):
            raise RegistryLookupError(
                f"PyPI urls 항목이 객체가 아닙니다: {name}@{version}"
            )
        raw = entry.get("upload_time_iso_8601")
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise RegistryLookupError(
                f"PyPI publish 시각이 문자열이 아닙니다: {name}@{version}"
            )
        stamps.append(raw)
    if not stamps:
        return None
    return max(_parse_iso8601(stamp) for stamp in stamps)


def fetch_publish_time(ecosystem: str, name: str, version: str) -> datetime | None:
    """registry에서 해당 버전의 publish 시각을 얻는다.

    Args:
        ecosystem: `npm` 또는 `pypi`.
        name: 패키지 이름.
        version: 버전 문자열.

    Returns:
        UTC aware publish 시각. 404이거나 버전 메타가 없으면 None.

    Raises:
        RegistryLookupError: 알 수 없는 ecosystem이거나, 이름·버전 형식이 위반이거나,
            조회가 실패했거나, 응답 형태가 예상 밖일 때.
    """
    # 파서 층에서 이미 걸렀지만 조회 표면은 따로 호출될 수 있어 URL 조립 직전에도 막는다.
    _check_identifiers(ecosystem, name, version, RegistryLookupError)
    if ecosystem == "npm":
        return _npm_publish_time(name, version)
    return _pypi_publish_time(name, version)


def read_base_lockfile(root: Path, base_ref: str, path: str) -> str:
    """`git show <base>:<path>`로 base 커밋의 lockfile을 읽는다.

    Args:
        root: 레포 루트.
        base_ref: base ref 또는 SHA.
        path: 레포 루트 상대 lockfile 경로.

    Returns:
        lockfile 전문.

    Raises:
        LockfileFormatError: base에 해당 lockfile이 없을 때(전량 신규로 넘기지 않는다).
    """
    completed = subprocess.run(  # noqa: S603
        ["git", "show", f"{base_ref}:{path}"],  # noqa: S607
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",  # 로케일 의존 디코딩 금지(head 경로는 이미 명시적이다)
        check=False,
    )
    if completed.returncode != 0:
        raise LockfileFormatError(
            f"base lockfile을 읽을 수 없습니다: {base_ref}:{path} — {completed.stderr.strip()}"
        )
    return completed.stdout


def read_head_lockfile(root: Path, path: str) -> str:
    """작업트리의 lockfile을 읽는다.

    Args:
        root: 레포 루트.
        path: 레포 루트 상대 lockfile 경로.

    Returns:
        lockfile 전문.

    Raises:
        LockfileFormatError: 파일이 없거나 일반 파일이 아닐 때(디렉토리 포함 — `is_file()`
            가드가 `read_text`의 `IsADirectoryError`를 사전에 차단한다).
    """
    target = root / path
    if not target.is_file():
        raise LockfileFormatError(f"head lockfile이 없습니다: {path}")
    return target.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점.

    Args:
        argv: 인자 목록. None이면 `sys.argv[1:]`.

    Returns:
        통과 0, 차단·오류 1.
    """
    parser = argparse.ArgumentParser(
        description="lockfile 신규·출처변경 의존성의 registry publish 신선도를 검사한다."
    )
    parser.add_argument("--base", required=True, help="비교 기준 ref 또는 SHA")
    args = parser.parse_args(argv)

    if not args.base.strip():
        # 빈 문자열은 argparse의 required=True를 통과하고, `git show ":<path>"`는 인덱스를
        # 읽어 exit 0을 낸다 → base == head → 검사 대상 0건 → silent green(fail-open)이 된다.
        print("::error title=supply-chain gate::--base 값이 비어 있습니다(fail-closed)")
        return 1

    root = _repo_root()
    reset_http_cache()
    try:
        allowlist = load_allowlist(root / ALLOWLIST_PATH)
        changed: dict[str, tuple[str | None, str]] = {}
        for path, parse in LOCKFILES:
            base = parse(read_base_lockfile(root, args.base, path))
            head = parse(read_head_lockfile(root, path))
            merge_changed_packages(changed, diff_changed_packages(base, head))
        # UP017(datetime.UTC)은 Python 3.11+ 전용 — 이 스크립트는 3.9 호환이 계약이다.
        now = datetime.now(timezone.utc)  # noqa: UP017
        findings = classify(changed, allowlist, fetch_publish_time, now)
    except (LockfileFormatError, RegistryLookupError) as exc:
        # ::error 애노테이션은 한 줄만 렌더되므로 요약만 싣고, 다줄 상세(git stderr 등)는
        # 잘리지 않도록 별도 출력으로 남긴다.
        detail = str(exc)
        summary, _, rest = detail.partition("\n")
        print(f"::error title=supply-chain gate::{summary}")
        if rest:
            print(detail)
        return 1

    if findings:
        print(
            "::error title=supply-chain gate::"
            f"신규·출처변경 의존성 {len(findings)}건이 차단되었습니다 (기준 {MAX_AGE_DAYS}일)"
        )
        print(render_findings(findings))
        return 1
    print(f"lockfile freshness: 검사 대상 {len(changed)}건, 차단 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
