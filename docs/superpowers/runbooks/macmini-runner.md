# macmini self-hosted 러너 등록 — sjmj-ai

deploy.yml(`runs-on: [self-hosted, macmini]`)이 동작하려면 GangsubLim/sjmj-ai repo에
러너가 online 이어야 한다. (운영 클론 /Users/submini/sjmj-ai 는 Phase 1D에서 존재.)

## 1. 등록 토큰 발급 (개발 머신, gh 인증 GangsubLim)
gh api -X POST repos/GangsubLim/sjmj-ai/actions/runners/registration-token --jq .token

## 2. macmini에서 러너 디렉터리 구성 (SSH)
#   augron/donboksa 러너와 별개 디렉터리. actions-runner 최신 릴리스 사용.
ssh submini@macmini
mkdir -p ~/actions-runner-sjmj-ai && cd ~/actions-runner-sjmj-ai
# (augron/donboksa 러너 디렉터리의 동일 버전 tar 재사용 가능)
./config.sh --url https://github.com/GangsubLim/sjmj-ai \
  --token <위 토큰> --labels self-hosted,macmini --name macmini-sjmj-ai --unattended

## 3. 서비스 등록 + 시작
./svc.sh install
./svc.sh start

## 4. online 확인 (개발 머신)
gh api repos/GangsubLim/sjmj-ai/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
# status == "online", labels에 self-hosted,macmini 포함 확인.
