# NU40 DK + OLED Live Stock Ticker

> **English quick start** (한국어 설명은 아래에 있습니다)
>
> Shows a stock's price, % change and a mini chart on a 0.96" SSD1306 OLED (128×64, I2C) connected to an NU40 DK.
> Buttons S1–S4 switch between 4 stocks; a slot's LED turns on when its price drops below your alert level.
> The board has no Wi-Fi, so a Python program on your computer fetches prices and sends them over USB serial.
>
> 1. Wire the OLED: GND → GND, SDA → P0.26, SCL → P0.27, VCC → VMOD (3.3 V).
> 2. Install **Adafruit SSD1306** (+ dependencies) in Arduino IDE, open `firmware/nu40_ticker/nu40_ticker.ino`, and upload (board: NU40DK nRF52840).
>    The OLED shows `WAITING` / `SLOT 1` until data arrives.
> 3. `python3 -m venv .venv && .venv/bin/pip install -r host/requirements.txt`
> 4. Test with simulated prices (no account needed): `.venv/bin/python tools/mock_feed.py` (add `--alert-after 15` to test the LEDs).
> 5. Optional: `cp host/config.example.env host/.env`, set the four `SLOTn=symbol,name,alert level` lines (English or Korean names both work).
> 6. Live prices: `.venv/bin/python host/bridge.py` uses the **Toss Securities Open API** (Korean brokerage account, API key and allowed IP required).
>
> Scanner for wiring problems: `tools/i2c_scan/i2c_scan.ino` (expect `Found: 0x3C`). Close Arduino IDE's Serial Monitor before running the Python tools.

---

# NU40 DK + OLED 실시간 주식 티커

토스증권 오픈 API에서 주가를 가져와, USB로 연결된 **NU40 DK 보드의 0.96인치 OLED**에
현재가 · 등락률 · 미니 차트를 실시간으로 띄웁니다.
보드의 **버튼 4개**로 종목 4개를 전환하고, 정해둔 **임계가 아래로 내려가면 LED가 켜집니다.**

```
토스 오픈API ──REST 폴링──▶ PC(파이썬) ──USB 시리얼──▶ NU40 DK ──I2C──▶ OLED
                                                        └─▶ 온보드 LED (임계가 알림)
```

### 왜 이렇게 나눴나

NU40은 nRF52840 칩을 쓰는데 **WiFi가 없습니다.** 보드가 직접 인터넷을 못 씁니다.
그래서 인터넷 호출 · JSON 해석 · 등락률 계산 · 차트 값 만들기는 **전부 PC(파이썬)가** 하고,
보드는 **받은 숫자를 화면에 그리기만** 합니다.

> 용어
> - **I2C**: 선 2가닥(SDA/SCL)으로 화면 같은 부품과 대화하는 통신 방식
> - **시리얼**: USB 케이블로 PC와 글자를 주고받는 통로. 여기선 115200 속도
> - **슬롯**: 종목 하나가 들어가는 자리. 0~3번, 버튼 1~4번에 대응
> - **폴링**: 서버에 "지금 값 뭐야?"를 일정 주기로 반복해서 물어보는 것

---

## 1. 배선표

**보드:** NUCODE NU40 DK (NU40-B-Dev02), Nordic nRF52840, USB-C
**화면:** SSD1306, 128×64, I2C, 주소 `0x3C` (안 잡히면 `0x3D`)

| 항목 | 핀 | USER TERMINAL 번호 |
|---|---|---|
| OLED SDA | **P0.26** | 25번 |
| OLED SCL | **P0.27** | 26번 |
| OLED VCC | **3V3** (3.3V OUT) | 27번 |
| OLED GND | **GND** | 24번 |
| 버튼 S1~S4 (보드 내장) | **P0.11 / P0.12 / P0.24 / P0.25** | — |
| LED 1~4 (보드 내장) | **P0.13 / P0.14 / P0.15 / P0.16** | — |

버튼은 누르면 LOW가 되므로 `INPUT_PULLUP`으로 읽습니다.
LED는 HIGH를 주면 켜집니다.

### I2C 핀을 코드에서 따로 지정하지 않는 이유

nRF52840은 I2C 핀을 소프트웨어로 정합니다. 보통은 `Wire.setPins()`로 지정해야 하지만,
**NU40DK는 그럴 필요가 없습니다.** 보드 패키지의 변형(variant) 파일에 이미 박혀 있기 때문입니다.

```
~/Library/Arduino15/packages/nucode/hardware/nrf52/1.0.2/variants/nu40dk_nrf52840/variant.h

    #define PIN_WIRE_SDA (26)   // P0.26
    #define PIN_WIRE_SCL (27)   // P0.27
```

즉 **`Wire.begin()` 기본값이 곧 P0.26 / P0.27** 입니다.
그래서 펌웨어는 `Wire.begin()`만 부르고, 확인차 `PIN_WIRE_SDA / PIN_WIRE_SCL` 상수를 그대로 씁니다.
(윈도우에서는 위 경로가 `C:\Users\사용자명\AppData\Local\Arduino15\...` 입니다.)

---

## 2. 아두이노 IDE 설치 순서

1. **아두이노 IDE 2.x** 설치 — https://www.arduino.cc/en/software
2. **보드 패키지 등록**
   - `파일` → `기본 설정` → `추가 보드 관리자 URL` 에 NUCODE가 안내한 주소를 넣습니다.
   - `도구` → `보드` → `보드 매니저` 에서 **`NUBoards nRF52 by NUCODE`** 를 검색해 설치합니다.
   - 설치 후 `도구` → `보드` → `NUBoards nRF52` → **`NU40DK nRF52840`** 을 선택합니다.
3. **라이브러리 설치** — `스케치` → `라이브러리 포함하기` → `라이브러리 관리`
   - `Adafruit SSD1306` 검색 → 설치
   - 함께 설치할지 물으면 **전부 설치(Install all)** 를 누릅니다.
     (`Adafruit GFX Library`, `Adafruit BusIO` 가 같이 필요합니다)
4. `도구` → `포트` 에서 보드 포트를 선택합니다.
   - 맥: `/dev/cu.usbmodem...`
   - 윈도우: `COM3` 같은 이름

---

## 3. 1단계 — I2C 주소 확인 (화면 배선 검사)

화면이 안 켜질 때 **"배선 문제인지 화면이 죽은 건지"** 를 가르는 단계입니다.

1. 아두이노 IDE에서 `tools/i2c_scan/i2c_scan.ino` 를 엽니다.
   (아두이노는 폴더 이름과 파일 이름이 같아야 열립니다. 폴더째로 여세요.)
2. 업로드합니다.
3. `도구` → `시리얼 모니터` 를 열고 오른쪽 아래 속도를 **115200** 으로 맞춥니다.

**정상이면 이렇게 나옵니다:**
```
=== NU40 DK I2C address scan ===
SDA pin: P0.26
SCL pin: P0.27

Scanning... (0x01 - 0x7E)
  Found: 0x3C
  Found 1 device(s). If you see 0x3C or 0x3D, the OLED is OK.
```

**아무것도 안 나오면:**
- SDA와 SCL을 **서로 바꿔서** 꽂아보세요. (제일 흔한 실수입니다)
- OLED의 VCC가 **3V3**, GND가 **GND**에 꽂혔는지 확인하세요. (5V에 꽂으면 안 됩니다)
- 점퍼선을 다른 것으로 바꿔보세요. 선이 끊긴 경우가 생각보다 많습니다.

`0x3D`가 나와도 괜찮습니다. 펌웨어가 `0x3C` 실패 시 `0x3D`를 자동으로 시도합니다.

---

## 4. 2단계 — 티커 펌웨어 올리기

1. `firmware/nu40_ticker/nu40_ticker.ino` 를 열고 업로드합니다.
2. 화면에 `WAITING` / `SLOT 1` 이 뜨면 성공입니다.
   아직 PC에서 값을 안 보냈다는 뜻입니다.

> **한글은 어떻게 나오나**
> Adafruit GFX 기본 글꼴에는 한글이 없습니다. 그래서 **한글 글자 모양을 직접 만들어
> 펌웨어에 심어뒀습니다.** `삼성전자`, `SK하이닉스` 처럼 한글 이름을 그대로 띄울 수 있습니다.
> 자세한 것은 아래 **한글 글꼴** 절을 보세요.

---

## 5. 3단계 — 가짜 주가로 하드웨어 검증 (`mock_feed.py`)

**API를 붙이기 전에 반드시 이 단계를 먼저 하세요.**
나중에 문제가 생겼을 때 "API 문제인지 보드 문제인지"를 바로 가를 수 있습니다.

### 파이썬 준비 (한 번만)

```bash
cd ~/nu40-stock-ticker

# 가상환경 만들기 — 맥 시스템 파이썬을 건드리지 않기 위해서입니다
python3 -m venv .venv

# 라이브러리 설치
.venv/bin/pip install -r host/requirements.txt
```

> 윈도우에서는 `python -m venv .venv` 후 `.venv\Scripts\pip install -r host\requirements.txt` 입니다.

### 실행

```bash
.venv/bin/python tools/mock_feed.py
```

`host/.env` 가 있으면 **거기 적힌 종목·표시이름·임계가를 그대로 따라갑니다.**
(설정을 두 군데 고치지 않아도 되게 하려는 것입니다)
`.env` 가 없으면 내장된 기본 종목으로 돕니다. 필요한 라이브러리는 `pyserial` 하나뿐입니다.

- 포트는 자동으로 찾습니다. 못 찾으면 `--port /dev/cu.usbmodem1101` 처럼 직접 지정합니다.
- **일부러 LED를 켜보려면:** `.venv/bin/python tools/mock_feed.py --alert-after 15`
  → 15초 뒤 슬롯 1·3의 가격을 임계가 아래로 끌어내려 LED를 켭니다.

### 이 셋을 눈으로 확인하세요

1. **화면** — 종목명·현재가·등락률·차트가 뜨고, 차트가 조금씩 움직인다
2. **버튼** — 버튼 1~4를 누르면 종목이 **즉시** 바뀐다 (아래 네모 인디케이터도 같이 이동)
3. **LED** — `--alert-after` 를 주면 해당 슬롯 LED가 **깜빡이지 않고 계속** 켜져 있다

여기까지 되면 배선과 펌웨어는 완전히 정상입니다.

---

## 6. 4단계 — 토스 API 연결 (`bridge.py`)

### 설정 파일 만들기

```bash
cp host/config.example.env host/.env
```

`host/.env` 를 열어 채웁니다:

```
TOSS_CLIENT_ID=발급받은_ID
TOSS_CLIENT_SECRET=발급받은_시크릿
SERIAL_PORT=/dev/cu.usbmodem1101
POLL_SECONDS=5
SLOT0=005930,삼성전자,69000
SLOT1=000660,SK하이닉스,170000
SLOT2=TSLA,테슬라,400
SLOT3=NVDA,엔비디아,170
```

`SLOTn = 종목코드,표시이름,임계가` 형식입니다.
- 국내 종목은 6자리 숫자(`005930`), 미국 종목은 영문 티커(`TSLA`)
- 임계가: 현재가가 이 값 **아래**로 내려가면 그 슬롯 LED가 켜집니다
- 표시이름은 **한글 그대로** 화면에 나옵니다 (`삼성전자`). 가로 80픽셀까지 쓰고 넘치면 잘립니다

> **허용 IP 등록을 잊지 마세요.**
> 토스증권 WTS → `설정` → `Open API` → 아래쪽 **`허용 IP 관리`** 에
> 지금 쓰는 인터넷의 공인 IP를 등록해야 합니다.
> 등록 안 된 IP에서 부르면 **403**으로 막힙니다. (집 IP가 바뀌면 다시 등록해야 합니다)

### 실행

```bash
.venv/bin/python host/bridge.py
```

정상이면 이렇게 나옵니다:

```
============================================================
 NU40 DK Live Stock Ticker — Toss Securities Open API
============================================================
[13:20:01] Loaded settings: /Users/.../host/.env
[13:20:01] Slots: 0=005930(삼성전자) alert below 69,000.00, ...
[13:20:01] Connected to the board: /dev/cu.usbmodem1101 (115200 baud)
[13:20:01] Board ready — sending all 4 slots now.
[13:20:02] Got an access token (valid for about 3600 s).
[13:20:05] 삼성전자 68,900 -2.96%  |  SK하이닉스 170,500 +0.29%  |  ...
```

멈추려면 `Ctrl+C` 입니다.

### 이 프로그램이 부르는 API (전부 조회 전용)

| 엔드포인트 | 쓰임 |
|---|---|
| `POST /oauth2/token` | 액세스 토큰 발급 (OAuth 2.0 Client Credentials) |
| `GET /api/v1/prices` | 현재가 조회 — 종목 4개를 **한 번의 호출로** 처리 |
| `GET /api/v1/candles` | 1분봉(차트용), 일봉 2개(등락률 기준용) |
| `GET /api/v1/market-calendar/KR` `/US` | 오늘 정규장이 몇 시부터 몇 시까지인지 |

**주문(매매) API는 부르지 않습니다. 관련 함수 자체를 만들지 않았습니다.**

호출 횟수를 아끼려고 이렇게 나눴습니다:

| 무엇 | 주기 | 이유 |
|---|---|---|
| 현재가 | `POLL_SECONDS` (기본 5초) | 이게 실시간으로 보여야 하는 값 |
| 1분봉 차트 | 60초 | 어차피 1분에 한 번만 바뀜 |
| 전일 종가 (등락률 기준) | 10분 | 하루에 한 번 바뀜 |
| 장 운영 시간 | 30분 | 하루에 한 번 바뀜 |

### 차트는 "오늘 정규장" 구간입니다

`CHART_RANGE=day` (기본값)이면 **오늘 정규장 시작부터 지금까지**를 64개 점에 담습니다.
국내는 09:00~15:30, 미국은 한국시간 22:30~05:00 구간입니다.
**아직 오늘 장이 안 열렸으면 가장 최근에 끝난 장**을 보여줍니다.
(낮에 미국 주식을 보면 어젯밤 미국장이 뜹니다)

`CHART_RANGE=hour` 로 바꾸면 최근 64분만 담습니다.

> **왜 장 운영 시간 API까지 부르나**
> 처음엔 "캔들 사이 간격이 크게 벌어지는 곳이 장 경계"라는 방법을 썼는데, 국내 종목엔 통했지만
> 미국 종목엔 통하지 않았습니다. 토스 API가 미국 종목은 **24시간 내내 1분봉을 주기 때문에**
> 장과 장 사이에 빈 구간이 없었습니다. (1400분을 받아봐도 경계가 안 나왔습니다)
> 그래서 장 운영 시간을 직접 물어보는 방식으로 바꿨습니다.

> **미국 종목의 날짜 함정**
> 미국 주식도 응답 시각이 **한국 시간(+09:00) 기준**으로 옵니다.
> 그래서 미국 정규장 한 번이 22:30~다음날 05:00, 즉 **날짜 두 개에 걸칩니다.**
> "오늘 날짜인 캔들만 고르기" 같은 방식으로 자르면 미국 종목이 반토막 납니다.

### 실시간 변동이 어느 정도로 보이나

- **현재가·등락률**: 5초마다 갱신됩니다. 눈에 잘 띕니다.
- **차트**: 맨 오른쪽 점이 5초마다 갱신됩니다. 다만 하루 전체를 64점에 담으면
  **점 하나가 약 6분**이라, 5초 변동은 마지막 한 점에만 반영됩니다.

움직임이 크게 보이길 원하면 `CHART_RANGE=hour` 를 쓰세요. 같은 조건(120원 하락)으로 재보면:

| 모드 | 5초 뒤 오른쪽 끝 3점 | 체감 |
|---|---|---|
| `day` | `[6,4,1]` → `[6,4,0]` | 마지막 점 1칸 |
| `hour` | `[5,2,0]` → `[6,4,0]` | 꼬리 전체가 재조정 |

## 7. 통신 규약 (사람이 읽을 수 있는 텍스트)

속도 **115200**, 줄 단위, **UTF-8**. 시리얼 모니터로 눈으로 보면서 디버깅할 수 있습니다.
(한글 종목명이 오갈 수 있어서 UTF-8을 씁니다. 한글 한 글자는 3바이트입니다)

### PC → 보드

```
D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,<v1>,...,<v63>
```

| 칸 | 뜻 |
|---|---|
| `slot` | 0~3 (버튼 1~4에 대응) |
| `name` | 화면에 띄울 이름. 한글 가능 (`삼성전자`, `SK하이닉스`). 가로 80픽셀까지 |
| `price` | 현재가 문자열 (`68,900` / `398.20`) |
| `chg` | 등락률 문자열 (`-2.96%`) |
| `alert` | `0` 또는 `1` — 임계가 아래면 1 |
| `v0~v63` | 차트용 정규화 값 **0~36 정수 64개**. 최근 값이 뒤쪽 |

정규화·콤마 포맷팅·소수점 처리는 전부 파이썬이 끝냅니다. 보드는 파싱만 합니다.

실제 예시 (한 줄):
```
D|0|삼성전자|268,500|-2.19%|0|18,19,17,20,22,...,15,12,9
```

한 줄의 최대 길이는 약 241바이트입니다(가장 긴 한글 이름 + 가장 긴 숫자 기준).
펌웨어 버퍼는 320바이트라 여유가 있습니다.

### 보드 → PC

| 줄 | 뜻 |
|---|---|
| `READY` | 보드 부팅 완료. PC는 이 신호를 받으면 4슬롯을 즉시 전부 보냅니다 |
| `SEL|<slot>` | 사용자가 버튼을 눌러 슬롯을 바꿨습니다 (로그용) |
| `ERR|<메시지>` | 보드 쪽 오류 |

보드는 **4개 슬롯을 모두 메모리에 캐시**하고 현재 선택된 슬롯만 그립니다.
버튼을 누르면 PC 응답을 기다리지 않고 **캐시된 값으로 즉시** 화면을 바꿉니다. 체감 속도가 핵심입니다.

### 차트 값이 0~36인데 화면은 30픽셀인 이유

규약은 `0~36`으로 고정입니다. 그런데 화면 레이아웃상 차트 영역은 y 28~57, 즉 **30픽셀**입니다.
그래서 펌웨어가 그릴 때 비율에 맞춰 줄입니다 (`chartValueToY()`).
파이썬은 규약대로 0~36을 보내고, 화면 맞춤은 보드가 합니다.

---

## 8. 화면 배치 (128×64)

```
┌────────────────────────────┐
│ 삼성전자           -2.19%   │  y  1~ 9   종목명(좌, 한글 9픽셀) / 등락률(우정렬)
│ 268,500                    │  y 13~28   현재가, size 2 큰 글씨
│      ╱╲    ╱╲              │
│  ╱╲╱   ╲╱╱   ╲╲            │  y 31~57   차트 (점을 선으로 연결)
│ ╱             ╲            │
│ ▮ ▯ ▯ ▯                    │  y 58~63   슬롯 인디케이터, 선택된 것만 채움
└────────────────────────────┘
```

- 차트는 64개 점을 x = 0, 2, 4, ..., 126에 놓고 `drawLine()`으로 잇습니다. 점을 다 찍지 않습니다.
- 값이 없는 슬롯은 `WAITING` 표시.
- **화면은 값이 실제로 바뀌었을 때만 다시 그립니다.** 매 루프마다 그리면 눈에 보이게 깜빡입니다.

### 한글 글꼴

Adafruit GFX 기본 글꼴에는 한글이 없어서, **글자 모양을 직접 비트맵으로 만들어 펌웨어에
심었습니다.** 글꼴은 **갈무리9(Galmuri9)** — 작은 화면용 픽셀 글꼴이라 9픽셀에서도 또렷합니다.

```bash
python3 tools/make_hangul_font.py           # KS X 1001 완성형 2350자 (기본)
python3 tools/make_hangul_font.py --full    # 현대 한글 11172자 전부
```

결과물은 `firmware/nu40_ticker/hangul_font.h` 입니다. 자동 생성물이라 직접 고치지 마세요.

| | |
|---|---|
| 글자 크기 | 9×9 픽셀 (모든 한글이 균일), 글자 간격 10픽셀 |
| 저장 방식 | 한 글자당 18바이트. 9줄 × 2바이트 |
| 글자 찾기 | 글자 번호 목록을 이진 탐색 (2350자를 12번 안에) |
| 차지 용량 | 약 46KB — 플래시 사용량이 8% → 14%로 늘어납니다 |

한글은 이 비트맵으로, 영문·숫자는 기존 GFX 글꼴로 그립니다. 한 줄에 섞여 있어도 됩니다
(`SK하이닉스`). 영문이 한글보다 2픽셀 작아서 1픽셀 내려 눈높이를 맞춥니다.

**글꼴을 다시 만들 필요는 거의 없습니다.** 2350자에 일상적인 회사 이름은 전부 들어 있어서,
종목을 바꿔도 `.env`만 고치면 됩니다.

## LED 동작

- `alert=1`인 슬롯의 LED가 켜집니다. 슬롯 0~3 ↔ P0.13~P0.16.
- **지금 보고 있는 슬롯이 아니어도 켜집니다.** 다른 종목이 떨어진 것도 알아야 하니까요.
- **깜빡임이 아니라 상시 점등**입니다. 촬영할 때 프레임에 안정적으로 잡히게 하기 위해서입니다.

---

## 9. 자주 나는 오류와 해결법

### 화면이 안 켜집니다

| 확인 순서 | 방법 |
|---|---|
| 1 | `i2c_scan` 을 올려 `0x3C`(또는 `0x3D`)가 뜨는지 봅니다 |
| 2 | 안 뜨면 **SDA와 SCL을 서로 바꿔** 꽂습니다 (가장 흔한 원인) |
| 3 | VCC가 **3V3**인지 확인합니다. 5V에 꽂으면 안 됩니다 |
| 4 | 점퍼선을 다른 것으로 교체합니다 |

시리얼 모니터에 `ERR|No OLED` 가 반복되면 보드는 살아 있고 화면만 못 찾은 상태입니다.

### 포트를 못 찾습니다 / 포트를 열 수 없습니다

- **아두이노 IDE의 시리얼 모니터를 닫으세요.** 포트는 한 번에 한 프로그램만 씁니다.
  이게 압도적으로 많은 원인입니다.
- 맥에서 포트 확인: `ls /dev/cu.usb*`
- 윈도우: 장치 관리자 → 포트(COM & LPT)
- USB 케이블이 **충전 전용**이면 안 됩니다. 데이터 통신이 되는 케이블을 쓰세요.
- `.env`의 `SERIAL_PORT`를 비워두면 프로그램이 알아서 찾습니다.

### 글자가 깨져 보입니다

- **한글 자리가 비어 있다면** 그 글자가 글꼴에 없는 것입니다. 기본으로 넣은 것은
  KS X 1001 완성형 2350자라 일상적인 회사 이름은 전부 들어 있지만, 드문 글자는 빠질 수 있습니다.
  `python3 tools/make_hangul_font.py --full` 로 현대 한글 11172자를 전부 넣고 다시 구우면 됩니다.
  (용량이 46KB → 약 200KB로 늘지만 보드 플래시는 815KB라 충분합니다)
- **글자 전체가 깨진다면** 시리얼 **속도가 115200이 맞는지** 확인하세요.
- **이름이 잘려 보인다면** 정상입니다. 등락률 자리를 남기려고 이름은 가로 80픽셀까지만 씁니다.
  (한글 8자 또는 영문 13자 정도)

### 토큰 관련 오류

| 메시지 | 원인과 해결 |
|---|---|
| `Token refused — check client_id/client_secret.` | `.env`의 키를 다시 확인하세요. 앞뒤 공백이나 따옴표가 들어가지 않았는지 보세요 |
| `Token blocked (403)` / `Access denied (403)` | 토스증권 → 설정 → Open API → **허용 IP 관리**에 현재 IP를 등록하세요 |
| `Token expired, getting a new one.` | **정상 동작입니다.** 프로그램이 알아서 재발급합니다. 아무것도 안 해도 됩니다 |
| `Rate limited. Retrying in ...` | `POLL_SECONDS`를 늘리세요(5 → 10). 잠시 뒤 자동으로 재시도합니다 |

### 인터넷이 끊겼습니다

프로그램은 죽지 않습니다. 2초 → 4초 → 8초... 최대 60초까지 간격을 늘려가며 재시도하고,
**보드는 마지막 정상값을 계속 화면에 띄웁니다.** 연결이 돌아오면 저절로 이어집니다.

### 맥에서 보드가 안 잡힙니다 (가장 먼저 확인할 것)

**애플 실리콘 맥북(M1/M2/M3...)에서 제일 흔한 원인입니다.** 보드에 불은 들어오는데
포트가 안 생기면 대부분 이것입니다.

macOS는 처음 보는 USB 장치를 사용자 승인 없이 막습니다. 커널 로그에 이렇게 남습니다:

```
enumerated 0x239a/00da (PCA10056) at 12 Mbps
device functionality blocked by transport restrictions   ← 차단됨
device will not be registered for matching               ← 그래서 포트가 안 만들어짐
```

**해결:** `시스템 설정` → `개인정보 보호 및 보안` → 맨 아래 **`액세서리 연결 허용`**
→ **`잠금 해제 상태에서 자동으로`** 로 변경한 뒤, 케이블을 뽑았다 다시 꽂습니다.

> 설정을 바꿔도 **이미 연결된 장치에는 적용되지 않습니다.** 반드시 다시 꽂아야 합니다.

미리 바꿔두길 권합니다. 보드를 구울 때마다 앱 모드(`0x239A/0x00DA`)와 부트로더 모드(`0x239A/0x80xx`)를
오가며 장치 ID가 바뀌는데, macOS는 그때마다 새 액세서리로 보고 다시 물어봅니다.

**직접 확인하는 방법** (터미널):
```bash
# 지금 보드가 붙어 있나
ioreg -p IOUSB -l -w 0 | grep -E '"USB Product Name"|"idVendor"'

# 최근 3분간 USB 연결/차단 기록
log show --last 3m --predicate 'subsystem == "com.apple.usb"' --style compact
```

### 업로드가 안 됩니다 — `No data received on serial port`

보드가 **안전모드(Safe Mode)** 에 잠겼거나, 실행 중인 스케치가 계속 죽는 경우입니다.
이전에 올린 스케치가 여러 번 죽으면 로더가 스스로 잠급니다.

**증상 구분법 — 장치 ID를 보면 알 수 있습니다:**

| 보이는 것 | 뜻 | 할 일 |
|---|---|---|
| `0x239A / 0x00xx` (포트만 생김) | **앱(스케치) 실행 중** | RST 한 번으론 안 됩니다. 아래 더블탭 |
| `0x239A / 0x80xx` + 드라이브 마운트 | **부트로더** — 구울 수 있는 상태 | 그냥 업로드하면 됩니다 |
| 5~15초마다 붙었다 떨어졌다 | 스케치가 죽고 재시작을 반복 | 아래 더블탭으로 덮어쓰기 |

**해결: RESET 버튼을 빠르게 두 번 누릅니다.**
그러면 스케치를 건너뛰고 부트로더로 들어가서, `NRF52BOOT` 같은 드라이브가 마운트됩니다.
이 상태는 안정적이라 업로드가 확실히 됩니다.

부트로더 드라이브가 뜨면 두 가지 방법이 있습니다:
1. 아두이노 IDE에서 그냥 업로드 (권장)
2. `.uf2` 파일을 그 드라이브에 **복사** — 드래그만 하면 됩니다
   (복사 중 `입력/출력 오류`가 떠도 정상입니다. 보드가 다 받자마자 즉시 재시작하기 때문입니다)

드라이브 안의 `NU_STATUS.TXT` 를 열면 상태를 볼 수 있습니다:
```
Safe-Mode: Yes            ← 잠긴 상태
Fault-Count: 3            ← 스케치가 3번 죽었다는 뜻
Sketch-Armed: No
```

### `i2c_scan` 에 `Nothing found.` 만 나옵니다

NU40 DK 보드에는 I2C 부품이 따로 붙어 있지 않습니다. 그래서 **OLED가 안 꽂혔거나 잘못 꽂혔으면
아무 주소도 안 나옵니다.** OLED를 제대로 꽂으면 이렇게 **하나만** 나옵니다:
```
  Found: 0x3C
  Found 1 device(s). If you see 0x3C or 0x3D, the OLED is OK.
```
안 나오면 위 **화면이 안 켜집니다** 표대로 SDA·SCL 바꿔 꽂기 → VCC(VMOD)·GND 확인 → 점퍼선 교체 순서로 봅니다.

### `i2c_scan` 이 아무것도 출력하지 않고 멈춥니다

포트는 살아 있는데 3초마다 나오던 글이 뚝 끊겼다면, **I2C 선 하나가 계속 LOW로 붙들려 있는
상태**입니다. `Wire.endTransmission()` 이 응답을 기다리며 무한정 멈춥니다.

거의 항상 배선 문제입니다:
- **VCC와 GND가 뒤바뀜** (제일 흔함)
- SDA/SCL이 엉뚱한 핀에 꽂힘, 또는 서로 닿음

배선을 고친 뒤 RST를 한 번 누르면 다시 돕니다.

## 10. 파일 구성

```
firmware/nu40_ticker/nu40_ticker.ino   보드 펌웨어 (화면 그리기·버튼·LED)
firmware/nu40_ticker/hangul_font.h      한글 글꼴 데이터 (자동 생성물, 고치지 말 것)
tools/make_hangul_font.py               한글 글꼴 데이터 만드는 도구
host/bridge.py                          토스 API 폴링 + 시리얼 송신
host/config.example.env                 설정 템플릿 (복사해서 .env로)
host/requirements.txt                   파이썬 라이브러리 목록
tools/i2c_scan/i2c_scan.ino             I2C 주소 확인용 (별도)
tools/mock_feed.py                      API 없이 가짜 데이터로 보드 테스트
tools/preview/oled_preview.py           보드 없이 화면 미리보기 (아래 설명)
tools/preview/range_compare.py          차트 범위별 움직임 비교
README.md                               이 문서
```

> 아두이노는 **스케치 파일과 폴더 이름이 같아야** 열립니다.
> 그래서 `i2c_scan` 은 폴더 안에 들어 있습니다. 폴더째로 여세요.

### 보드 없이 화면을 미리 보는 도구

```bash
.venv/bin/python tools/preview/oled_preview.py
```

`tools/preview/*.png` 로 화면 그림이 나오고 자동으로 열립니다.

이건 대충 그린 목업이 아닙니다. **아두이노에 실제로 설치된 Adafruit GFX 글꼴 파일
(`glcdfont.c`)을 직접 읽어서**, 펌웨어의 `drawScreen()` 로직을 그대로 옮겨 그립니다.
글자 모양·자간·차트 선 알고리즘까지 같아서 **실제 화면과 픽셀 단위로 동일**합니다.

디자인을 고치고 싶을 때 보드에 굽고 확인하는 왕복을 안 해도 됩니다.

## 11. 보안

- `.env`는 `.gitignore`에 들어 있어 깃에 올라가지 않습니다.
- `client_secret`은 **어떤 로그에도 찍지 않습니다.** 오류 메시지에도 포함되지 않습니다.
- 이 프로젝트는 **조회 API만** 사용합니다. 주문·매매 함수는 만들지 않았습니다.
