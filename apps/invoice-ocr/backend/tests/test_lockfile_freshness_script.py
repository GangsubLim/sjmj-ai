import json
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# 백엔드 tests 기준 레포 루트: tests → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    # 소스를 직접 compile한다 — importlib 로더는 scripts/__pycache__의 stale 바이트코드를
    # 재사용할 수 있고, 그러면 테스트가 현재 소스가 아닌 옛 구현을 검증하게 된다(실제로 발생).
    path = _REPO_ROOT / "scripts" / "lockfile_freshness.py"
    module = types.ModuleType("lockfile_freshness")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


mod = _load_module()


@pytest.fixture(autouse=True)
def _isolate_http_cache():
    mod.reset_http_cache()
    yield
    mod.reset_http_cache()


UV_LOCK_FIXTURE = """version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "sjmj-ai-invoice-ocr-backend"
version = "0.1.0"
source = { virtual = "." }

[package.metadata]
requires-dist = [
    { name = "opendartreader", version = "0.2.3", source = { registry = "https://pypi.org/simple" }, marker = "python_full_version < '3.13'" },
]
name = "ghost"
version = "9.9.9"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "aiofile"
version = "3.11.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "local-tool"
version = "0.2.0"
source = { editable = "../tools" }

[[package]]
name = "vendored"
version = "1.0.0"
source = { git = "https://github.com/x/vendored.git?rev=abc" }
"""


NPM_LOCK_FIXTURE = json.dumps(
    {
        "name": "frontend",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "frontend", "version": "0.1.0", "dependencies": {"react": "*"}},
            "node_modules/react": {
                "version": "19.0.0",
                "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz",
            },
            "node_modules/@babel/code-frame": {
                "version": "7.26.2",
                "resolved": "https://registry.npmjs.org/@babel/code-frame/-/code-frame-7.26.2.tgz",
            },
            "node_modules/@babel/code-frame/node_modules/js-tokens": {
                "version": "4.0.0",
                "resolved": "https://registry.npmjs.org/js-tokens/-/js-tokens-4.0.0.tgz",
            },
            "node_modules/linked-lib": {"resolved": "../linked-lib", "link": True},
            "node_modules/from-git": {
                "version": "1.2.3",
                "resolved": "git+https://github.com/x/from-git.git#abc",
            },
            "node_modules/from-url": {
                "version": "2.0.0",
                "resolved": "https://example.com/pkg/from-url-2.0.0.tgz",
            },
        },
    }
)


# --- 1: uv.lock registry 패키지의 name/version 추출 (+ 비-[[package]] 테이블 오인 방지) ---
def test_parse_uv_lock_extracts_registry_package():
    parsed = mod.parse_uv_lock(UV_LOCK_FIXTURE)
    assert parsed["pypi:aiofile@3.11.1"] == "registry"
    # (a) 정규식 search 기반 파서 방지 — requires-dist 인라인 테이블의 name/version/source를
    #     패키지로 오인하면 안 된다. (uv.lock:919-920이 이 형태이며, 실 lockfile에는 같은 이름의
    #     진짜 [[package]]가 :2464에 따로 있다.)
    assert "pypi:opendartreader@0.2.3" not in parsed
    # (b) stateless 파서 방지 — [package.metadata] 하위 컬럼0 키를 패키지로 오인하면 안 된다.
    #     이것이 파서를 stateful로 두는 진짜 이유다.
    assert "pypi:ghost@9.9.9" not in parsed
    assert parsed["pypi:vendored@1.0.0"] == "git:https://github.com/x/vendored.git?rev=abc"


# --- 2: virtual·editable → local ---
def test_parse_uv_lock_marks_virtual_and_editable_as_local():
    parsed = mod.parse_uv_lock(UV_LOCK_FIXTURE)
    assert parsed["pypi:sjmj-ai-invoice-ocr-backend@0.1.0"] == "local"
    assert parsed["pypi:local-tool@0.2.0"] == "local"


# --- 3: 헤더 version != 1 → fail-closed ---
def test_parse_uv_lock_rejects_unknown_lock_version():
    text = UV_LOCK_FIXTURE.replace("version = 1", "version = 2", 1)
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- SC-1: `[[package]]` 헤더는 인라인 주석·괄호 내부 공백을 허용하는 TOML 정본 형태다.
# 정확 문자열 비교로 판정하면 주석 한 글자만 붙여도 블록 전체가 조용히 사라져(예외도 finding도
# 없이) 악성 패키지가 게이트를 통과한다 — 최악의 false green. ---
_UV_COMMENTED_HEADER = """version = 1

[[package]]
name = "aiofile"
version = "3.11.1"
source = { registry = "https://pypi.org/simple" }

[[package]] # 무해해 보이는 주석
name = "evil"
version = "9.9.9"
source = { registry = "https://pypi.org/simple" }

[[ package ]]
name = "evil2"
version = "8.8.8"
source = { registry = "https://pypi.org/simple" }
"""


def test_parse_uv_lock_recognizes_package_header_with_comment_or_inner_space():
    parsed = mod.parse_uv_lock(_UV_COMMENTED_HEADER)
    assert parsed["pypi:evil@9.9.9"] == "registry"
    assert parsed["pypi:evil2@8.8.8"] == "registry"
    assert parsed["pypi:aiofile@3.11.1"] == "registry"


# --- SC-1: 인식하지 못하는 배열 테이블은 조용히 무시하지 않고 fail-closed로 막는다 ---
def test_parse_uv_lock_rejects_unknown_array_table():
    text = (
        'version = 1\n\n[[package]]\nname = "aiofile"\nversion = "3.11.1"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        '\n[[future-package]]\nname = "evil"\nversion = "9.9.9"\n'
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- SC-1: 닫히지 않은 배열 테이블 헤더도 fail-closed ---
def test_parse_uv_lock_rejects_unterminated_array_table_header():
    text = (
        'version = 1\n\n[[package]]\nname = "aiofile"\nversion = "3.11.1"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n[[package\nname = "evil"\n'
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- SC-3: 단일 세그먼트 `.`·`..` version은 quote()가 인코딩하지 않아 조회 URL의 경로
# 세그먼트로 살아남는다(`/pypi/<name>/./json`). 경로를 정규화하는 서버·CDN에서는 패키지 단위
# 엔드포인트로 접혀 **핀된 버전이 아닌 다른 릴리스**의 publish 시각으로 판정된다. ---
@pytest.mark.parametrize("version", [".", ".."])
def test_parse_uv_lock_rejects_dot_only_version(version):
    text = (
        f'version = 1\n\n[[package]]\nname = "evil"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    with pytest.raises(mod.LockfileFormatError, match="version"):
        mod.parse_uv_lock(text)


@pytest.mark.parametrize("version", [".", ".."])
def test_fetch_publish_time_rejects_dot_only_version_before_request(monkeypatch, version):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response({}))
    with pytest.raises(mod.RegistryLookupError, match="형식 위반"):
        mod.fetch_publish_time("pypi", "requests", version)
    assert calls == []


# --- SC-5: npm의 대문자 금지는 **신규 등록 이름**에만 적용되고 2017년 이전 공개 패키지
# (JSONStream 등)는 그대로 살아 있다. 파서 층 검증은 allowlist보다 먼저 돌아서 면제로 풀 수
# 없으므로, transitive로 하나만 유입돼도 CI·배포가 스크립트 수정 전까지 영구 차단된다.
# 대문자는 경로 세그먼트에서 URL-safe라 허용해도 게이트가 약해지지 않는다. ---
def test_parse_npm_lock_accepts_legacy_uppercase_package_name():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/JSONStream": {
                    "version": "1.3.5",
                    "resolved": "https://registry.npmjs.org/JSONStream/-/JSONStream-1.3.5.tgz",
                },
            },
        }
    )
    assert mod.parse_npm_lock(fixture)["npm:JSONStream@1.3.5"] == "registry"


def test_fetch_publish_time_accepts_legacy_uppercase_package_name(monkeypatch):
    payload = {"time": {"1.3.5": "2019-01-01T00:00:00.000Z"}}
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(payload))
    assert mod.fetch_publish_time("npm", "JSONStream", "1.3.5") == datetime(2019, 1, 1, tzinfo=UTC)
    assert calls[0]["url"] == "https://registry.npmjs.org/JSONStream"


# --- 4: package-lock v3의 node_modules/<name> 추출 ---
def test_parse_npm_lock_extracts_top_level_package():
    parsed = mod.parse_npm_lock(NPM_LOCK_FIXTURE)
    assert parsed["npm:react@19.0.0"] == "registry"


# --- 5: 중첩 node_modules/a/node_modules/b → b ---
def test_parse_npm_lock_uses_last_path_segment_for_nested_package():
    parsed = mod.parse_npm_lock(NPM_LOCK_FIXTURE)
    assert parsed["npm:js-tokens@4.0.0"] == "registry"


# --- 6: 스코프 node_modules/@scope/name → @scope/name ---
def test_parse_npm_lock_preserves_scoped_name():
    parsed = mod.parse_npm_lock(NPM_LOCK_FIXTURE)
    assert parsed["npm:@babel/code-frame@7.26.2"] == "registry"


# --- 7: 루트 "" 무시, link: true → local ---
def test_parse_npm_lock_skips_root_and_marks_link_as_local():
    parsed = mod.parse_npm_lock(NPM_LOCK_FIXTURE)
    assert not [key for key in parsed if key == "npm:frontend@0.1.0"]
    assert parsed["npm:linked-lib@"] == "local"


# --- 8: non-registry resolved → git:/url: 정규화 ---
def test_parse_npm_lock_normalizes_non_registry_sources():
    parsed = mod.parse_npm_lock(NPM_LOCK_FIXTURE)
    assert parsed["npm:from-git@1.2.3"] == "git:https://github.com/x/from-git.git#abc"
    assert parsed["npm:from-url@2.0.0"] == "url:https://example.com/pkg/from-url-2.0.0.tgz"


# --- H1: 중복 name@version 키 충돌 시 비-registry 출처가 이긴다 (fail-closed 병합) ---
def test_parse_npm_lock_prefers_non_registry_source_on_key_collision():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/a/node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "https://evil.example.com/foo-1.0.0.tgz",
                },
                "node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                },
            },
        }
    )
    # T-1: `!= "registry"`는 병합 결과가 `local`이어도 통과하는 약한 부정 단언이다 — classify는
    # local을 차단 없이 건너뛰므로 그 회귀는 조용한 fail-open이 된다. 정확한 값과 차단까지 고정한다.
    parsed = mod.parse_npm_lock(fixture)
    assert parsed["npm:foo@1.0.0"] == "url:https://evil.example.com/foo-1.0.0.tgz"
    findings = mod.classify(
        {"npm:foo@1.0.0": (None, parsed["npm:foo@1.0.0"])}, {}, _never_called, NOW
    )
    assert [(f.key, f.reason) for f in findings] == [("npm:foo@1.0.0", mod.REASON_EXOTIC)]


def test_parse_npm_lock_prefers_non_registry_source_regardless_of_order():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                },
                "node_modules/a/node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "https://evil.example.com/foo-1.0.0.tgz",
                },
            },
        }
    )
    parsed = mod.parse_npm_lock(fixture)
    assert parsed["npm:foo@1.0.0"] == "url:https://evil.example.com/foo-1.0.0.tgz"
    findings = mod.classify(
        {"npm:foo@1.0.0": (None, parsed["npm:foo@1.0.0"])}, {}, _never_called, NOW
    )
    assert [(f.key, f.reason) for f in findings] == [("npm:foo@1.0.0", mod.REASON_EXOTIC)]


# --- M-1 ①: 서로 다른 비-registry 출처 둘이 충돌하면 unknown으로 강등한다 (fail-closed) ---
# 기존 값을 조용히 유지하면 나중에 유입된 출처(예: git+ 경로)가 어떤 판정도 받지 못하고
# 사라진다 — unknown은 registry가 아니므로 classify에서 exotic(차단) 취급되고, 값 자체가
# 바뀌므로 diff_changed_packages에도 변경으로 잡힌다.
def test_merge_npm_source_demotes_to_unknown_on_differing_non_registry_sources():
    result = mod._merge_npm_source(
        "url:https://mirror.example.com/foo-1.0.0.tgz",
        "git:https://github.com/x/foo.git#abc",
    )
    assert result == "unknown"


# --- M-1 ②: 동일한 비-registry 출처 중복은 그대로 유지한다(무해한 병합) ---
def test_merge_npm_source_keeps_identical_non_registry_source():
    same = "url:https://mirror.example.com/foo-1.0.0.tgz"
    assert mod._merge_npm_source(same, same) == same


# --- M-1 ③: registry vs 비-registry는 비-registry가 이긴다(기존 동작 보존) ---
def test_merge_npm_source_prefers_non_registry_over_registry():
    non_registry = "git:https://github.com/x/foo.git#abc"
    assert mod._merge_npm_source("registry", non_registry) == non_registry
    assert mod._merge_npm_source(non_registry, "registry") == non_registry


# --- M-1: 실증 시나리오 — 같은 키에 서로 다른 비-registry 출처가 병합되면 parse_npm_lock
# 결과가 unknown이 되어 diff_changed_packages에서도 변경으로 잡힌다 ---
def test_parse_npm_lock_demotes_conflicting_non_registry_sources_to_unknown():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "https://mirror.example.com/foo-1.0.0.tgz",
                },
                "node_modules/a/node_modules/foo": {
                    "version": "1.0.0",
                    "resolved": "git+https://github.com/x/foo.git#abc",
                },
            },
        }
    )
    parsed = mod.parse_npm_lock(fixture)
    assert parsed["npm:foo@1.0.0"] == "unknown"


# --- H2: workspace 중첩 경로(packages/<ws>/node_modules/<dep>)의 서드파티 패키지도 검사 대상 ---
def test_parse_npm_lock_includes_workspace_nested_third_party_package():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "packages/app/node_modules/evil": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/evil/-/evil-1.0.0.tgz",
                },
            },
        }
    )
    parsed = mod.parse_npm_lock(fixture)
    assert "npm:evil@1.0.0" in parsed


# --- M1: npm lockfileVersion 화이트리스트(2·3) 밖 → fail-closed ---
def test_parse_npm_lock_rejects_unsupported_lockfile_version():
    fixture = json.dumps(
        {
            "lockfileVersion": 9,
            "packages": {
                "node_modules/x": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz",
                }
            },
        }
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_npm_lock(fixture)


# --- M2: 파싱 결과 0건 → fail-closed (uv) ---
def test_parse_uv_lock_rejects_empty_result():
    text = 'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n'
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- M2: 파싱 결과 0건 → fail-closed (npm) ---
def test_parse_npm_lock_rejects_empty_result():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {"": {"name": "frontend", "version": "0.1.0"}},
        }
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_npm_lock(fixture)


# --- LOW: node_modules/로 끝나 이름이 빈 문자열이 되는 entry는 skip ---
def test_parse_npm_lock_skips_entry_with_empty_name():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/real": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/real/-/real-1.0.0.tgz",
                },
                "node_modules/": {"version": "0.0.0"},
            },
        }
    )
    parsed = mod.parse_npm_lock(fixture)
    assert "npm:@0.0.0" not in parsed
    assert all(key != "npm:@" for key in parsed)


# --- M4 ①: flush()의 name/version 누락 → raise ---
def test_parse_uv_lock_raises_when_package_missing_name_or_version():
    text = 'version = 1\n\n[[package]]\nname = "onlyname"\n'
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- M4 ②: 사설 인덱스 → url: 강등 ---
def test_normalize_uv_source_marks_private_index_as_url():
    result = mod._normalize_uv_source('{ registry = "https://example-private.com/simple" }')
    assert result == "url:https://example-private.com/simple"


# --- M4 ③: directory/path → path: ---
def test_normalize_uv_source_marks_directory_and_path_as_path():
    assert mod._normalize_uv_source('{ directory = "../local-pkg" }') == "path:../local-pkg"
    assert mod._normalize_uv_source('{ path = "../local-pkg2" }') == "path:../local-pkg2"


# --- M4 ④: 알 수 없는 source kind → unknown ---
def test_normalize_uv_source_returns_unknown_for_unrecognized_kind():
    assert mod._normalize_uv_source('{ workspace = "true" }') == "unknown"


# --- M4 ⑤: npm JSON 파손 → error ---
def test_parse_npm_lock_raises_on_invalid_json():
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_npm_lock("{not valid json")


# --- M4 ⑥: npm packages 키 부재 → error ---
def test_parse_npm_lock_raises_when_packages_key_missing():
    fixture = json.dumps({"lockfileVersion": 3, "name": "frontend"})
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_npm_lock(fixture)


# --- M4 ⑦: resolved 없는 non-link entry → unknown ---
def test_normalize_npm_source_returns_unknown_when_resolved_missing():
    assert mod._normalize_npm_source("foo", {"version": "1.0.0"}) == "unknown"


# --- 9: head에만 있는 키 → 검사 대상 ---
def test_diff_reports_new_key():
    base = {"npm:a@1.0.0": "registry"}
    head = {"npm:a@1.0.0": "registry", "npm:b@2.0.0": "registry"}
    changed = mod.diff_changed_packages(base, head)
    assert changed == {"npm:b@2.0.0": (None, "registry")}


# --- 10: 같은 이름의 버전 변경 → 새 키이므로 검사 대상 ---
def test_diff_treats_version_bump_as_new_key():
    base = {"npm:a@1.0.0": "registry"}
    head = {"npm:a@1.1.0": "registry"}
    changed = mod.diff_changed_packages(base, head)
    assert changed == {"npm:a@1.1.0": (None, "registry")}


# --- 11: base에서 사라진 키는 무시 ---
def test_diff_ignores_removed_key():
    base = {"npm:a@1.0.0": "registry", "npm:gone@9.9.9": "registry"}
    head = {"npm:a@1.0.0": "registry"}
    assert mod.diff_changed_packages(base, head) == {}


# --- 14: 키·출처가 모두 동일 → 검사 대상 아님 ---
def test_diff_ignores_unchanged_key_and_source():
    base = {"pypi:a@1.0.0": "registry", "pypi:b@2.0.0": "local"}
    head = dict(base)
    assert mod.diff_changed_packages(base, head) == {}


# --- spec §6 경로 ②: 키는 동일하나 출처가 바뀜(registry→git) → 검사 대상 ---
# 이 경로가 빠지면 registry→git 스왑이 키 불변을 틈타 게이트를 우회한다.
def test_diff_reports_source_change_on_same_key():
    base = {"npm:a@1.0.0": "registry"}
    head = {"npm:a@1.0.0": "git:https://github.com/x/a.git#abc"}
    changed = mod.diff_changed_packages(base, head)
    assert changed == {"npm:a@1.0.0": ("registry", "git:https://github.com/x/a.git#abc")}


# --- M-2: diff_changed_packages는 호출 전후로 base/head를 mutate하지 않는다(불변성 계약 고정) ---
def test_diff_does_not_mutate_base_or_head_inputs():
    base = {"npm:a@1.0.0": "registry", "npm:b@2.0.0": "registry"}
    head = {"npm:a@1.0.0": "registry", "npm:c@3.0.0": "registry"}
    base_snapshot = dict(base)
    head_snapshot = dict(head)
    mod.diff_changed_packages(base, head)
    assert base == base_snapshot
    assert head == head_snapshot


# --- L-2: base가 빈 dict(신규 lockfile 도입 등) → head 전부가 검사 대상이 된다 ---
def test_diff_treats_empty_base_as_all_new():
    base: dict[str, str] = {}
    head = {"npm:a@1.0.0": "registry", "npm:b@2.0.0": "local"}
    changed = mod.diff_changed_packages(base, head)
    assert changed == {
        "npm:a@1.0.0": (None, "registry"),
        "npm:b@2.0.0": (None, "local"),
    }


NOW = datetime(2026, 7, 27, 0, 0, 0, tzinfo=UTC)


def _never_called(*args):
    raise AssertionError(f"registry 조회가 호출되면 안 된다: {args!r}")


def _fetch_at(published: datetime | None):
    def _fetch(ecosystem, name, version):
        return published

    return _fetch


# --- 12: 동일 키에서 출처만 registry → git → 검사 대상이고 exotic 차단 (회귀) ---
def test_source_swap_to_git_is_detected_and_blocked():
    base = {"npm:foo@1.2.3": "registry"}
    head = {"npm:foo@1.2.3": "git:https://github.com/x/foo.git#v1.2.3"}
    changed = mod.diff_changed_packages(base, head)
    assert changed == {"npm:foo@1.2.3": ("registry", "git:https://github.com/x/foo.git#v1.2.3")}
    findings = mod.classify(changed, {}, _never_called, NOW)
    assert [(f.key, f.reason) for f in findings] == [("npm:foo@1.2.3", mod.REASON_EXOTIC)]
    assert findings[0].base_source == "registry"


# --- 13: 동일 키에서 출처만 git → registry → publish time 분류가 실행됨 ---
def test_source_swap_to_registry_runs_publish_time_classification():
    base = {"npm:foo@1.2.3": "git:https://github.com/x/foo.git#v1.2.3"}
    head = {"npm:foo@1.2.3": "registry"}
    changed = mod.diff_changed_packages(base, head)
    calls = []

    def _fetch(ecosystem, name, version):
        calls.append((ecosystem, name, version))
        return NOW - timedelta(days=1)

    findings = mod.classify(changed, {}, _fetch, NOW)
    assert calls == [("npm", "foo", "1.2.3")]
    assert [(f.key, f.reason) for f in findings] == [("npm:foo@1.2.3", mod.REASON_FRESH)]


# --- 15: 7일 미만 → fresh ---
def test_classify_blocks_package_published_within_window():
    changed = {"pypi:new-pkg@1.0.0": (None, "registry")}
    findings = mod.classify(changed, {}, _fetch_at(NOW - timedelta(days=6, hours=23)), NOW)
    assert [(f.key, f.reason) for f in findings] == [("pypi:new-pkg@1.0.0", mod.REASON_FRESH)]
    assert findings[0].age_days < 7


# --- 16: 정확히 7일 경계 → 통과 ---
def test_classify_passes_package_at_exact_boundary():
    changed = {"pypi:new-pkg@1.0.0": (None, "registry")}
    assert mod.classify(changed, {}, _fetch_at(NOW - timedelta(days=7)), NOW) == []


# --- 17: publish time 없음 → unverifiable ---
def test_classify_blocks_when_publish_time_missing():
    changed = {"npm:ghost@0.0.1": (None, "registry")}
    findings = mod.classify(changed, {}, _fetch_at(None), NOW)
    assert [(f.key, f.reason) for f in findings] == [("npm:ghost@0.0.1", mod.REASON_UNVERIFIABLE)]


# --- 18: allowlist name 전체 면제 ---
def test_allowlist_name_exempts_every_version():
    changed = {"npm:brace-expansion@2.0.2": (None, "registry")}
    allowlist = {"npm": ["brace-expansion"]}
    assert mod.classify(changed, allowlist, _never_called, NOW) == []


# --- 19: allowlist name@version 특정 버전만 면제 ---
def test_allowlist_pinned_version_exempts_only_that_version():
    allowlist = {"npm": ["brace-expansion@2.0.2"]}
    assert (
        mod.classify(
            {"npm:brace-expansion@2.0.2": (None, "registry")}, allowlist, _never_called, NOW
        )
        == []
    )
    findings = mod.classify(
        {"npm:brace-expansion@2.0.3": (None, "registry")},
        allowlist,
        _fetch_at(NOW - timedelta(days=1)),
        NOW,
    )
    assert [f.reason for f in findings] == [mod.REASON_FRESH]


# --- 20: ecosystem 격리 — npm allowlist가 PyPI 동명 패키지를 면제하지 않는다 ---
def test_allowlist_is_scoped_per_ecosystem():
    allowlist = {"npm": ["requests"], "pypi": []}
    assert (
        mod.classify({"npm:requests@0.0.1": (None, "registry")}, allowlist, _never_called, NOW)
        == []
    )
    findings = mod.classify(
        {"pypi:requests@2.31.0": (None, "registry")},
        allowlist,
        _fetch_at(NOW - timedelta(days=2)),
        NOW,
    )
    assert [(f.key, f.reason) for f in findings] == [("pypi:requests@2.31.0", mod.REASON_FRESH)]


# --- 21: exotic은 registry 조회 함수를 호출하지 않는다 ---
def test_exotic_never_calls_registry():
    changed = {
        "npm:tarball@1.0.0": (None, "url:https://example.com/t.tgz"),
        "pypi:vendored@1.0.0": (None, "git:https://github.com/x/v.git"),
        "pypi:selfpkg@0.1.0": (None, "local"),
    }
    findings = mod.classify(changed, {}, _never_called, NOW)
    assert sorted((f.key, f.reason) for f in findings) == [
        ("npm:tarball@1.0.0", mod.REASON_EXOTIC),
        ("pypi:vendored@1.0.0", mod.REASON_EXOTIC),
    ]


# --- T-4: allowlist 검사는 exotic 검사보다 **먼저** 돈다 — 이름 단위 면제는 출처 변경 차단까지
# 함께 해제한다(`render_findings`가 사용자에게 안내하는 의미론). 순서가 뒤집히거나 면제 범위가
# registry 축으로 좁아지면 안내와 실동작이 어긋나므로 여기서 고정한다. ---
def test_allowlist_name_also_lifts_exotic_source_change_block():
    changed = {"npm:foo@1.2.3": ("registry", "git:https://github.com/x/foo.git#v1.2.3")}
    assert mod.classify(changed, {"npm": ["foo"]}, _never_called, NOW) == []
    # 면제가 없으면 동일 입력이 차단된다 — 위 통과가 면제 때문임을 고정한다.
    assert [f.reason for f in mod.classify(changed, {}, _never_called, NOW)] == [mod.REASON_EXOTIC]


def test_allowlist_pinned_version_lifts_exotic_only_for_that_version():
    allowlist = {"npm": ["foo@1.2.3"]}
    exempt = {"npm:foo@1.2.3": ("registry", "url:https://evil.example.com/foo.tgz")}
    other = {"npm:foo@2.0.0": ("registry", "url:https://evil.example.com/foo.tgz")}
    assert mod.classify(exempt, allowlist, _never_called, NOW) == []
    assert [f.reason for f in mod.classify(other, allowlist, _never_called, NOW)] == [
        mod.REASON_EXOTIC
    ]


# --- T-5: `local`은 유일하게 차단 경로 커버리지가 없던 출처다. 위험한 형태는 신규 키가 아니라
# **출처 변경**(registry → editable/link:true)이며, diff는 잡지만 classify가 무조건 건너뛴다.
# 이 무조건 제외는 spec §6 규칙 2의 확정 결정("정규화 출처가 local인 것 — 로컬 코드이므로 제외")
# 이므로 의도된 면제다. 미검증 구멍과 구분되도록 기대 판정을 명시적으로 고정한다. ---
def test_source_change_to_local_is_intentionally_skipped():
    changed = mod.diff_changed_packages(
        {"pypi:tool@1.0.0": "registry", "npm:widget@2.0.0": "registry"},
        {"pypi:tool@1.0.0": "local", "npm:widget@2.0.0": "local"},
    )
    assert changed == {
        "pypi:tool@1.0.0": ("registry", "local"),
        "npm:widget@2.0.0": ("registry", "local"),
    }
    assert mod.classify(changed, {}, _never_called, NOW) == []


# --- 보조 3: 정규화 불가 출처(unknown) → exotic (가정 A2 고정) ---
def test_unknown_source_is_blocked_as_exotic():
    findings = mod.classify({"npm:mystery@1.0.0": (None, "unknown")}, {}, _never_called, NOW)
    assert [(f.key, f.reason) for f in findings] == [("npm:mystery@1.0.0", mod.REASON_EXOTIC)]


# --- T-3: 운영 allowlist 파일은 **면제가 추가되도록 설계된** 파일이다(render_findings가
# 사용자에게 추가·커밋을 안내한다). 내용을 정확 비교로 고정하면 게이트가 의도대로 처음 동작하는
# 순간 무관한 이유로 CI가 깨지고, 개발자는 면제 심사 대신 이 테스트를 지우도록 압박받는다.
# 그래서 내용이 아니라 **형태 계약**(두 ecosystem 키 + 비어있지 않은 문자열 배열)을 고정하고,
# 실파일이 운영 검증기를 그대로 통과하는지까지 확인한다. ---
def test_allowlist_file_matches_schema():
    path = _REPO_ROOT / "scripts" / "lockfile_allowlist.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"npm", "pypi"}
    for ecosystem, items in payload.items():
        assert isinstance(items, list), ecosystem
        assert all(isinstance(item, str) and item for item in items), ecosystem
    assert mod.load_allowlist(path) == payload


# --- H2: load_allowlist이 파일 부재 시 빈 allowlist를 반환한다 ---
def test_load_allowlist_returns_empty_when_file_missing(tmp_path):
    assert mod.load_allowlist(tmp_path / "missing.json") == {}


# --- H2: load_allowlist이 JSON 파손 시 fail-closed ---
def test_load_allowlist_raises_on_invalid_json(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1: 최상위가 dict가 아니면 fail-closed ---
def test_load_allowlist_raises_when_top_level_is_not_object(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(["npm"]), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1: ecosystem 값이 배열이 아니라 객체면(버전 핀 의도) fail-closed ---
def test_load_allowlist_raises_when_ecosystem_value_is_dict(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": {"brace-expansion": "2.0.2"}}), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1: ecosystem 값이 문자열이면(글자 단위 리스트화 방지) fail-closed ---
def test_load_allowlist_raises_when_ecosystem_value_is_string(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": "brace-expansion"}), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1: ecosystem 값이 null이면 fail-closed ---
def test_load_allowlist_raises_when_ecosystem_value_is_null(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": None}), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1: 항목이 문자열이 아니면(무진단 통과 방지) fail-closed ---
def test_load_allowlist_raises_when_item_is_not_string(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": [1, 2]}), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H1(=L6 동시 해소): 빈 문자열 항목은 "@" 없는 키를 전부 면제하므로 fail-closed ---
def test_load_allowlist_raises_when_item_is_empty_string(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": [""]}), encoding="utf-8")
    with pytest.raises(mod.LockfileFormatError):
        mod.load_allowlist(path)


# --- H2: 정상 페이로드는 그대로 반환된다 ---
def test_load_allowlist_accepts_valid_payload(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"npm": ["foo", "bar@1.0.0"], "pypi": []}), encoding="utf-8")
    assert mod.load_allowlist(path) == {"npm": ["foo", "bar@1.0.0"], "pypi": []}


# --- H3: 스코프 npm 패키지가 classify에서 (ecosystem, name, version)으로 올바르게 분리된다 ---
def test_classify_splits_scoped_npm_package_name_and_version():
    changed = {"npm:@scope/name@1.0.0": (None, "registry")}
    calls = []

    def _fetch(ecosystem, name, version):
        calls.append((ecosystem, name, version))
        return NOW - timedelta(days=10)

    findings = mod.classify(changed, {}, _fetch, NOW)
    assert calls == [("npm", "@scope/name", "1.0.0")]
    assert findings == []


# --- H3: allowlist 이름 면제가 스코프 npm 패키지에도 동작한다 ---
def test_allowlist_name_exempts_scoped_npm_package():
    changed = {"npm:@scope/name@9.9.9": (None, "registry")}
    allowlist = {"npm": ["@scope/name"]}
    assert mod.classify(changed, allowlist, _never_called, NOW) == []


# --- M1: is_allowlisted가 split_key를 재사용해도 스코프 패키지 pin이 정확히 동작한다(회귀) ---
def test_is_allowlisted_pins_scoped_npm_package_to_exact_version():
    allowlist = {"npm": ["@scope/name@1.0.0"]}
    assert mod.is_allowlisted("npm:@scope/name@1.0.0", allowlist) is True
    assert mod.is_allowlisted("npm:@scope/name@2.0.0", allowlist) is False


# --- M1: version 문자열에 "@"가 포함되면 name/version 분리가 모호해지므로 파서가 fail-closed ---
def test_parse_npm_lock_rejects_version_containing_at_symbol():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/foo": {
                    "version": "1.0.0@evil",
                    "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                },
            },
        }
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_npm_lock(fixture)


# --- M1: uv.lock도 동일하게 fail-closed ---
def test_parse_uv_lock_rejects_version_containing_at_symbol():
    text = (
        'version = 1\n\n[[package]]\nname = "foo"\nversion = "1.0.0@evil"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    with pytest.raises(mod.LockfileFormatError):
        mod.parse_uv_lock(text)


# --- M3: fetch_publish_time이 naive datetime을 반환하면 키를 포함한 명시적 에러로 차단한다 ---
def test_classify_raises_explicit_error_for_naive_publish_time():
    naive = datetime(2026, 7, 20, 0, 0, 0)  # tzinfo 없음
    changed = {"npm:foo@1.0.0": (None, "registry")}
    with pytest.raises(mod.LockfileFormatError, match="npm:foo@1.0.0"):
        mod.classify(changed, {}, _fetch_at(naive), NOW)


# --- M4: render_findings — 출처 변경(exotic) 형태 ---
def test_render_findings_includes_key_reason_and_source_change():
    findings = [
        mod.Finding(
            "npm:foo@1.0.0",
            mod.REASON_EXOTIC,
            "registry",
            "git:https://github.com/x/foo.git",
            None,
            None,
        )
    ]
    output = mod.render_findings(findings)
    assert "npm:foo@1.0.0" in output
    assert f"reason={mod.REASON_EXOTIC}" in output
    assert "registry -> git:https://github.com/x/foo.git" in output


# --- M4: render_findings — fresh 형태(publish 시각 + 경과일) ---
def test_render_findings_includes_publish_time_and_age_for_fresh():
    published = datetime(2026, 7, 25, 0, 0, 0, tzinfo=UTC)
    findings = [
        mod.Finding("pypi:new-pkg@1.0.0", mod.REASON_FRESH, None, "registry", published, 2.0)
    ]
    output = mod.render_findings(findings)
    assert "pypi:new-pkg@1.0.0" in output
    assert f"reason={mod.REASON_FRESH}" in output
    assert published.isoformat() in output
    assert "age=2.00d" in output


# --- M4: render_findings — 출처 변경 없는 신규 키(출처 그대로 표시) ---
def test_render_findings_shows_source_label_when_unchanged():
    findings = [mod.Finding("npm:new@1.0.0", mod.REASON_UNVERIFIABLE, None, "registry", None, None)]
    output = mod.render_findings(findings)
    assert "출처: registry" in output


# --- M4+M2+L5: render_findings 말미 allowlist 안내(경로+이름 면제 범위+정확 일치 요구) ---
def test_render_findings_ends_with_allowlist_guidance():
    findings = [mod.Finding("npm:a@1.0.0", mod.REASON_EXOTIC, None, "unknown", None, None)]
    output = mod.render_findings(findings)
    assert mod.ALLOWLIST_PATH in output
    assert "이름 단위 면제" in output
    assert "정확히 일치" in output


# --- M4: 차단 목록과 allowlist 안내 사이에 빈 줄 구분자가 있다 ---
def test_render_findings_separates_findings_from_guidance_with_blank_line():
    findings = [mod.Finding("npm:a@1.0.0", mod.REASON_EXOTIC, None, "unknown", None, None)]
    lines = mod.render_findings(findings).split("\n")
    assert lines[1] == ""


# --- L4: classify 출력은 키 정렬 순서로 결정적이다 ---
def test_classify_returns_findings_in_sorted_key_order():
    changed = {
        "npm:zeta@1.0.0": (None, "url:https://example.com/z.tgz"),
        "npm:alpha@1.0.0": (None, "url:https://example.com/a.tgz"),
    }
    findings = mod.classify(changed, {}, _never_called, NOW)
    assert [f.key for f in findings] == ["npm:alpha@1.0.0", "npm:zeta@1.0.0"]


_UV_MIN = """version = 1

[[package]]
name = "aiofile"
version = "3.11.1"
source = { registry = "https://pypi.org/simple" }
"""

_UV_MIN_PLUS = (
    _UV_MIN
    + """
[[package]]
name = "brandnew"
version = "9.9.9"
source = { registry = "https://pypi.org/simple" }
"""
)

_NPM_MIN = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/react": {
                "version": "19.0.0",
                "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz",
            },
        },
    }
)


_NPM_MIN_PLUS = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/react": {
                "version": "19.0.0",
                "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz",
            },
            "node_modules/brandnew-npm": {
                "version": "9.9.9",
                "resolved": "https://registry.npmjs.org/brandnew-npm/-/brandnew-npm-9.9.9.tgz",
            },
        },
    }
)


def _wire_lockfiles(
    monkeypatch,
    uv_base,
    uv_head,
    ml_base=_UV_MIN,
    ml_head=_UV_MIN,
    npm_base=_NPM_MIN,
    npm_head=_NPM_MIN,
    allowlist=None,
):
    def _pick(path, backend, ml, npm):
        # dict 조회라 LOCKFILES에 축이 늘면 KeyError로 즉시 드러난다(조용한 누락 방지).
        return {
            mod.BACKEND_UV_LOCK_PATH: backend,
            mod.ML_UV_LOCK_PATH: ml,
            mod.NPM_LOCK_PATH: npm,
        }[path]

    def _base(root, base_ref, path):
        return _pick(path, uv_base, ml_base, npm_base)

    def _head(root, path):
        return _pick(path, uv_head, ml_head, npm_head)

    monkeypatch.setattr(mod, "read_base_lockfile", _base)
    monkeypatch.setattr(mod, "read_head_lockfile", _head)
    # T-3: main 테스트를 운영 allowlist 파일 내용에서 격리한다 — 그 파일은 면제가 추가되도록
    # 설계돼 있어, 실파일을 읽으면 정상 면제 1건이 무관한 테스트의 판정을 바꿀 수 있다.
    monkeypatch.setattr(mod, "load_allowlist", lambda path: dict(allowlist or {}))


# --- 22: findings 있으면 종료코드 1 ---
def test_main_returns_one_when_findings_exist(monkeypatch, capsys):
    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN_PLUS)
    monkeypatch.setattr(mod, "fetch_publish_time", lambda eco, name, version: datetime.now(UTC))
    assert mod.main(["--base", "deadbeef"]) == 1
    out = capsys.readouterr().out
    assert "pypi:brandnew@9.9.9" in out
    assert "fresh" in out


# --- T-2: main 레벨 테스트가 전부 uv 축만 주입해, `frontend/package-lock.json` 축은 단 한 번도
# 피검체가 되지 않았다. main의 lockfile 튜플에서 npm 항목이 빠지는 변경(= 프런트 생태계 전체가
# 게이트를 벗어남)이 어떤 테스트도 깨뜨리지 않는다. npm 축 신규 패키지의 차단을 고정한다. ---
def test_main_blocks_new_package_from_npm_lockfile(monkeypatch, capsys):
    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN, npm_base=_NPM_MIN, npm_head=_NPM_MIN_PLUS)
    monkeypatch.setattr(mod, "fetch_publish_time", lambda eco, name, version: datetime.now(UTC))
    assert mod.main(["--base", "deadbeef"]) == 1
    out = capsys.readouterr().out
    assert "npm:brandnew-npm@9.9.9" in out
    assert "fresh" in out


# --- T-3: main이 allowlist를 실제로 classify까지 전달한다(면제 경로의 결선 고정) ---
def test_main_returns_zero_when_finding_is_allowlisted(monkeypatch, capsys):
    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN_PLUS, allowlist={"pypi": ["brandnew"]})
    monkeypatch.setattr(mod, "fetch_publish_time", _never_called)
    assert mod.main(["--base", "deadbeef"]) == 0
    assert "검사 대상 1건, 차단 0건" in capsys.readouterr().out


# --- 23: findings 없으면 종료코드 0 ---
def test_main_returns_zero_when_no_findings(monkeypatch, capsys):
    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN)
    monkeypatch.setattr(mod, "fetch_publish_time", _never_called)
    assert mod.main(["--base", "deadbeef"]) == 0
    assert "검사 대상 0건" in capsys.readouterr().out


# --- 24: base lockfile 부재 → 종료코드 1 ---
def test_main_returns_one_when_base_lockfile_missing(monkeypatch, capsys):
    def _boom(root, base_ref, path):
        raise mod.LockfileFormatError(f"base lockfile을 읽을 수 없습니다: {base_ref}:{path}")

    monkeypatch.setattr(mod, "read_base_lockfile", _boom)
    monkeypatch.setattr(mod, "read_head_lockfile", lambda root, path: _UV_MIN)
    assert mod.main(["--base", "deadbeef"]) == 1
    assert "base lockfile" in capsys.readouterr().out


# --- 25: registry 조회 3회 실패 → 종료코드 1 (fail-closed) ---
def test_main_fails_closed_after_three_registry_attempts(monkeypatch, capsys):
    import urllib.error

    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN_PLUS)
    mod.reset_http_cache()
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # 재시도 backoff 대기 제거
    attempts = []

    def _urlopen(request, timeout=None):
        attempts.append(getattr(request, "full_url", request))
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert mod.main(["--base", "deadbeef"]) == 1
    assert len(attempts) == mod.FETCH_ATTEMPTS == 3
    assert "registry 조회 실패" in capsys.readouterr().out


# --- 보조 1: publish 시각 형식 파손 → RegistryLookupError (spec §8 4행) ---
def test_parse_iso8601_rejects_malformed_timestamp():
    with pytest.raises(mod.RegistryLookupError):
        mod._parse_iso8601("2026-07-XX")


# --- 보조 2: tz 정보 없는 시각 → RegistryLookupError (naive datetime 금지, spec §8 말미) ---
def test_parse_iso8601_rejects_naive_timestamp():
    with pytest.raises(mod.RegistryLookupError):
        mod._parse_iso8601("2026-07-27T00:00:00")


# =====================================================================================
# registry 조회 계약 — 전부 urlopen 스텁으로 수행하며 네트워크를 타지 않는다.
# =====================================================================================


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, amt=None):
        return self._body if amt is None else self._body[:amt]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _stub_urlopen(monkeypatch, handler):
    """urlopen을 스텁으로 갈아끼우고 호출 기록 리스트를 돌려준다."""
    calls = []

    def _urlopen(request, timeout=None):
        calls.append({"url": request.full_url, "timeout": timeout, "request": request})
        result = handler(request.full_url)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    return calls


def _json_response(payload):
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _http_error(url, code):
    import urllib.error

    return urllib.error.HTTPError(url, code, "boom", {}, None)


def _silence_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    return sleeps


_NPM_PAYLOAD = {
    "time": {"19.0.0": "2024-12-05T01:02:03.000Z", "18.0.0": "2022-03-29T00:00:00.000Z"}
}
_PYPI_PAYLOAD = {"urls": [{"upload_time_iso_8601": "2024-01-02T03:04:05.000000Z"}]}


# --- C1 회귀 ①: npm 이름에 URL 특수문자(`#`)가 섞이면 파서 층에서 fail-closed ---
# 리뷰 실측: "node_modules/react#x"가 통과하면 조회 URL의 fragment로 잘려 react의 publish
# 시각으로 판정되고, evil 패키지가 "차단 0건"으로 게이트를 통과한다.
def test_parse_npm_lock_rejects_name_with_url_special_characters():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/react#x": {
                    "version": "19.0.0",
                    "resolved": "https://registry.npmjs.org/evil-new-pkg/-/evil-new-pkg-1.0.0.tgz",
                },
            },
        }
    )
    with pytest.raises(mod.LockfileFormatError, match="이름"):
        mod.parse_npm_lock(fixture)


# --- C1 회귀 ②: uv.lock version에 경로 traversal + 쿼리스트링이 섞이면 파서 층에서 fail-closed ---
# 리뷰 실측: "1.0.0/../../requests/2.31.0/json?x=1"이 PyPI URL에 그대로 삽입되면 실제로 200이
# 떨어지고(requests 메타) 늙은 패키지로 오판된다.
def test_parse_uv_lock_rejects_version_with_path_traversal():
    text = (
        'version = 1\n\n[[package]]\nname = "evil"\n'
        'version = "1.0.0/../../requests/2.31.0/json?x=1"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    with pytest.raises(mod.LockfileFormatError, match="version"):
        mod.parse_uv_lock(text)


# --- C1: 조회 직전 화이트리스트 검증 — 이름 위반은 URL을 만들기 전에 차단한다 ---
@pytest.mark.parametrize(
    ("ecosystem", "name", "version"),
    [
        ("npm", "react#x", "19.0.0"),
        ("npm", "react?x=1", "19.0.0"),
        ("npm", "../../etc/passwd", "19.0.0"),
        ("pypi", "requests/2.31.0/json", "2.31.0"),
    ],
)
def test_fetch_publish_time_rejects_malformed_name_before_request(
    monkeypatch, ecosystem, name, version
):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response({}))
    with pytest.raises(mod.RegistryLookupError, match="형식 위반"):
        mod.fetch_publish_time(ecosystem, name, version)
    assert calls == []


# --- C1: 버전 위반도 동일하게 조회 이전에 차단한다 ---
@pytest.mark.parametrize(
    ("ecosystem", "version"),
    [
        ("pypi", "1.0.0/../../requests/2.31.0/json?x=1"),
        ("pypi", "1.0.0?x=1"),
        ("npm", "19.0.0#x"),
        ("npm", ""),
    ],
)
def test_fetch_publish_time_rejects_malformed_version_before_request(
    monkeypatch, ecosystem, version
):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response({}))
    with pytest.raises(mod.RegistryLookupError, match="형식 위반"):
        mod.fetch_publish_time(ecosystem, "requests", version)
    assert calls == []


# --- H4 ① + ⑥: npm packument에서 publish 시각을 실제로 추출한다 ---
# `payload["time"]` 키 이름이 바뀌면(뮤턴트) 조회가 전면 파손되므로 이 단언이 그것을 죽인다.
def test_npm_fetch_extracts_publish_time_from_time_map(monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(_NPM_PAYLOAD))
    result = mod.fetch_publish_time("npm", "react", "19.0.0")
    assert result == datetime(2024, 12, 5, 1, 2, 3, tzinfo=UTC)
    assert calls[0]["url"] == "https://registry.npmjs.org/react"


# --- H4 ①: 스코프 npm은 `/`를 %2F로 인코딩한 단일 세그먼트 URL을 쓴다 ---
def test_npm_fetch_encodes_scoped_name_with_percent_2f(monkeypatch):
    payload = {"time": {"7.26.2": "2024-11-01T00:00:00.000Z"}}
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(payload))
    result = mod.fetch_publish_time("npm", "@babel/code-frame", "7.26.2")
    assert result == datetime(2024, 11, 1, tzinfo=UTC)
    assert calls[0]["url"] == "https://registry.npmjs.org/@babel%2Fcode-frame"


# --- H4 ①: PyPI 버전별 엔드포인트에서 upload_time_iso_8601을 추출한다 ---
def test_pypi_fetch_extracts_publish_time_from_urls(monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(_PYPI_PAYLOAD))
    result = mod.fetch_publish_time("pypi", "requests", "2.31.0")
    assert result == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert calls[0]["url"] == "https://pypi.org/pypi/requests/2.31.0/json"


# --- H4: 모든 요청에 User-Agent 헤더가 실린다 ---
def test_registry_request_carries_user_agent_header(monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(_NPM_PAYLOAD))
    mod.fetch_publish_time("npm", "react", "19.0.0")
    assert calls[0]["request"].get_header("User-agent") == mod.USER_AGENT
    assert mod.USER_AGENT


# --- H4 ④: urlopen에 넘어간 timeout이 REQUEST_TIMEOUT_SECONDS(=10)와 같다 ---
# 체인 비교라 "timeout 인자 제거"와 "타임아웃 상수 변경" 뮤턴트를 둘 다 죽인다.
def test_registry_request_uses_declared_timeout(monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(_NPM_PAYLOAD))
    mod.fetch_publish_time("npm", "react", "19.0.0")
    assert calls[0]["timeout"] == mod.REQUEST_TIMEOUT_SECONDS == 10


# --- H4 ②: 404 → None → classify가 unverifiable로 차단 ---
def test_registry_404_returns_none_and_classifies_as_unverifiable(monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _http_error(url, 404))
    assert mod.fetch_publish_time("npm", "ghost", "0.0.1") is None
    findings = mod.classify(
        {"npm:ghost@0.0.1": (None, "registry")}, {}, mod.fetch_publish_time, NOW
    )
    assert [(f.key, f.reason) for f in findings] == [("npm:ghost@0.0.1", mod.REASON_UNVERIFIABLE)]


# --- H4 ③: 같은 URL 2회 조회는 urlopen을 1회만 호출한다(이름 단위 캐시) ---
def test_registry_lookup_is_cached_per_url(monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _json_response(_NPM_PAYLOAD))
    first = mod.fetch_publish_time("npm", "react", "19.0.0")
    second = mod.fetch_publish_time("npm", "react", "18.0.0")
    assert len(calls) == 1
    assert first == datetime(2024, 12, 5, 1, 2, 3, tzinfo=UTC)
    assert second == datetime(2022, 3, 29, tzinfo=UTC)


# --- H4 ③: npm과 pypi가 같은 이름이어도 서로의 캐시를 오염시키지 않는다 ---
# 캐시 키에서 URL을 빼 전역 1슬롯으로 만드는 뮤턴트를 죽인다.
def test_registry_cache_does_not_leak_across_ecosystems(monkeypatch):
    def _handler(url):
        return _json_response(_PYPI_PAYLOAD if "pypi.org" in url else _NPM_PAYLOAD)

    calls = _stub_urlopen(monkeypatch, _handler)
    npm_time = mod.fetch_publish_time("npm", "requests", "19.0.0")
    pypi_time = mod.fetch_publish_time("pypi", "requests", "2.31.0")
    assert len(calls) == 2
    assert npm_time == datetime(2024, 12, 5, 1, 2, 3, tzinfo=UTC)
    assert pypi_time == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


# --- H1: payload가 JSON 객체가 아니면 raw traceback 대신 RegistryLookupError ---
@pytest.mark.parametrize("payload", ["문자열", [1, 2, 3], 42, None])
def test_registry_rejects_non_object_payload(monkeypatch, payload):
    _silence_sleep(monkeypatch)
    _stub_urlopen(monkeypatch, lambda url: _json_response(payload))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")


# --- H1: npm `time`이 dict가 아니면 RegistryLookupError ---
def test_npm_rejects_non_dict_time_field(monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _json_response({"time": "2024-12-05T00:00:00.000Z"}))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")


# --- H1: npm `time[version]`이 문자열이 아니면 RegistryLookupError ---
def test_npm_rejects_non_string_timestamp(monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _json_response({"time": {"19.0.0": 1733356800}}))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")


# --- H1: npm `time`에 해당 버전이 없으면 None(→ unverifiable) ---
def test_npm_returns_none_when_version_absent_from_time_map(monkeypatch):
    _stub_urlopen(
        monkeypatch, lambda url: _json_response({"time": {"1.0.0": "2020-01-01T00:00:00Z"}})
    )
    assert mod.fetch_publish_time("npm", "react", "19.0.0") is None


# --- H1: PyPI `urls`가 리스트가 아니면 RegistryLookupError ---
@pytest.mark.parametrize("urls", [{"a": 1}, "문자열", 7])
def test_pypi_rejects_non_list_urls_field(monkeypatch, urls):
    _stub_urlopen(monkeypatch, lambda url: _json_response({"urls": urls}))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("pypi", "requests", "2.31.0")


# --- H1: PyPI `urls` 항목이 객체가 아니거나 타임스탬프가 문자열이 아니면 RegistryLookupError ---
@pytest.mark.parametrize("entry", ["문자열", 7, {"upload_time_iso_8601": 12345}])
def test_pypi_rejects_malformed_urls_entry(monkeypatch, entry):
    _stub_urlopen(monkeypatch, lambda url: _json_response({"urls": [entry]}))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("pypi", "requests", "2.31.0")


# --- H1: PyPI `urls`가 빈 배열이면 None(→ unverifiable, spec §6.1) ---
def test_pypi_returns_none_for_empty_urls(monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _json_response({"urls": []}))
    assert mod.fetch_publish_time("pypi", "requests", "2.31.0") is None


# --- H1: http.client.InvalidURL(HTTPException 계열)도 재시도 루프 안에서 잡힌다 ---
# URLError/OSError/ValueError 튜플에 HTTPException을 추가해 잡는다(빠뜨리면 예외 경계 탈출).
def test_registry_retries_on_http_client_exception(monkeypatch):
    import http.client

    _silence_sleep(monkeypatch)
    calls = _stub_urlopen(monkeypatch, lambda url: http.client.InvalidURL("bad url"))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")
    assert len(calls) == mod.FETCH_ATTEMPTS


# --- H3: PyPI는 `urls` 중 가장 최근 업로드 시각으로 판정한다(min → max, fail-closed) ---
# PyPI는 기존 release에 아티팩트 추가 업로드를 허용하므로, min을 쓰면 옛 버전에 얹은 악성
# wheel이 "늙은 패키지"로 통과한다.
def test_pypi_uses_latest_upload_time_and_blocks_as_fresh(monkeypatch):
    payload = {
        "urls": [
            {"upload_time_iso_8601": "2020-01-01T00:00:00.000000Z"},
            {"upload_time_iso_8601": "2026-07-26T00:00:00.000000Z"},
        ]
    }
    _stub_urlopen(monkeypatch, lambda url: _json_response(payload))
    published = mod.fetch_publish_time("pypi", "requests", "2.31.0")
    assert published == datetime(2026, 7, 26, tzinfo=UTC)
    findings = mod.classify(
        {"pypi:requests@2.31.0": (None, "registry")}, {}, mod.fetch_publish_time, NOW
    )
    assert [(f.key, f.reason) for f in findings] == [("pypi:requests@2.31.0", mod.REASON_FRESH)]


# --- M1: 응답 본문이 상한을 넘으면 RegistryLookupError (무제한 read 방지) ---
def test_registry_rejects_oversized_response(monkeypatch):
    _silence_sleep(monkeypatch)
    body = b"{}" + b" " * (mod.MAX_RESPONSE_BYTES + 1)
    _stub_urlopen(monkeypatch, lambda url: _FakeResponse(body))
    with pytest.raises(mod.RegistryLookupError, match="응답이 너무 큽니다"):
        mod.fetch_publish_time("npm", "react", "19.0.0")


# --- M2: 재시도 가치 없는 상태코드(403)는 즉시 실패한다 ---
def test_registry_does_not_retry_non_retryable_status(monkeypatch):
    _silence_sleep(monkeypatch)
    calls = _stub_urlopen(monkeypatch, lambda url: _http_error(url, 403))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")
    assert len(calls) == 1


# --- M2: 재시도 가치 있는 상태코드(429·5xx)는 FETCH_ATTEMPTS만큼 시도하고 backoff를 넣는다 ---
@pytest.mark.parametrize("code", [429, 500, 503])
def test_registry_retries_retryable_status_with_backoff(monkeypatch, code):
    sleeps = _silence_sleep(monkeypatch)
    calls = _stub_urlopen(monkeypatch, lambda url: _http_error(url, code))
    with pytest.raises(mod.RegistryLookupError):
        mod.fetch_publish_time("npm", "react", "19.0.0")
    assert len(calls) == mod.FETCH_ATTEMPTS
    assert sleeps == [mod.RETRY_BACKOFF_SECONDS] * (mod.FETCH_ATTEMPTS - 1)


# --- H2: resolved가 registry 호스트라도 패키지 이름 경로가 다르면 unknown(→ exotic) ---
# 리뷰 실측: left-pad(2018 publish) entry에 evil-new tarball을 넣으면 특수문자 없이도
# "registry"로 인정돼 늙은 publish 시각으로 통과한다.
def test_normalize_npm_source_demotes_registry_host_with_mismatched_path():
    entry = {
        "version": "1.3.0",
        "resolved": "https://registry.npmjs.org/evil-new/-/evil-new-1.0.0.tgz",
    }
    assert mod._normalize_npm_source("left-pad", entry) == "unknown"


def test_parse_npm_lock_blocks_registry_tarball_of_another_package():
    fixture = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/left-pad": {
                    "version": "1.3.0",
                    "resolved": "https://registry.npmjs.org/evil-new/-/evil-new-1.0.0.tgz",
                },
            },
        }
    )
    parsed = mod.parse_npm_lock(fixture)
    assert parsed["npm:left-pad@1.3.0"] == "unknown"
    findings = mod.classify({"npm:left-pad@1.3.0": (None, "unknown")}, {}, _never_called, NOW)
    assert [(f.key, f.reason) for f in findings] == [("npm:left-pad@1.3.0", mod.REASON_EXOTIC)]


# --- SC-2: tarball 경로 검증이 **이름 축만** 보면 버전 축이 그대로 열린다. 설치는
# `resolved`+`integrity`가 결정하고 신선도 판정은 lockfile의 `version`으로 하므로, 늙은 버전을
# 그대로 두고 tarball만 새 버전으로 바꾸면 (a) 출처가 registry로 유지돼 diff에도 안 잡히고
# (b) 늙은 publish 시각으로 통과한다. 실 lockfile 412건 전부가 정본 형태라 오탐 비용이 없다. ---
def test_normalize_npm_source_demotes_registry_tarball_of_mismatched_version():
    entry = {
        "version": "1.3.0",
        "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-9.9.9.tgz",
    }
    assert mod._normalize_npm_source("left-pad", entry) == "unknown"


def test_parse_npm_lock_detects_version_axis_tarball_swap():
    def _lock(resolved):
        return json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "node_modules/left-pad": {"version": "1.3.0", "resolved": resolved},
                },
            }
        )

    base = mod.parse_npm_lock(_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz"))
    head = mod.parse_npm_lock(_lock("https://registry.npmjs.org/left-pad/-/left-pad-9.9.9.tgz"))
    changed = mod.diff_changed_packages(base, head)
    assert changed == {"npm:left-pad@1.3.0": ("registry", "unknown")}
    findings = mod.classify(changed, {}, _never_called, NOW)
    assert [(f.key, f.reason) for f in findings] == [("npm:left-pad@1.3.0", mod.REASON_EXOTIC)]


# --- SC-2: 실 lockfile의 registry 항목 전부가 정본 tarball 형태라 이 강화가 오탐을 만들지 않는다 ---
def test_real_package_lock_registry_entries_stay_registry():
    text = (_REPO_ROOT / mod.NPM_LOCK_PATH).read_text(encoding="utf-8")
    entries = json.loads(text)["packages"]
    checked = 0
    for path, entry in entries.items():
        if "node_modules/" not in path:
            continue
        name = path.rsplit("node_modules/", 1)[1]
        if not name or not str(entry.get("resolved", "")).startswith(mod.NPM_REGISTRY_PREFIX):
            continue
        checked += 1
        assert mod._normalize_npm_source(name, entry) == "registry", path
    assert checked > 0


# --- H2: 스코프 패키지의 실제 resolved 형태(tarball은 스코프를 뺀 이름)는 registry로 남는다 ---
def test_normalize_npm_source_accepts_real_scoped_tarball_shape():
    entry = {
        "version": "7.26.2",
        "resolved": "https://registry.npmjs.org/@babel/code-frame/-/code-frame-7.26.2.tgz",
    }
    assert mod._normalize_npm_source("@babel/code-frame", entry) == "registry"


# --- H4 ⑤: read_base_lockfile을 스텁 없이 실제 호출 — 없는 ref는 fail-closed ---
# `git show` 종료코드 검사를 제거하는 뮤턴트를 죽인다(빈 stdout이 조용히 "빈 lockfile"이 된다).
def test_read_base_lockfile_fails_closed_for_missing_ref():
    with pytest.raises(mod.LockfileFormatError, match="base lockfile"):
        mod.read_base_lockfile(
            _REPO_ROOT, "definitely-not-a-real-ref-0000", mod.BACKEND_UV_LOCK_PATH
        )


# --- H4 ⑤: 존재하는 ref는 실제 lockfile 본문을 돌려준다(HEAD 기준) ---
def test_read_base_lockfile_returns_content_for_existing_ref():
    text = mod.read_base_lockfile(_REPO_ROOT, "HEAD", mod.BACKEND_UV_LOCK_PATH)
    assert text.startswith("version = 1")


# --- L2: head 경로가 디렉토리면 IsADirectoryError가 아니라 fail-closed 에러 ---
def test_read_head_lockfile_rejects_directory_path(tmp_path):
    (tmp_path / mod.BACKEND_UV_LOCK_PATH).mkdir(parents=True)
    with pytest.raises(mod.LockfileFormatError, match="head lockfile"):
        mod.read_head_lockfile(tmp_path, mod.BACKEND_UV_LOCK_PATH)


# --- M4: ::error 애노테이션은 요약 1줄이고 다줄 상세는 별도 출력으로 보존된다 ---
def test_main_error_annotation_is_single_line_with_details_preserved(monkeypatch, capsys):
    def _boom(root, base_ref, path):
        raise mod.LockfileFormatError("요약 한 줄\n상세 둘째 줄\n상세 셋째 줄")

    monkeypatch.setattr(mod, "read_base_lockfile", _boom)
    monkeypatch.setattr(mod, "read_head_lockfile", lambda root, path: _UV_MIN)
    assert mod.main(["--base", "deadbeef"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "::error title=supply-chain gate::요약 한 줄"
    assert "상세 셋째 줄" in "\n".join(lines[1:])


# --- M1: --base가 빈 문자열이면 `git show ":<path>"`가 인덱스를 읽고 exit 0을 내
# base == head → 검사 대상 0건 → silent green이 된다. fail-closed로 명시 거부한다. ---
def test_main_rejects_empty_base(capsys):
    assert mod.main(["--base", ""]) == 1
    assert "--base" in capsys.readouterr().out


# --- M1: 공백만 있는 --base도 같은 이유로 거부한다 ---
def test_main_rejects_whitespace_only_base(capsys):
    assert mod.main(["--base", "   "]) == 1
    assert "--base" in capsys.readouterr().out


_UV_MIN_ML = (
    _UV_MIN
    + """
[[package]]
name = "mlbrandnew"
version = "8.8.8"
source = { registry = "https://pypi.org/simple" }
"""
)


# --- ml 축(apps/invoice-ocr/ml/uv.lock)이 LOCKFILES에서 빠져도 어떤 테스트도 깨지지 않는
# 사각을 막는다(donboksa T-2가 npm 축에 건 고정의 ml 대응물). ---
def test_main_blocks_new_package_from_ml_lockfile(monkeypatch, capsys):
    _wire_lockfiles(monkeypatch, _UV_MIN, _UV_MIN, ml_base=_UV_MIN, ml_head=_UV_MIN_ML)
    monkeypatch.setattr(mod, "fetch_publish_time", lambda eco, name, version: datetime.now(UTC))
    assert mod.main(["--base", "deadbeef"]) == 1
    out = capsys.readouterr().out
    assert "pypi:mlbrandnew@8.8.8" in out
    assert "fresh" in out


# --- 검사 대상 lockfile 3축과 파서 결선을 계약으로 고정한다 ---
def test_lockfiles_constant_covers_all_three_axes():
    assert [path for path, _ in mod.LOCKFILES] == [
        "apps/invoice-ocr/backend/uv.lock",
        "apps/invoice-ocr/ml/uv.lock",
        "apps/invoice-ocr/frontend/package-lock.json",
    ]
    assert [parse for _, parse in mod.LOCKFILES] == [
        mod.parse_uv_lock,
        mod.parse_uv_lock,
        mod.parse_npm_lock,
    ]


_UV_SHARED_GIT = (
    _UV_MIN
    + """
[[package]]
name = "sharedpkg"
version = "1.0.0"
source = { git = "https://evil.example/sharedpkg.git#deadbeef" }
"""
)
_UV_SHARED_REGISTRY = (
    _UV_MIN
    + """
[[package]]
name = "sharedpkg"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
"""
)
_UV_SHARED_EDITABLE = (
    _UV_MIN
    + """
[[package]]
name = "sharedpkg"
version = "1.0.0"
source = { editable = "." }
"""
)


# --- ml 축의 registry 추가가 backend 축의 exotic(registry→git) 판정을 덮으면
# 게이트가 조용히 통과한다(fail-open). 무거운 판정이 살아남아야 한다. ---
def test_ml_axis_does_not_mask_backend_exotic_on_shared_key(monkeypatch, capsys):
    _wire_lockfiles(
        monkeypatch,
        _UV_SHARED_REGISTRY,
        _UV_SHARED_GIT,
        ml_base=_UV_MIN,
        ml_head=_UV_SHARED_REGISTRY,
    )
    monkeypatch.setattr(
        mod,
        "fetch_publish_time",
        lambda eco, name, version: datetime.now(UTC) - timedelta(days=400),
    )
    assert mod.main(["--base", "deadbeef"]) == 1
    out = capsys.readouterr().out
    assert "pypi:sharedpkg@1.0.0" in out
    assert "exotic" in out


# --- 반대 방향도 막는다 — ml 축의 local(editable)이 backend 축의 registry 신규 추가를
# 덮으면 classify가 통째로 건너뛴다. ---
def test_ml_axis_does_not_mask_backend_registry_with_local(monkeypatch, capsys):
    _wire_lockfiles(
        monkeypatch,
        _UV_MIN,
        _UV_SHARED_REGISTRY,
        ml_base=_UV_MIN,
        ml_head=_UV_SHARED_EDITABLE,
    )
    monkeypatch.setattr(mod, "fetch_publish_time", lambda eco, name, version: datetime.now(UTC))
    assert mod.main(["--base", "deadbeef"]) == 1
    out = capsys.readouterr().out
    assert "pypi:sharedpkg@1.0.0" in out
    assert "fresh" in out
