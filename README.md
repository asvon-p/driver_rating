# Driver Rating

운전석 MP4 영상 한 편을 올리면 팀의 ResNet50 5개가 각 표본을 분류한다. 결과 화면에서 안전도, 판정률, 자세별 시간·감점, 미판정 사유, 시간대별 자세를 확인할 수 있다.

## 로컬 실행

Python 3.12의 `driver_rating` conda 환경을 활성화한 PowerShell에서 프로젝트 폴더로 이동한다. 처음 설치할 때 이 프로젝트와 같은 NVIDIA CUDA 12.8 빌드를 사용하려면 다음 명령을 실행한다.

```powershell
conda activate driver_rating
python -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
```

CPU에서만 사용할 경우 첫 번째 pip 명령의 인덱스를 `https://download.pytorch.org/whl/cpu`로 바꾼다. 이미 설치된 환경에서는 아래 명령으로 서버를 시작하고 브라우저에서 [로컬 서비스](http://127.0.0.1:8017/)를 연다. API와 모델 워커 모두 활성 환경의 Python을 사용한다.

```powershell
.\start.ps1
```

`분석 준비 완료`가 표시되면 영상을 선택하고 **안전도 분석하기**를 누른다. 업로드한 브라우저에서 새로고침하면 마지막 분석을 다시 불러온다. 포그라운드 실행은 Ctrl+C로, 백그라운드 실행은 같은 폴더의 다른 PowerShell에서 `.\start.ps1 -Stop`으로 종료한다. `-Port 8018`처럼 HTTP 포트를 변경할 수 있다. 한 프로젝트에서는 서버 한 개를 실행한다.

PowerShell에서 스크립트 실행이 제한된 경우 활성 환경에서 `python -B -X utf8 run.py`로 시작하고, 같은 명령에 `--stop`을 붙여 종료할 수 있다.

**입력 조건**은 H.264 MP4, 최대 3분, 100 MB, 1920×1080(세로 영상 포함)이다. 고정 카메라로 한 운전자의 상체와 양손이 보이는 영상을 사용한다. 개발 PC에서 사용한 로컬 시연 파일은 `.runtime/demo/driver-demo.mp4`다. 저장소를 새로 받은 경우 직접 준비한 영상을 사용한다. 기존 팀 학습 사진으로 만든 20초 클립이며 일반 운전 12초, 오른손 문자 입력 4초, 검은 화면 4초로 구성했다. 이 PC에서 검증한 결과는 75.0점, 판정률 80.0%다.

## 분석 방식

- 영상 트랙의 재생 타임스탬프를 0.5초 구간으로 나누고, 각 구간의 중앙에 가장 가까운 프레임을 선택한다. 마지막 구간은 실제 길이만 반영한다. 프레임이 없는 구간은 제외한다.
- 디스플레이 회전을 적용한 RGB 이미지를 팀 검증 코드와 같은 224×224 직접 리사이즈 및 ImageNet 정규화로 처리한다. 워커가 `fold_0`~`fold_4`의 `best_model.pt`를 한 번씩 읽는다. 각 모델의 10종 softmax 확률을 동일 가중치로 평균한 뒤 최댓값을 선택한다.
- 어둡거나 대비·선명도가 낮은 화면, 최고 앙상블 확률이 0.70 미만인 구간은 미판정이다. 이 방식에서는 도로 전방, 다른 사람, 학습하지 않은 행동을 높은 신뢰도로 잘못 분류할 수 있다. 일반 운전 c0는 운전자의 자세에 관한 분류다.
- 안전도 = 100 − Σ(자세별 인정 시간 × 위험도) / 총 인정 시간. 판정률 = 총 인정 시간 / 전체 영상 시간 × 100. 총 인정 시간이 10초 미만이거나 판정률이 70% 미만이면 점수는 `null`이고 판정률과 이유를 표시한다. 조건은 반올림 전 값으로 검사한다.
- 위험도는 c0~c9 순서로 **0, 100, 70, 100, 70, 40, 50, 90, 80, 30**이다. 위험도와 보류 기준은 시연용 정책이다. `driver/scoring.py`의 정책 설정이 바뀌면 정책 식별자가 달라진다.

## 모델 변경

기본 모델 설정은 `models/team-resnet50.json`이다. `root`는 이 JSON의 위치를 기준으로 한 경로 또는 절대 경로다. 기본 설정은 프로젝트와 나란히 있는 `deep_learning_team/5_fold/team_folds/results/baseline`을 읽기 전용으로 참조한다. 체크포인트는 별도로 준비해 `fold_0`부터 `fold_4`까지 각각의 `best_model.pt`를 두거나, 로컬 모델 설정에서 경로를 지정한다. 프로젝트가 체크포인트나 Kaggle 사진을 원래 폴더에 쓰지 않는다.

1. 같은 ResNet50 구조로 교체할 때는 `models`에 새 JSON을 만들고 `name`, `root`, `checkpoints`, `image_size`, `mean`, `std`, `temperature`를 해당 학습 설정에 맞춘다. `checkpoints`에 파일을 하나 넣으면 단일 모델, 여러 개 넣으면 동일 가중치 확률 평균을 사용한다. 기본 로더는 `model_state`와 10개 출력의 `fc`를 요구한다.
2. 다른 CNN이면 `driver` 아래에 `__init__(manifest, paths)`와 `predict_batch(images)`를 가진 어댑터를 작성한다. RGB PIL 이미지 목록을 받아 **N×10 확률 배열**을 c0~c9 순서로 반환한다. JSON의 `adapter`를 해당 `모듈:클래스`로 지정한다. 코드와 설정은 운영자가 관리하는 로컬 파일에서만 로드한다.
3. 기존 서버를 종료한 후 아래처럼 모델을 선택해 시작한다. 로더는 체크포인트의 키와 출력 확률을 검사한다. 준비가 실패하면 UI가 업로드를 막고 `.runtime/logs/worker.log`에 원인을 남긴다. 모델을 바꾼 후에는 고정 영상의 자세별 시간과 미판정 비율을 다시 확인한다.

```powershell
$env:DRIVER_MODEL_CONFIG = (Resolve-Path ".\models\team-resnet50.json").Path
.\start.ps1
```

결과에는 설정과 체크포인트 내용에서 계산한 모델 식별자, 실행 장치, 점수 정책 식별자가 함께 저장된다. 기본 장치 선택은 CUDA 우선이며 CPU에서 실행하려면 시작 전에 `$env:DRIVER_DEVICE = "cpu"`를 지정한다.

## 서버와 저장 위치

FastAPI는 업로드와 결과 조회를 맡고, 별도 워커 프로세스가 PostgreSQL 작업 큐에서 영상을 가져간다. 워커는 구간과 점수를 한 트랜잭션으로 저장한다. 실행 중 중단된 작업은 다음 워커가 한 차례 재시도한다. 요청 브라우저의 세션 쿠키 지문으로 결과 조회를 제한한다.

| 경로 또는 포트 | 용도 |
| --- | --- |
| `127.0.0.1:8017` | 웹 UI와 FastAPI. `GET /health`에서 현재 모델 준비 상태를 확인한다. |
| `127.0.0.1:15432` | 프로젝트 전용 PostgreSQL 17.12. 인증 정보는 `.runtime/db-password`에 로컬 보관한다. |
| `.runtime/pgdata` | 분석 상태, 결과, 시간대별 구간, 정책 버전. 보관 기간 7일. |
| `.runtime/uploads`, `.runtime/tmp` | 업로드·디코딩 임시 파일. 종료 후 원본을 삭제하며, 워커가 기동 중이면 중단된 원본도 24시간을 기준으로 정리한다. 프레임은 메모리에서 처리한다. |
| `.runtime/postgres`, `.runtime/logs` | 로컬 PostgreSQL 실행 파일과 워커·DB 로그. Python 라이브러리는 활성 conda 환경에 설치한다. |

`POST /analyses`는 `video` multipart 파일을 받아 202와 분석 ID를 반환한다. `GET /analyses/{id}`에서 대기·처리·완료·보류·실패 상태를 확인한다. `GET /analyses/{id}/result`는 점수, 판정률(%), 구간 시각(ms), 자세별 시간(초), 제외 사유를 반환한다. 상태와 결과 조회에는 업로드 세션 쿠키를 함께 보낸다.

Windows x64에서 최초 실행 시 Maven Central의 [PostgreSQL 배포본](https://repo.maven.apache.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/17.12.0/)을 프로젝트 안에 내려받아 SHA-256을 검증한다. 이후 같은 환경과 체크포인트로 오프라인에서 다시 시작할 수 있다. 기존 PostgreSQL을 사용할 경우 전용 빈 DB의 연결 문자열을 `DRIVER_DATABASE_URL`에 설정하고 해당 Python에서 사용할 수 있는 libpq 또는 psycopg 바이너리 드라이버를 준비한다.

## 검증

프로젝트 폴더에서 `python -B -m unittest discover -s tests -v`를 실행한다. 계획서의 73.3점 예시, 0점과 보류의 구분, 판정 기준 경계, 가변 프레임 간격·공백·마지막 구간 처리를 검증한다.

현재 기본 체크포인트를 팀 제출 파일의 첫 64장과 동일한 배치 크기로 대조했을 때 10개 클래스 확률의 최대 절대 차이는 5×10⁻¹¹ 미만이었다. GPU의 배치 크기에 따라 부동소수점 연산 결과에는 미세한 차이가 있다. 시연 영상과 이 대조는 연결 검증에 사용했다.
