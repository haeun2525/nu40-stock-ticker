/*
  I2C address scanner (for NU40 DK)
  ------------------------------------------------------------------
  Use this when the OLED stays dark, to tell "the wiring is wrong" apart from
  "the screen is dead". Upload it, then open Arduino IDE's Tools -> Serial Monitor
  at 115200. It lists the address of every part it finds on the I2C wires.

  Expected result:  "Found: 0x3C" (or 0x3D)
  Nothing found -> swap SDA and SCL, and check VCC (VMOD, 3.3V) and GND.

  Term: an I2C address is the number that tells parts apart when several of them
  share the same 2 wires.

  Note: Arduino only opens a sketch whose file name matches its folder name.
        That's why it lives at tools/i2c_scan/i2c_scan.ino. Open the whole folder.
*/

#include <Wire.h>

void setup() {
  Serial.begin(115200);

  // Wait up to 3 seconds for USB serial to open (continue anyway if it doesn't).
  uint32_t start = millis();
  while (!Serial && millis() - start < 3000) { }

  // The NU40DK board package already sets SDA=P0.26 and SCL=P0.27,
  // so Wire.begin()'s defaults are exactly the pins we use. No need to set them.
  Wire.begin();

  Serial.println();
  Serial.println("=== NU40 DK I2C address scan ===");
  Serial.print("SDA pin: P0."); Serial.println(PIN_WIRE_SDA);
  Serial.print("SCL pin: P0."); Serial.println(PIN_WIRE_SCL);
  Serial.println();
}

void loop() {
  uint8_t found = 0;

  Serial.println("Scanning... (0x01 - 0x7E)");

  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t result = Wire.endTransmission();

    if (result == 0) {   // 0 = a part answered at this address
      Serial.print("  Found: 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
      found++;
    }
  }

  if (found == 0) {
    Serial.println("  Nothing found.");
    Serial.println("  -> Try swapping SDA (P0.26) and SCL (P0.27).");
    Serial.println("  -> Check that OLED VCC goes to VMOD (3.3V) and GND to GND.");
  } else {
    Serial.print("  Found ");
    Serial.print(found);
    Serial.println(" device(s). If you see 0x3C or 0x3D, the OLED is OK.");
  }

  Serial.println();
  delay(3000);   // scan again in 3 seconds
}
