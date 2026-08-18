/*
  I2C 주소 확인용 스케치 (NU40 DK 전용)
  ------------------------------------------------------------------
  OLED가 안 켜질 때 "배선이 잘못됐는지, 화면이 죽었는지"를 가르는 도구다.
  이걸 보드에 올리고 아두이노 IDE의 [도구] → [시리얼 모니터]를 115200으로 열면,
  연결된 부품의 주소를 찾아서 알려준다.

  기대하는 결과:  "찾음: 0x3C" (또는 0x3D)
  아무것도 안 나오면 → SDA와 SCL을 서로 바꿔 꽂아보고, 3V3·GND도 확인한다.

  용어: I2C 주소란 선 2가닥에 여러 부품을 붙였을 때 서로를 구분하는 번호다.

  ※ 아두이노는 스케치 파일과 폴더 이름이 같아야 열린다.
     그래서 tools/i2c_scan/i2c_scan.ino 로 넣어뒀다. 폴더째로 열면 된다.
*/

#include <Wire.h>

void setup() {
  Serial.begin(115200);

  // USB 시리얼이 열릴 때까지 최대 3초 기다린다(안 열려도 그냥 진행).
  uint32_t start = millis();
  while (!Serial && millis() - start < 3000) { }

  // NU40DK는 보드 패키지에 SDA=P0.26, SCL=P0.27 이 이미 지정돼 있어서
  // Wire.begin() 기본값이 곧 우리가 쓰는 핀이다. 따로 지정할 필요가 없다.
  Wire.begin();

  Serial.println();
  Serial.println("=== NU40 DK I2C 주소 검사 ===");
  Serial.print("SDA 핀: P0."); Serial.println(PIN_WIRE_SDA);
  Serial.print("SCL 핀: P0."); Serial.println(PIN_WIRE_SCL);
  Serial.println();
}

void loop() {
  uint8_t found = 0;

  Serial.println("검사 중... (0x01 ~ 0x7E)");

  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t result = Wire.endTransmission();

    if (result == 0) {   // 0 = 그 주소에 부품이 응답했다는 뜻
      Serial.print("  찾음: 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
      found++;
    }
  }

  if (found == 0) {
    Serial.println("  아무것도 못 찾았습니다.");
    Serial.println("  → SDA(P0.26)와 SCL(P0.27)을 서로 바꿔 꽂아보세요.");
    Serial.println("  → OLED의 VCC가 3V3, GND가 GND에 제대로 꽂혔는지 확인하세요.");
  } else {
    Serial.print("  총 ");
    Serial.print(found);
    Serial.println("개 찾았습니다. 0x3C 또는 0x3D 가 보이면 OLED 정상입니다.");
  }

  Serial.println();
  delay(3000);   // 3초 뒤에 다시 검사
}
