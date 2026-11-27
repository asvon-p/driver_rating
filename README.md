# Driver Rating

관리자가 사진 묶음을 오래된 관찰부터 정렬하면 모델 A·B가 같은 사진을 분류한다. 사진별 안전점수와 근거, 최근 관찰을 더 반영하는 EMA, 모델별 Grad-CAM을 비교하고 발표용 사례를 다시 열 수 있다.

## 가상환경 준비

처음 받는 Windows PC에서는 PowerShell에서 설치할 상위 폴더로 이동한 뒤 `develop` 브랜치를 복제한다.

```powershell
git clone --branch develop https://github.com/asvon-p/driver_rating.git
Set-Location driver_rating
```

`driver_rating` 환경이 없으면 `conda create -n driver_rating python=3.12`로 만든다. 프로젝트 폴더에서 다음 명령을 실행해 CUDA 12.8용 라이브러리를 설치한다.

```powershell
conda activate driver_rating
python -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
```

CPU에서만 사용할 경우 첫 번째 pip 명령의 인덱스를 `https://download.pytorch.org/whl/cpu`로 바꾼다.

## 로컬·시연 서버 실행 및 중지

호스트 PC에서 사용할 모드를 골라 PowerShell 창 하나에서 실행한다. 기본 모드는 로컬 접속용이다. 공개 시연을 선택하면 분석 서버가 준비된 뒤 이 실행에 한해 Tailscale Funnel의 HTTPS 10000 포트를 연다.

| 구분 | 접속 주소 | 호스트 PC에서 실행 | 호스트 PC에서 중지 |
| --- | --- | --- | --- |
| 로컬 분석 서버 | [http://127.0.0.1:8017/](http://127.0.0.1:8017/) | `.\start.ps1` | 실행 창에서 `Ctrl+C` 또는 프로젝트 폴더의 다른 PowerShell에서 `.\start.ps1 -Stop` |
| 공개 시연 서버 | [https://sgy-host.taile39b25.ts.net:10000/](https://sgy-host.taile39b25.ts.net:10000/) | `.\start.ps1 -Demo` | 실행 창에서 `Ctrl+C` 또는 다른 PowerShell에서 `.\start.ps1 -Stop` |

### 1. 호스트 PC에서 분석 서버 시작

첫 실행 전 팀에서 별도로 받은 A·B 체크포인트를 [모델 변경](#모델-변경)에 설명된 경로에 준비한다. 아래 경로와 표의 공개 주소는 현재 시연 호스트 기준이다. 다른 PC에서는 실제 복제 경로를 사용하고, 공개 주소는 그 PC에서 `tailscale funnel status`로 확인한다.

첫 번째 PowerShell에서 다음 명령을 실행하고 창을 열어 둔다. `driver_rating` 환경이 이미 활성화되어 있으면 `conda activate`는 생략한다.

```powershell
Set-Location 'C:\Users\user\Desktop\vscode setting\driver_rating'
conda activate driver_rating
.\start.ps1
```

공개 시연을 하려면 위 마지막 줄을 `.\start.ps1 -Demo`로 바꾼다. 이미 실행 중인 서버의 모드를 바꾸려면 먼저 종료한다. 새 분석 화면에서 모델 상태를 확인한다. PowerShell 스크립트 실행이 제한된 환경에서는 `python -B -X utf8 run.py`(로컬), `python -B -X utf8 run.py --demo`(시연), `python -B -X utf8 run.py --stop`(종료)을 사용한다. `-Port 8018`처럼 로컬 포트를 지정하면 공개 연결도 해당 포트를 사용한다.

### 2. 시연 주소로 접속

호스트 PC의 두 번째 PowerShell에서 공개 주소와 대상 포트를 확인한다.

```powershell
tailscale funnel status
```

학교 컴퓨터에서 시연 주소를 열면 **새 사진 묶음 분석** 화면이 바로 뜬다. **저장된 결과**에서는 발표용으로 고정한 사례와 같은 브라우저에서 올린 사진을 다시 열 수 있다. 시연 중에는 호스트 PC의 서버와 Tailscale을 실행한다.

### 3. 시연 종료

실행 창에서 `Ctrl+C`를 누르거나 프로젝트 폴더의 다른 PowerShell에서 `.\start.ps1 -Stop`을 실행한다. 실행기가 이번 시연의 공개 연결, 모델 워커, 직접 시작한 PostgreSQL을 함께 정리한다. `tailscale funnel status`에서 10000 포트 연결 종료를 확인할 수 있다.

## 사진 묶음 분석

1. JPG·PNG를 최대 24장 선택하고 **↑ / ↓**로 오래된 사진부터 배열한다. 사진당 10 MiB·2,000만 픽셀, 묶음 전송량 48 MiB까지 지원한다. 카메라 시점·출처·검수 정답을 기록하거나 운전자 없음·10종 외 행동을 제외할 수 있다.
2. **분석 실행**을 누른다. 두 카드는 독립적으로 유효 사진 수, 판정률, EMA, 반복된 자세와 최근 관찰을 보여 준다. 시연에서 여러 사람을 섞는 경우도 하나의 가상 운전자로 취급한다.
3. 그래프나 썸네일에서 사진을 선택한다. **점수 계산 보기**는 예측 클래스·위험도·신뢰도와 검수 정답이 있을 때 절대오차를 보여 준다. **Grad-CAM 보기**는 해당 모델의 목표 클래스에 대한 히트맵을 생성하고 저장한다. **선택 사진의 양쪽 Grad-CAM 저장**으로 발표 전 양쪽을 준비할 수 있다.
4. **발표용 고정**을 누르면 사례가 목록 상단에 표시된다. 일반 사례는 7일간 보관하고, 고정 해제 시 그때부터 다시 7일간 보관한다. 고정한 묶음은 시연 주소를 연 사람에게 공개된다. 시연에서 고정·삭제는 사진을 올린 브라우저에서 할 수 있다. 호스트가 로컬 모드(`.\start.ps1`)로 실행하면 기존 묶음도 모두 관리할 수 있다. 묶음을 삭제하면 사진·결과·히트맵이 함께 삭제된다.

사진 점수는 **100 − 예측 자세 위험도**다. 안전 운전은 85점 이상, 일반 운전은 60점 이상 85점 미만, 위험 운전은 60점 미만이다. 화질 기준 미달 또는 모델 신뢰도 70% 미만이면 보류한다. 각 모델의 EMA는 유효 사진만 대상으로 최근부터 1, 0.7, 0.7², …의 상대 가중치를 준다. 한 장은 순간 판단만, 두 장은 잠정 경향으로 표시한다. 두 모델 사이의 묶음 점수 차이는 공통 판정 사진이 두 장 이상일 때 그 사진들로 다시 계산한다. [점수와 발표 계획](IMAGE_COMPARISON_SERVICE_PLAN.md)

## 모델 변경

현재 서비스의 임시 조합은 다음과 같다. 두 모델의 차이로 증강 학습 효과를 단정할 수 없다.

| 역할 | 설정 | 기본 가중치 경로 | 추론 |
| --- | --- | --- | --- |
| A · R10KF | `models/comparison-a.json` | `.runtime/models/A/R10KF_fold0_best.pt` ~ `R10KF_fold4_best.pt` | 384×384, 원본·415 중앙 크롭, fold 내부 평균 로짓 → softmax → 5개 fold 평균 |
| B · 기존 baseline | `models/comparison-b.json` | `.runtime/models/B/fold_0/best_model.pt` ~ `fold_4/best_model.pt` | 224×224, fold별 softmax 후 5개 확률 평균 |

새로 복제한 PC에는 체크포인트를 별도로 배치한다. 확정 모델을 받을 때는 해당 JSON의 `name`, `description`, `provisional`, `architecture`, `root`, `checkpoints`, 입력 크기·정규화·TTA를 실제 학습 설정과 맞춘다. R10은 순수 state dict, baseline은 `model_state`를 포함한 체크포인트를 사용한다. 다른 구조는 `predict_batch(images)`와 `heatmap(image, target)`를 제공하는 운영자 관리 어댑터를 등록한다. 서버를 재시작하면 내용 해시로 새 버전을 구분한다. 모델 하나만 준비된 경우에도 해당 모델로 분석할 수 있다.

다른 설정 파일을 선택하려면 서버 시작 전에 역할별 변수를 지정한다. CPU 추론은 `$env:DRIVER_DEVICE = "cpu"`로 선택한다.

```powershell
$env:DRIVER_MODEL_A_CONFIG = (Resolve-Path ".\models\comparison-a.json").Path
$env:DRIVER_MODEL_B_CONFIG = (Resolve-Path ".\models\comparison-b.json").Path
.\start.ps1
```

## 저장과 검증

FastAPI와 전용 워커가 PostgreSQL 작업 큐를 사용한다. 원본 방향을 보정해 메타데이터를 제거한 RGB 사진, 썸네일, 모델 버전·정책·순서와 결과, 생성한 히트맵을 같은 DB에 보관한다. 로컬 PostgreSQL 데이터는 `.runtime/pgdata`, 워커 로그는 `.runtime/logs/worker.log`에 있다. 외부 PostgreSQL을 지정한 경우 사진 자료도 그 DB에 보관된다. 중단 후에는 완료된 사진 결과를 재사용하고 확정 순번으로 EMA를 복원한다. 모델 교체 후에도 기존 스냅샷·캐시된 히트맵은 그대로 조회하며 새 히트맵에는 당시 모델 버전이 필요하다.

프로젝트 폴더에서 `python -B -m unittest discover -s tests -v`로 점수 경계, 순서 반전, 보류와 공통 관찰 처리 및 수동 실행 동작을 검사한다.
