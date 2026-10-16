# WeFarm Coffee Game 도헌 제작 명세

> **도헌이 개발 시작용 명세 v0.1 — RoboCasa Kitchen24 Coffee Game.** 목표는 RLWRLD의 공개 학습·평가 환경과 로봇·작업·관측·행동 규격을 맞춘 브라우저 조작 게임을 만들고, 사람이 만든 시연을 재생·학습 가능한 파일로 내보내는 것이다. 문서의 제작 범위·키 배치·납품 규격은 제안이며, 아래 출처로 확인된 원본 동작과 구분한다.
> 작성: 2026-10-16. 담당: 게임·기록기 구현은 도헌, 고객 요구사항·데이터 수요 확인과 정책 성능 실험 설계는 세혁과 협의. 이 문서는 제작 명세이며 구현 완료 보고가 아니다.

## 1. 처음 만들 결과물과 범위

**첫 납품:** 사용자가 브라우저에서 PandaOmron을 조작해 커피 작업 3개를 각각 수행하고, 성공/중단 에피소드를 저장·제출·재생하며, 동일 기록에서 LeRobot 학습용 데이터를 추출할 수 있는 로컬 데모.

| 이번 버전 필수 | 다음 단계 |
| --- | --- |
| 원본 CoffeePressButton / CoffeeSetupMug / CoffeeServeMug | 공개 AI 정책의 실패 상태를 사람이 이어받는 Recovery Mode |
| 영어 UI, 조작 도움말, 연습·수집 모드, 시작·일시정지·제출·다시하기 | 여러 사용자별 시뮬레이터 격리와 배포 |
| 20Hz 관측·행동 기록, 3개 학습 카메라, raw replay와 LeRobot export | 마켓플레이스 주문/QA 상태 연동, AI Player 연결 |
| 기록 검증·재생·학습 로더로 샘플 읽기 | 기존 정책 대비 추가학습 성능 비교 |

커피 세 작업은 **독립 task_id와 episode_id**를 갖는다. 화면에서 순차 미션으로 보여줘도 각 작업마다 원본 환경을 reset하고 별도 저장한다. 컵 이동 성공은 원본 시뮬레이터의 성공 함수로 판정한다. 컵을 손에 붙이기·순간이동·자동 경로 보조 등 물리를 바꾸는 기능을 넣으면 별도 assisted 데이터로 구분한다.

회사 적합성 근거: RLWRLD가 이 Kitchen24와 PandaOmron의 모델·평가·학습 코드를 공개했다. 게임 데이터의 실제 학습 효과와 회사의 구매 의사는 아직 검증되지 않았다. [공식 평가 설명](https://github.com/RLWRLD/RLDX-1/blob/main/run_scripts/eval/robocasa_kitchen/README.md)

## 2. 사용할 공개 소스와 버전

| 소스 | 사용 목적 | 개발 시 주의 |
| --- | --- | --- |
| [RLWRLD/RLDX-1](https://github.com/RLWRLD/RLDX-1) | Kitchen24 환경·로봇 wrapper·학습 데이터 설정을 기준으로 사용 | 선택한 Git commit을 기록하고 고정 |
| [동봉된 RoboCasa](https://github.com/RLWRLD/RLDX-1/tree/main/external_dependencies/robocasa) | 원본 24-task의 장면·물체·성공 판정 | 최신 RoboCasa365와 섞지 않는다. submodule/gitlink 또는 동봉 코드 revision 확인 |
| [공식 환경 설치 스크립트](https://github.com/RLWRLD/RLDX-1/blob/main/rldx/eval/sim/robocasa/setup_RoboCasa.sh) | Python MuJoCo 환경 구성 출발점 | robosuite master를 가져오는 부분이 있어 실제 설치된 SHA를 별도로 고정 |
| [공개 체크포인트](https://huggingface.co/RLWRLD/RLDX-1-FT-ROBOCASA) | 후속 AI baseline 평가 | 게임 수동 플레이부터 만들 수 있다. 모델 실행·학습은 별도 GPU 작업 |
| [공개 시연 파일](https://huggingface.co/datasets/kimtaey/robocasa_mg_gr00t_300) | 입출력 schema·샘플 로더 검증 | 24개 task × 300개 = 7,200 episodes, 2,073,457 frames, 20Hz. 개인 업로더 자료이며 RLWRLD 학습 원본과 동일한지·원본 출처·권리는 미확인 |
| [RoboCasa 수집 가이드](https://robocasa.ai/docs/build/html/use_cases/creating_datasets.html) | raw 기록 → 영상 추출 → LeRobot 변환 참고 | 현재 가이드는 최신 버전이므로 Kitchen24와의 호환성은 샘플로 검증 |

**버전 manifest 필수:** RLDX commit, RoboCasa revision, robosuite SHA, MuJoCo/Python/package lock, asset 경로·hash, task/controller 설정, 카메라 이름·변환, exporter 버전. 설치 후 검증된 조합을 재사용한다.

공개 데이터의 이름에 300이 들어가지만 전체 300개라는 뜻은 아니다. 실제 episodes 기준 24종 × 300개이며 사람/합성 구성은 확정하지 않는다. metadata의 total_tasks=365는 지시문 등 annotation vocabulary와 관련되어 365개 환경으로 해석하면 안 된다. 일부 summary/split 값은 실제 episode 목록과 불일치하므로 실제 파일·episode manifest로 검증한다. [info](https://huggingface.co/datasets/kimtaey/robocasa_mg_gr00t_300/blob/main/meta/info.json) · [modality](https://huggingface.co/datasets/kimtaey/robocasa_mg_gr00t_300/blob/main/meta/modality.json)

**권리 확인:** 공개 RLDX 모델은 비상업 RLWRLD Model License v1.0이다. 연구용 baseline과 영리 서비스 사용을 구분하고, 배포 전에 선택 코드·자산·데이터·모델별 조건을 확인한다. 위 개인 업로더 데이터는 공개 접근만 확인됐으므로 사업용 재배포 허용으로 해석하지 않는다. [모델 이용 조건](https://huggingface.co/RLWRLD/RLDX-1-FT-ROBOCASA#license)

## 3. 권장 실행 구조와 기존 코드 활용

기존 marketplace/agent 연동을 수정할 때는 저장소 AGENTS.md, openroture_QA.md, crusoe_player.md를 따른다. QA 모델은 OpenRouter, AI Player 추론은 Crusoe 역할을 유지한다. 이번 1차 목표는 사람 조작과 데이터 출력이며 기존 구형 Brainbase 경로를 기본 실행 경로로 삼지 않는다.

**제안 구조:** 브라우저 UI → WebSocket 입력 → Python의 원본 RoboCasa/MuJoCo → 플레이 화면 전송. 서버 recorder가 학습 카메라·로봇 상태·행동·월드 상태를 함께 저장 → 검증 → LeRobot export. 원본 task/controller를 유지하는 방향으로 먼저 구현한다.

플레이 화면 렌더 fps와 학습 데이터 20Hz는 분리한다. 서버는 최신 입력 상태를 고정 20Hz에서 읽고 원본 controller action을 생성한다. 네트워크·렌더 지연이 물리 dt나 샘플 간격을 임의로 바꾸지 않게 한다. 처리 속도가 20Hz를 못 따라가면 감속·지연을 명시하고 기록한다.

| 현재 저장소에서 확인한 것 | 이 명세에 맞춰 필요한 변경 |
| --- | --- |
| sim/web/server.py: Python MuJoCo + WebSocket 입력·영상 + HDF5 저장의 참고 구현 | 원본 세 task 선택, canonical 3-camera recorder, 입력·관측·행동 시간 정렬, 세션 잠금/격리 |
| sim/cafe/cafe_env.py: CafeServeCoffee는 컵을 트레이에 놓는 커스텀 두 단계 작업 | 원본 Kitchen24 커피 작업으로 각각 생성. 기존 커스텀 작업을 동일 benchmark로 표시하지 않음 |
| 기존 server의 학습 카메라: center/wrist 2개, 128px, 10Hz | 목표 left/right/wrist 3개, 최종256px, 20Hz로 새 capture/export 구성 |
| 현재 tomato browser recorder: .jsonl.gz 중심의 별도 계약 | 이번 native RoboCasa raw + LeRobot 계약과 task/robot/version을 명시해 구분 |
| RLWRLD 평가 wrapper의 dataset dump hook은 pass로 비어 있음 | 평가 실행 옵션만 켜면 저장된다고 가정하지 말고 실제 recorder를 구현 |

첫 데모는 **활성 플레이어 1명**을 명시하고 중복 접속을 잠그는 방식으로 범위를 줄여도 된다. 여러 사용자에게 같은 mutable simulation을 공유하는 상태를 다중 플레이 지원으로 표시하지 않는다. [원본 wrapper](https://github.com/RLWRLD/RLDX-1/blob/main/external_dependencies/robocasa/robocasa/utils/gym_utils/gymnasium_basic.py)

## 4. 플레이어 작업과 실제 성공 조건

| 작업·용도 | 영어 화면 지시문 제안 | 플레이어 행동 | 원본 성공 판정 |
| --- | --- | --- | --- |
| CoffeePressButton / 조작 튜토리얼 | Activate the coffee machine. | 컵이 놓인 머신의 버튼을 누른 뒤 손을 뺀다 | turned_on이 참 + gripper가 버튼에서 멀어짐 |
| CoffeeSetupMug / 주 수집 작업 | Place the mug below the dispenser. | 카운터 컵을 집어 추출구 아래에 놓고 손을 뺀다 | 머신의 receptacle placement 판정 통과 + gripper가 컵에서 멀어짐 |
| CoffeeServeMug / 주 수집 작업 | Move the mug onto the counter. | 머신 아래 컵을 집어 지정 카운터 위에 놓고 손을 뺀다 | 컵과 지정 counter 접촉 + gripper가 컵에서 멀어짐 |

각 task의 _check_success를 호출해 판정하며, UI의 자체 거리 기준으로 대체하지 않는다. 액체·온도·맛을 시뮬레이션했다고 표시하지 않는다. CoffeeServeMug의 목표는 머신→카운터 이동이다. [원본 커피 task 코드](https://github.com/RLWRLD/RLDX-1/blob/main/external_dependencies/robocasa/robocasa/environments/kitchen/single_stage/kitchen_coffee.py)

**수집·평가 분리:** 연습은 pause/reset을 허용한다. 수집은 전체 episode를 기록하고 success 여부를 보존한다. RLWRLD benchmark 평가는 최대720 action steps(20Hz에서36초)를 사용한다. 사람 시연 모드는 초보 조작을 위해 더 긴 제한을 둘 수 있으나 horizon/mode를 기록하고, 후속 정책 평가는 원본720-step 조건에서 비교한다. [평가 설정](https://github.com/RLWRLD/RLDX-1/blob/main/run_scripts/eval/robocasa_kitchen/eval_robocasa.sh)

## 5. 로봇 사양과 조작 UI 제안

| 항목 | 고정할 구성 |
| --- | --- |
| 로봇 | PandaOmron: Franka Panda 단팔7관절 + PandaGripper 두 손가락 + Omron LD-60 형태 이동 베이스 |
| 그리퍼 | 두 손가락 위치·속도를 관측하지만 개폐 제어 명령은1개 |
| 베이스·몸통 | 전후·좌우·yaw3개 + torso 상하1개. 몸통 joint 범위0–0.34m |
| 팔 제어 | base frame 기준 EEF 위치 delta3 + axis-angle 회전 delta3, 원본 OSC_POSE/controller 설정 유지 |
| 학습 카메라 | 외부left/right + wrist. 원본 wrapper는512×512 렌더 후 flip/resize; 모델·데이터 export는256×256 RGB. 선택한 원본 처리 순서를 그대로 고정 |
| 주기 | 20Hz simulation control/recording. wall-clock과 simulation timestamp를 구분 |

[로봇·행동 mapper](https://github.com/RLWRLD/RLDX-1/blob/main/external_dependencies/robocasa/robocasa/models/robots/__init__.py) · [PandaOmron controller](https://github.com/ARISE-Initiative/robosuite/blob/master/robosuite/controllers/config/robots/default_pandaomron.json) · [Omron 모델](https://github.com/ARISE-Initiative/robosuite/blob/master/robosuite/models/assets/bases/omron_mobile_base.xml)

아래 키 배치는 **개발 제안**이다. 키 입력을 원본 named controller channel로 변환하고, Shift 미세 조작은 controller 입력 크기만 낮춘다.

| 입력 | Arm mode | Base mode |
| --- | --- | --- |
| W / S | 손끝 전진 / 후진 | 베이스 전진 / 후진 |
| A / D | 손끝 좌 / 우 | 베이스 좌 / 우 |
| R / F | 손끝 위 / 아래 | 몸통 위 / 아래 |
| Q / E | 손끝 yaw 회전 | 베이스 yaw 회전 |
| Z / X, C / V | 손끝 pitch, roll | 사용 안 함 |
| Space | 그리퍼 개폐 toggle | 그리퍼 개폐 toggle |
| Tab | Arm/Base mode 전환 | Arm/Base mode 전환 |
| Shift | 미세 조작 | 미세 조작 |
| 마우스 | 플레이 화면 카메라 회전·확대 | 플레이 화면 카메라 회전·확대 |

입력창 또는 화면 밖으로 포커스가 이동하거나 연결이 끊기면 이동 입력을 모두0으로 만들고 자동 pause한다. 카메라를 돌려도 저장하는 학습용 세 카메라의 위치·보정값은 바꾸지 않는다. 입력·자동 보조·UI 힌트가 있으면 provenance에 남긴다.

**화면 필수 요소:** task 선택 카드, 목표 지시문, 조작 도움말, Arm/Base 표시, 시뮬레이션 시간·steps, 손목 PiP, Start Recording / Pause / Resume / Stop & Submit / Retry / Discard. 성공 시 task 완료를 표시하고 저장·제출 상태는 별도로 표시한다.

**상태 흐름:** Ready → Recording ↔ Paused → Completed 또는 Stopped → Submitting → Submitted. 저장 실패는 Upload failed로 표시하고 같은 episode_id로 재시도한다. pause 동안 물리·샘플 기록을 함께 멈추고, 재개 시 입력을 비운다. reset은 이전 episode를 덮어쓰지 않는다.

## 6. 최종 데이터 계약 — 핵심 구현 대상

**매 control step의 순서:** 시점 t의 카메라·로봇 관측·월드 상태 저장 → 실행할 native action과 dataset action 저장 → env.step(action) → 결과 reward/done/success 저장. next 값과 pre-action 관측이 한 행에서 어떤 의미인지 exporter에 명시한다.

| 항목 | 저장 내용·규격 | 필요 이유 |
| --- | --- | --- |
| 영상 | left/right/wrist RGB3개, export256×256,20fps, 동일 timestep/frame_index | 정책의 관측 입력. RGB channel·상하방향·resize 변환 고정 |
| observation.state | 원본53차원 state. base pos/quat, world·base-relative EEF pos/quat, gripper qpos/qvel, arm qpos/cos/sin/qvel | 모델 호환성·추가 분석. 53관절이라는 뜻이 아님 |
| action | 공개 dataset 규격12차원, 아래 ordering과 named mapping metadata | 모방학습의 정답 행동 |
| 시간·ID | episode_id, episode_index, frame_index, timestamp,task_id; wall-clock 별도 | 동기화·중복 방지·재현 |
| 작업 annotation | 원본 task ID, 자연어 지시, task index와 지시문 테이블 | 조건부 정책 학습. 최종 column/key는 선택 로더의 modality와 맞춤 |
| 결과 | reward,done,task_success,end_reason,validity | 성공 시연·실패·중단을 구분 |
| 입력 출처 | human/AI,player의 가명 ID, 입력장치, 개입 시작·종료 frame, assist flag | 누가 어떤 구간을 만들었는지 추적 |
| 재생 sidecar | MJCF/model XML,초기 및 per-step MuJoCo integration state,env_info,ep_meta,seed/layout/style/object/controller/camera/version manifest | 로봇53차원만으로 전체 월드를 재생할 수 없으므로 별도 보존 |
| QA sidecar | 형식/동기화/재생/원본 성공판정 결과,검사 버전·오류·검사 시각 | 파일 검증과 학습 효과 평가를 구분 |

원본 dataset의 flat action ordering은 다음과 같다. 이는 simulator-native action vector ordering과 다를 수 있으므로 원본 converter 및 named action mapping을 이용한다.

```text
action[0:4]   base_motion: 베이스3 + torso1
action[4:5]   control_mode
action[5:8]   end_effector_position: delta3
action[8:11]  end_effector_rotation: axis-angle delta3
action[11:12] gripper_close
```

그리퍼·mode는 데이터0/1과 simulator−1/+1 인코딩 차이를 wrapper에서 처리한다. 키보드 이벤트만 저장하거나 native vector를 위 순서로 오인해 그대로 복사하면 안 된다. normalization 전 원본 값을 보존하고, 단위·좌표계·controller scales를 manifest에 남긴다. [공개 modality](https://huggingface.co/datasets/kimtaey/robocasa_mg_gr00t_300/blob/main/meta/modality.json) · [학습 설정](https://github.com/RLWRLD/RLDX-1/blob/main/rldx/configs/data/robocasa_config.py)

현재 모델은 state53 중 relative EEF pos3/quat4 + gripper qpos2 + base pos3/quat4 =16개를 사용한다. processor는 세 카메라 각각 과거4프레임을 사용하며 action16-step chunk를 구성한다. 게임 recorder는 매 step 원본을 저장하고, chunking·percentile normalization·padding은 모델 loader/processor에 맡긴다. 이 Kitchen 데이터에 tactile/torque 필드가 있다고 가정하지 않는다. [공식 processor](https://huggingface.co/RLWRLD/RLDX-1-FT-ROBOCASA/blob/main/processor/processor_config.json)

**제안 납품 구조:**

```text
coffee_dataset/
  raw/                         # HDF5 등 native actions + pre-action world states
  lerobot/
    data/                      # episode Parquet: state/action/time/labels
    videos/                    # left/right/wrist MP4
    meta/                      # info/modality/tasks/episodes/stats 등 선택 버전 규격
  replay/                      # MJCF, env settings, states, version manifest
  qa/                          # episode 검사·오류·success report
  README.md                    # 실행·재생·변환·schema·출처
```

LeRobot의 최신 버전을 무조건 고르지 말고 RLDX 선택 revision의 dataloader가 읽는 버전에 맞춘다. 최종 제출 전에 실제 샘플을 loader로 열어 카메라3개/state/action/지시문과 chunk 구성이 정상인지 확인한다. [RLDX 학습 문서](https://github.com/RLWRLD/RLDX-1/blob/main/docs/training.md) · [RoboCasa 출력 구조 참고](https://robocasa.ai/docs/build/html/datasets/using_datasets.html)

## 7. 데이터 분리·제출·QA

- **평가 분리:** 회사 wrapper는 object split B 및 특정 layout/style 조합을 평가에 사용한다. 이를 학습 수집에 섞지 않는다. dev/train/held-out eval manifest를 만들고 원본이 지원하는 train split을 확인해 고정한다. 평가용 seed/object/layout 목록을 exporter가 학습 데이터에서 제외하도록 한다.
- **성공·실패 분리:** 원본 success를 통과한 인간 시연을 positive imitation 후보로 저장한다. 실패·user_stop·timeout·reset·disconnect는 보존하되 성공 시연으로 묶지 않는다. Recovery Mode는 AI 구간과 인간 교정 구간의 경계를 저장한다.
- **저장·제출:** episode_id는 생성 후 불변. 서버에서 임시 파일 저장→close→검증→atomic finalize→제출 응답. 같은 ID로 재시도해도 중복 episode를 만들지 않는다. 로컬 원본 저장과 외부 제출 상태를 따로 표시한다.
- **QA 단계:** 파일 존재·읽기 → 필드/shape/finite/time/camera sync → replay → 원본 success. QA 통과는 데이터 무결성과 해당 시뮬레이션 작업 완료를 의미한다. 학습 성능 향상은 별도 정책 평가 결과로 보고한다.
- **재생 검증:** action replay와 저장 state 재생을 구분한다. 선택 버전의 정상 episode에서 qpos/object pose/success 차이 허용치를 먼저 측정하고 검사기에 기록한다. 포괄적인 0오차를 약속하지 않는다.

[원본 평가 wrapper와 split](https://github.com/RLWRLD/RLDX-1/blob/main/external_dependencies/robocasa/robocasa/utils/gym_utils/gymnasium_basic.py) · [시연 replay 참고](https://robosuite.ai/docs/algorithms/demonstrations.html)

## 8. 구현 순서와 완료 기준

| 단계 | 할 일 | 완료 증거 |
| --- | --- | --- |
| 1. 원본 실행·버전 고정 | 환경 설치, 세 커피 task를 직접 reset/step/render, robot/controller/camera와 dependency manifest 고정 | 세 작업 실행 로그·스크린샷, 재설치 가능한 README/lock |
| 2. 한 task 최소 게임 | CoffeeSetupMug로 브라우저 조작·gripper·mode·pause/reset 구현 | 비개발자가 도움말만 보고 성공1회; focus 이탈 시 로봇 정지 |
| 3. recorder·export | 20Hz pre-action capture, canonical3-camera, state53/action12, raw replay와 LeRobot 출력 | 수집 episode1개가 저장·재생·학습 loader read까지 통과 |
| 4. 세 task와 제출 QA | PressButton/ServeMug 추가, 원본 성공·중단 판정, 파일 보존·중복 없는 재시도 | task별 성공1개와 중단1개, 총6개 예제 및 QA report |
| 5. 후속 정책 비교 | RLDX baseline을 동일 환경에서 평가한 후 실패 구간·수집 목표를 정함 | 공통 held-out 평가 manifest와 baseline 결과. GPU·비용·범위는 별도 합의 |

**도헌이 첫 작업:** 위 원본 Kitchen24의 CoffeeSetupMug를 설치·실행하고 선택한 버전과 실제 camera/action schema를 고정한다. 그다음 브라우저 입력을 붙인다. 처음부터 전체24개 작업·RLDX 학습·다중 사용자·결제를 동시에 만들지 않는다.

## 9. 인수할 파일과 확인할 질문

- 소스 코드와 실행 README, dependency/version manifest, asset 준비 방법·출처.
- 영어 UI와 키 도움말, task 설정, native↔dataset action mapper, recorder/exporter, replay·QA 명령.
- 성공/중단 예제6개, episode manifest·검사 결과, 샘플 학습 loader 실행 결과.
- 미확인 항목: 개인 업로더 데이터의 출처·사용권 및 공식 학습 원본과의 동일성; 최종 pinned dependency 조합; 서비스 배포 방식; 실제 고객 실패 작업·필요 데이터량·합격 성능.

고객·인터뷰 맥락: [인터뷰 대상과 다음 플랜](https://app.notion.com/p/3fb2a46dc68c81c4a287fafd7ec3d9a9) · 전체 회의: [전체 회의](https://app.notion.com/p/3fb2a46dc68c80619604c5f2b630a2b5)
