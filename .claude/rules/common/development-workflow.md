---
paths:
  - "**/*"
---

# Development Workflow

> This file describes the full feature development pipeline: research, planning, TDD, code review, and committing/PR.

The Feature Implementation Workflow describes the development pipeline: research, planning, TDD, code review, and then committing to git.

## Feature Implementation Workflow

0. **Research & Reuse** _(mandatory before any new implementation)_
   - **GitHub code search first:** Run `gh search repos` and `gh search code` to find existing implementations, templates, and patterns before writing anything new.
   - **Library docs second:** Use Context7 or primary vendor docs to confirm API behavior, package usage, and version-specific details before implementing.
   - **Tavily only when the first two are insufficient:** Use the Tavily MCP (`mcp__claude_ai_Tavily__*`: `tavily_search`, `tavily_extract`, `tavily_crawl`, `tavily_map`, `tavily_research`) for broader web research or discovery after GitHub search and primary docs.
   - **Check package registries:** Search npm, PyPI, crates.io, and other registries before writing utility code. Prefer battle-tested libraries over hand-rolled solutions.
   - **Search for adaptable implementations:** Look for open-source projects that solve 80%+ of the problem and can be forked, ported, or wrapped.
   - Prefer adopting or porting a proven approach over writing net-new code when it meets the requirement.

1. **Plan First**
   - Use **planner** agent to create implementation plan
   - Generate planning docs before coding: PRD, architecture, system_design, tech_doc, task_list
   - Identify dependencies and risks
   - Break down into phases

2. **TDD Approach**
   - Use **tdd-guide** agent
   - Write tests first (RED)
   - Implement to pass tests (GREEN)
   - Refactor (IMPROVE)
   - Verify 80%+ coverage

3. **Code Review**
   - Use **code-reviewer** agent immediately after writing code
   - Address CRITICAL and HIGH issues
   - Fix MEDIUM issues when possible

4. **Commit & Push**
   - 단일 논리 단위로 묶어 commit
   - NXN commit convention: `type(scope): 한국어 설명` (conventional commits)
   - 자동화: **nxn-commit** 스킬 사용 (변경 분석 + 메시지 작성 + 단일 커밋)
   - 브랜치: feature는 `devel`로 squash merge, `devel`은 `main`으로 merge commit

5. **Pull Request**
   - PR 생성 + 이슈 매핑 + 리뷰 흐름: **nxn-pr** 스킬 사용
   - PR 제목은 짧게(<70자), 본문에 Summary + Test plan
   - 모든 자동 체크(CI/CD) 통과 + 머지 충돌 해결 + 베이스 브랜치 최신화 후에만 리뷰 요청
