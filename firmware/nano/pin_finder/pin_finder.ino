// MORPH pin finder for the Arduino Nano.
//
// Prints A0-A7 (0-1023) and D2-D12 (1 = HIGH / open, 0 = LOW / pressed) over USB
// serial at 115200 baud, 4 times per second. Move ONE control at a time and
// watch which pin changes; values that changed since the previous line get a '*'.
//
// Read-only: every digital pin is an INPUT with the internal pull-up, so nothing
// is driven. D0/D1 (USB serial) and D13 (on-board LED) are not touched.

const uint8_t FIRST_DIGITAL = 2;
const uint8_t LAST_DIGITAL = 12;
const uint8_t ANALOG_PINS[8] = {A0, A1, A2, A3, A4, A5, A6, A7};
const int ANALOG_CHANGE = 40;          // smaller changes are noise (unconnected pins wander)
const unsigned long PERIOD_MS = 250;

int lastAnalog[8];
uint8_t lastDigital[LAST_DIGITAL + 1];
bool firstLine = true;

void setup() {
  Serial.begin(115200);
  for (uint8_t pin = FIRST_DIGITAL; pin <= LAST_DIGITAL; pin++) {
    pinMode(pin, INPUT_PULLUP);
  }
  Serial.println(F("MORPH pin finder. Move ONE control at a time; * = changed since the last line."));
  Serial.println(F("A0-A7: 0-1023. D2-D12: 1 = HIGH/open, 0 = LOW/pressed (internal pull-ups on)."));
}

void loop() {
  for (uint8_t i = 0; i < 8; i++) {
    analogRead(ANALOG_PINS[i]);  // throw-away read: lets the ADC settle after switching pins
    const int value = analogRead(ANALOG_PINS[i]);
    const bool changed = !firstLine && abs(value - lastAnalog[i]) >= ANALOG_CHANGE;
    lastAnalog[i] = value;
    Serial.print('A');
    Serial.print(i);
    Serial.print('=');
    Serial.print(value);
    Serial.print(changed ? F("* ") : F("  "));
  }
  Serial.print(F("| "));
  for (uint8_t pin = FIRST_DIGITAL; pin <= LAST_DIGITAL; pin++) {
    const uint8_t value = digitalRead(pin);
    const bool changed = !firstLine && value != lastDigital[pin];
    lastDigital[pin] = value;
    Serial.print('D');
    Serial.print(pin);
    Serial.print('=');
    Serial.print(value);
    Serial.print(changed ? F("* ") : F("  "));
  }
  Serial.println();
  firstLine = false;
  delay(PERIOD_MS);
}
