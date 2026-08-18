/*
  NU40 DK 실시간 주식 티커 — 보드 펌웨어
  ------------------------------------------------------------------
  이 보드가 하는 일은 딱 두 가지다.
    1) PC(파이썬 bridge.py)가 USB로 보내준 "이미 계산이 끝난 숫자"를 받아 OLED에 그린다.
    2) 버튼 4개로 화면에 띄울 종목을 바꾸고, 임계가 알림 LED를 켠다.

  이 보드(nRF52840)에는 WiFi가 없다. 그래서 인터넷 호출·JSON 해석·차트 계산은
  전부 PC가 하고, 보드는 "받은 대로 그리기"만 한다. 이 역할 분담을 바꾸지 말 것.

  용어 한 줄 설명
    - I2C   : 선 2가닥(SDA/SCL)으로 화면 같은 부품과 대화하는 통신 방식.
    - 시리얼 : USB 케이블로 PC와 글자를 주고받는 통로. 여기선 115200 속도를 쓴다.
    - 슬롯   : 종목 하나가 들어가는 자리. 0~3번, 버튼 1~4번에 각각 대응한다.
*/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ─────────────────────────────────────────────────────────────
// 하드웨어 설정 (회로도로 확정된 값 — 바꾸지 말 것)
// ─────────────────────────────────────────────────────────────

// OLED는 P0.26(SDA) / P0.27(SCL)에 연결한다.
// NU40DK 보드 패키지의 variant.h 에 PIN_WIRE_SDA=26, PIN_WIRE_SCL=27 로
// 이미 지정돼 있어서 Wire.begin() 기본값이 곧 P0.26/P0.27 이다.
// 즉 이 보드에서는 Wire.setPins() 를 따로 부를 필요가 없다.
static const uint8_t PIN_OLED_SDA = PIN_WIRE_SDA;  // = 26 (P0.26)
static const uint8_t PIN_OLED_SCL = PIN_WIRE_SCL;  // = 27 (P0.27)

// 보드에 내장된 버튼 4개. 누르면 LOW(0)가 되므로 INPUT_PULLUP 으로 읽는다.
static const uint8_t BUTTON_PINS[4] = { 11, 12, 24, 25 };  // P0.11 / P0.12 / P0.24 / P0.25

// 보드에 내장된 LED 4개. HIGH를 주면 켜진다(variant.h 의 LED_STATE_ON = 1).
static const uint8_t LED_PINS[4]    = { 13, 14, 15, 16 };  // P0.13 / P0.14 / P0.15 / P0.16

// 디스플레이: SSD1306, 128x64, I2C.
static const uint8_t OLED_ADDR_MAIN = 0x3C;  // 기본 주소
static const uint8_t OLED_ADDR_ALT  = 0x3D;  // 기본 주소로 안 잡힐 때 시도할 주소

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1   // NU40DK에는 화면 리셋 전용 핀이 없다는 뜻

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ─────────────────────────────────────────────────────────────
// 화면 배치 (128 x 64 픽셀)
//   y  0~11 : 종목명(왼쪽) / 등락률(오른쪽 정렬)
//   y 12~27 : 현재가, 큰 글씨(size 2)
//   y 28~57 : 미니 차트
//   y 58~63 : 슬롯 인디케이터(지금 보는 슬롯만 꽉 채움)
// ─────────────────────────────────────────────────────────────
static const int16_t HEADER_Y      = 2;    // 종목명/등락률 글자 윗줄 위치
static const int16_t PRICE_Y       = 12;   // 현재가 글자 윗줄 위치
static const int16_t CHART_TOP     = 28;   // 차트 영역 맨 위
static const int16_t CHART_BOTTOM  = 57;   // 차트 영역 맨 아래
static const int16_t CHART_HEIGHT  = CHART_BOTTOM - CHART_TOP;  // = 30 픽셀
static const int16_t INDICATOR_Y   = 58;   // 슬롯 인디케이터 윗줄 위치

// ─────────────────────────────────────────────────────────────
// 슬롯 데이터
// ─────────────────────────────────────────────────────────────
static const uint8_t SLOT_COUNT   = 4;
static const uint8_t CHART_POINTS = 64;   // 차트 점 개수 (PC가 64개를 보내준다)
static const uint8_t CHART_MAX    = 36;   // 차트 값의 최대치. PC는 0~36 정수로 정규화해서 보낸다.

struct Slot {
  char    name[11];               // 화면에 띄울 짧은 이름 (최대 10자 + 문자열 끝표시 1)
  char    price[16];              // 현재가 문자열 (예: "68,900")
  char    chg[12];                // 등락률 문자열 (예: "-2.96%")
  uint8_t alert;                  // 1이면 임계가 아래 → LED 점등
  uint8_t chart[CHART_POINTS];    // 차트용 값 0~36
  bool    hasData;                // PC에서 값을 한 번이라도 받았는지
};

static Slot    slots[SLOT_COUNT];
static uint8_t currentSlot = 0;   // 지금 화면에 그리고 있는 슬롯
static bool    needsRedraw  = true;  // 화면을 다시 그려야 하는지 (값이 바뀔 때만 true)
static bool    displayOk    = false; // OLED를 정상적으로 찾았는지

// ─────────────────────────────────────────────────────────────
// 시리얼 수신 버퍼
// PC가 보내는 한 줄은 최대 230자 정도다. 넉넉히 320자를 잡는다.
// ─────────────────────────────────────────────────────────────
static const uint16_t LINE_BUF_SIZE = 320;
static char     lineBuf[LINE_BUF_SIZE];
static uint16_t lineLen      = 0;
static bool     lineOverflow = false;  // 한 줄이 너무 길어서 잘렸는지

// ─────────────────────────────────────────────────────────────
// 버튼 흔들림 제거(디바운스)
// 버튼은 누르는 순간 전기적으로 몇 번 떨린다. 30ms 안의 변화는 무시한다.
// ─────────────────────────────────────────────────────────────
static const uint32_t DEBOUNCE_MS = 30;
static bool     buttonWasDown[4]   = { false, false, false, false };
static uint32_t buttonChangedAt[4] = { 0, 0, 0, 0 };

static uint32_t lastErrorNoticeMs = 0;  // OLED 오류 메시지를 5초에 한 번만 보내려고 쓴다

// ─────────────────────────────────────────────────────────────
// 준비 단계
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  // 버튼: 눌리면 LOW. 내부 풀업 저항을 켜서 평소엔 HIGH가 되게 한다.
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
  }

  // LED: 처음엔 모두 끈다.
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    pinMode(LED_PINS[i], OUTPUT);
    digitalWrite(LED_PINS[i], LOW);
  }

  // 슬롯을 빈 상태로 초기화한다.
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    slots[i].name[0]  = '\0';
    slots[i].price[0] = '\0';
    slots[i].chg[0]   = '\0';
    slots[i].alert    = 0;
    slots[i].hasData  = false;
    for (uint8_t j = 0; j < CHART_POINTS; j++) slots[i].chart[j] = 0;
  }

  // I2C 시작. NU40DK는 기본값이 이미 P0.26/P0.27 이라 핀 지정이 따로 필요 없다.
  Wire.begin();
  Wire.setClock(400000);  // 화면을 빠르게 갱신하려고 400kHz로 올린다

  // OLED를 0x3C 주소로 찾아보고, 없으면 0x3D로 한 번 더 시도한다.
  displayOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_MAIN);
  if (!displayOk) {
    displayOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ALT);
  }

  if (displayOk) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    drawScreen();
  }

  // PC에게 "나 준비됐다"고 알린다. PC는 이 줄을 보면 슬롯 4개를 즉시 다 보내준다.
  Serial.println("READY");

  if (!displayOk) {
    Serial.println("ERR|OLED를 찾지 못했습니다. SDA=P0.26 / SCL=P0.27 배선과 3V3·GND를 확인하세요.");
  }
}

// ─────────────────────────────────────────────────────────────
// 반복 실행
// ─────────────────────────────────────────────────────────────
void loop() {
  readSerial();
  readButtons();

  // 화면은 값이 실제로 바뀌었을 때만 다시 그린다.
  // 매번 그리면 눈에 보이게 깜빡인다.
  if (needsRedraw && displayOk) {
    drawScreen();
    needsRedraw = false;
  }

  // OLED가 없으면 5초에 한 번씩만 알려준다(로그가 도배되지 않게).
  if (!displayOk && millis() - lastErrorNoticeMs > 5000) {
    lastErrorNoticeMs = millis();
    Serial.println("ERR|OLED 없음 — 배선 확인 후 보드를 다시 연결하세요.");
  }
}

// ─────────────────────────────────────────────────────────────
// PC가 보낸 글자를 한 줄씩 모은다
// ─────────────────────────────────────────────────────────────
void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') continue;  // 윈도우 줄바꿈의 앞글자는 버린다

    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      if (lineOverflow) {
        Serial.println("ERR|받은 줄이 너무 깁니다. 무시했습니다.");
      } else if (lineLen > 0) {
        handleLine(lineBuf);
      }
      lineLen      = 0;
      lineOverflow = false;
      continue;
    }

    if (lineLen < LINE_BUF_SIZE - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineOverflow = true;  // 넘치면 이 줄은 통째로 버린다
    }
  }
}

// ─────────────────────────────────────────────────────────────
// 한 줄 해석
//   D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,<v1>,...,<v63>
// ─────────────────────────────────────────────────────────────
void handleLine(char* line) {
  if (line[0] != 'D' || line[1] != '|') {
    Serial.println("ERR|알 수 없는 명령입니다. D| 로 시작해야 합니다.");
    return;
  }

  // '|' 를 기준으로 6칸으로 자른다. 마지막 칸(차트)은 자르지 않고 통째로 둔다.
  char*   field[6];
  uint8_t fieldCount = 0;
  char*   cursor     = line + 2;

  field[fieldCount++] = cursor;
  while (*cursor != '\0' && fieldCount < 6) {
    if (*cursor == '|') {
      *cursor = '\0';               // 여기서 끊고
      field[fieldCount++] = cursor + 1;  // 다음 칸의 시작을 기억한다
    }
    cursor++;
  }

  if (fieldCount < 6) {
    Serial.println("ERR|칸 개수가 모자랍니다. D|슬롯|이름|가격|등락률|알림|차트 형식이어야 합니다.");
    return;
  }

  // 1) 슬롯 번호
  long slotNo = atol(field[0]);
  if (slotNo < 0 || slotNo >= SLOT_COUNT) {
    Serial.println("ERR|슬롯 번호는 0~3 이어야 합니다.");
    return;
  }
  Slot& s = slots[slotNo];

  // 2) 글자 항목들. 정해진 길이를 넘으면 뒤를 잘라서 담는다.
  bool changed = false;
  changed |= copyIfChanged(s.name,  field[1], sizeof(s.name));
  changed |= copyIfChanged(s.price, field[2], sizeof(s.price));
  changed |= copyIfChanged(s.chg,   field[3], sizeof(s.chg));

  // 3) 알림 여부 (0 또는 1)
  uint8_t alert = (field[4][0] == '1') ? 1 : 0;
  if (alert != s.alert) {
    s.alert = alert;
    changed = true;
  }

  // 4) 차트 값 64개. 콤마로 구분된 0~36 정수다.
  uint8_t count  = 0;
  char*   p      = field[5];
  while (*p != '\0' && count < CHART_POINTS) {
    long v = atol(p);                 // 숫자 하나를 읽고
    if (v < 0) v = 0;                 // 범위를 벗어나면 잘라 맞춘다
    if (v > CHART_MAX) v = CHART_MAX;
    if (s.chart[count] != (uint8_t)v) {
      s.chart[count] = (uint8_t)v;
      changed = true;
    }
    count++;
    while (*p != '\0' && *p != ',') p++;  // 다음 콤마까지 건너뛰고
    if (*p == ',') p++;                    // 콤마를 넘긴다
  }

  if (count < CHART_POINTS) {
    Serial.println("ERR|차트 값이 64개보다 적습니다. 받은 만큼만 그립니다.");
    // 모자란 뒷부분은 0으로 채운다.
    while (count < CHART_POINTS) {
      if (s.chart[count] != 0) { s.chart[count] = 0; changed = true; }
      count++;
    }
  }

  if (!s.hasData) {
    s.hasData = true;
    changed   = true;
  }

  // LED는 지금 보고 있는 슬롯이 아니어도 켜진다.
  // 다른 종목이 임계가 아래로 내려간 것도 알아야 하기 때문이다. 깜빡임 없이 상시 점등.
  digitalWrite(LED_PINS[slotNo], s.alert ? HIGH : LOW);

  // 지금 보고 있는 슬롯의 값이 실제로 바뀌었을 때만 다시 그린다.
  if (slotNo == currentSlot && changed) {
    needsRedraw = true;
  }
}

// 새 글자가 기존과 다르면 복사하고 true를 돌려준다(= 화면을 다시 그려야 한다).
bool copyIfChanged(char* dst, const char* src, size_t dstSize) {
  if (strncmp(dst, src, dstSize - 1) == 0) return false;
  strncpy(dst, src, dstSize - 1);
  dst[dstSize - 1] = '\0';
  return true;
}

// ─────────────────────────────────────────────────────────────
// 버튼 읽기
// 버튼을 누르면 PC 응답을 기다리지 않고 캐시된 값으로 즉시 화면을 바꾼다.
// ─────────────────────────────────────────────────────────────
void readButtons() {
  uint32_t now = millis();

  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    bool isDown = (digitalRead(BUTTON_PINS[i]) == LOW);  // 눌리면 LOW

    if (isDown != buttonWasDown[i]) {
      if (now - buttonChangedAt[i] < DEBOUNCE_MS) continue;  // 흔들림은 무시
      buttonChangedAt[i] = now;
      buttonWasDown[i]   = isDown;

      if (isDown && currentSlot != i) {   // 새로 눌린 순간에만 반응
        currentSlot = i;
        needsRedraw = true;              // 캐시된 값으로 바로 전환
        Serial.print("SEL|");
        Serial.println(i);
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────
// 화면 그리기
// ─────────────────────────────────────────────────────────────
void drawScreen() {
  Slot& s = slots[currentSlot];

  display.clearDisplay();

  if (!s.hasData) {
    // 아직 PC에서 값을 못 받은 슬롯.
    // 기본 글꼴에는 한글이 없어서 "대기중..."을 그리면 글자가 깨진다. 영문으로 표시한다.
    display.setTextSize(1);
    display.setCursor(0, HEADER_Y);
    display.print("SLOT ");
    display.print(currentSlot + 1);
    display.setTextSize(2);
    display.setCursor(0, PRICE_Y);
    display.print("WAITING");
    display.setTextSize(1);
    display.setCursor(0, CHART_TOP + 8);
    display.print("PC data...");
    drawIndicator();
    display.display();
    return;
  }

  // 1) 종목명 (왼쪽)
  display.setTextSize(1);
  display.setCursor(0, HEADER_Y);
  display.print(s.name);

  // 2) 등락률 (오른쪽 정렬). size 1 글자 하나는 가로 6픽셀이다.
  int16_t chgWidth = (int16_t)strlen(s.chg) * 6;
  int16_t chgX     = SCREEN_WIDTH - chgWidth;
  if (chgX < 0) chgX = 0;
  display.setCursor(chgX, HEADER_Y);
  display.print(s.chg);

  // 3) 현재가 (큰 글씨)
  display.setTextSize(2);
  display.setCursor(0, PRICE_Y);
  display.print(s.price);

  // 4) 차트 — 점을 다 찍지 않고 선으로 잇는다.
  drawChart(s.chart);

  // 5) 슬롯 인디케이터
  drawIndicator();

  display.display();
}

// 차트: 값 64개를 x = 0, 2, 4, ..., 126 에 놓고 이웃끼리 선으로 잇는다.
// 값은 0~36 범위로 들어오는데 차트 영역은 30픽셀이라 비율에 맞춰 줄여서 그린다.
void drawChart(const uint8_t* values) {
  int16_t prevX = 0;
  int16_t prevY = chartValueToY(values[0]);

  for (uint8_t i = 1; i < CHART_POINTS; i++) {
    int16_t x = (int16_t)i * 2;
    int16_t y = chartValueToY(values[i]);
    display.drawLine(prevX, prevY, x, y, SSD1306_WHITE);
    prevX = x;
    prevY = y;
  }
}

// 차트 값(0~36)을 화면 세로 위치로 바꾼다. 값이 클수록 위로 간다.
int16_t chartValueToY(uint8_t value) {
  if (value > CHART_MAX) value = CHART_MAX;
  int32_t offset = ((int32_t)value * CHART_HEIGHT) / CHART_MAX;
  return (int16_t)(CHART_BOTTOM - offset);
}

// 슬롯 인디케이터: 네모 4개. 지금 보는 슬롯만 꽉 채운다.
void drawIndicator() {
  const int16_t boxW = 6;
  const int16_t boxH = 5;
  const int16_t gap  = 4;

  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    int16_t x = (int16_t)i * (boxW + gap);
    if (i == currentSlot) {
      display.fillRect(x, INDICATOR_Y, boxW, boxH, SSD1306_WHITE);
    } else {
      display.drawRect(x, INDICATOR_Y, boxW, boxH, SSD1306_WHITE);
    }
  }
}
