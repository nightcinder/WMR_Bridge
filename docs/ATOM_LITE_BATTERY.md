# M5Stack ATOM Lite + Atomic Battery Base: Battery Life Investigation

## Executive Summary

This investigation evaluates the battery life, electrical efficiency, thermal
characteristics, and practical feasibility of powering the WMR Bridge on an
M5Stack ATOM Lite using the M5Stack Atomic Battery Base (SKU A151).

### Verdict

- Dual-Controller Streaming (Standard Bridge): about 45 to 52 minutes of
  continuous operation.
- Single-Controller Streaming (Single-Bridge Variant): about 58 to 65 minutes of
  continuous operation.
- Idle / Advertising (No Controllers Connected): about 2.0 to 2.2 hours.
- Recommendation: not suitable for standard VR gaming sessions (typically 1.5 to
  3 hours). The 200 mAh cell is depleted in under an hour due to the continuous
  active RF duty cycle of dual-mode Bluetooth (simultaneous Classic BT BR/EDR HID
  polling plus BLE GATT streaming) combined with compound conversion losses
  (boost converter plus linear LDO regulator). It is viable only for brief bench
  tests, portable diagnostics, or wireless demonstrations (under 30 minutes).
  For room-scale VR sessions, an external USB-C power bank (for example 5,000 mAh
  providing more than 25 hours) or a dedicated larger battery base (at least
  1,000 mAh) is required.

---

## 1. Hardware Architecture & Power Specifications

### 1.1 M5Stack ATOM Lite

- Microcontroller: Espressif ESP32-PICO-D4 System-in-Package (dual-core Xtensa
  LX6 at 240 MHz, 4 MB SPI flash, 520 KB SRAM).
- Radio Subsystem: 2.4 GHz 802.11b/g/n Wi-Fi and Bluetooth v4.2 BR/EDR plus BLE,
  driven by a shared physical baseband and radio controller (BTDM subsystem).
- Power Regulation:
  - Power input: 5 V via USB-C or base pins (`5V_IN`).
  - On-board Regulator: Low-Dropout (LDO) linear regulator stepping down 5.0 V
    to 3.3 V for the ESP32 and peripheral circuitry.
  - Form factor: $24 \times 24 \times 10\text{ mm}$, weight about 12 g.

### 1.2 M5Stack Atomic Battery Base (SKU A151)

- Battery Cell: built-in rechargeable Lithium Polymer (LiPo), 200 mAh, 3.7 V
  nominal (4.20 V full charge, about 3.00 V cutoff).
- Nominal Energy:
  $$E_{\text{nominal}} = 3.7\text{ V} \times 0.200\text{ Ah} = 0.74\text{ Wh} = 740\text{ mWh} = 2,664\text{ J}$$
- Boost Converter: ETA9085E10 synchronous boost IC:
  - Converts cell voltage (3.0 to 4.2 V) to a regulated 5.0 V output on the
    base header.
  - Rated output current: 300 mA continuous (500 mA peak).
  - Typical efficiency: about 86% to 90% at 100 to 150 mA output.
- Charging Management: LGS4056HDA linear charger IC:
  - Charging current: about 223 mA from 5 V USB input.
  - Charge time: about 60 to 75 minutes for a full recharge from empty.
- Controls & Telemetry:
  - Mechanical ON/OFF switch: enables 5 V boost output or isolates output for
    charging.
  - Fuel gauge telemetry: internal 1/2 resistor divider connects battery positive
    terminal to GPIO 33 (`G33` / `ADC1_CH5`), allowing software monitoring of
    battery voltage ($V_{\text{bat}} = 2 \times V_{\text{G33}}$).
  - Physical indicators: 4 red LEDs for state of charge, 1 bi-color LED for
    charging state.
  - Form factor: $24 \times 24 \times 23.95\text{ mm}$, weight about 9.4 g.
  - Combined stack: $24 \times 24 \times 34\text{ mm}$, weight about 21.4 g.

---

## 2. Power Conversion Chain & System Efficiency

A critical architectural consideration is the cascaded dual-stage power regulation
between the LiPo cell and the ESP32:

```
[ 3.7V 200mAh LiPo ]
         |
         v (ETA9085 Boost Converter: ~88% efficiency)
    [ 5.0V Rail ]
         |
         v (ATOM Lite On-Board LDO: 3.3V / 5.0V = 66% efficiency)
   [ 3.3V ESP32 Rail ]
```

### 2.1 Stage 1: Boost Converter Efficiency

The ETA9085 boosts battery voltage (3.0 to 4.2 V) to 5.0 V. At an operating
current of 100 to 160 mA on the 5 V output, efficiency is about 88%.

- Usable energy delivered to the 5 V rail:
  $$E_{5\text{V}} = 740\text{ mWh} \times 0.88 \approx 651\text{ mWh}$$
- Equivalent capacity at 5.0 V:
  $$Q_{5\text{V}} = \frac{651\text{ mWh}}{5.0\text{ V}} \approx 130.2\text{ mAh}$$

### 2.2 Stage 2: Linear LDO Regulator Efficiency

The ATOM Lite uses a linear LDO regulator rather than a switching buck converter.
In a linear regulator, input current strictly equals output current (neglecting
negligible quiescent current):
$$I_{5\text{V}} \approx I_{3.3\text{V}}$$
The electrical efficiency of this stage is fundamentally constrained by the
voltage ratio:
$$\eta_{\text{LDO}} = \frac{V_{\text{out}}}{V_{\text{in}}} = \frac{3.3\text{ V}}{5.0\text{ V}} = 66.0\%$$
The remaining 34.0% of the power is dissipated purely as heat:
$$P_{\text{loss, LDO}} = (5.0\text{ V} - 3.3\text{ V}) \times I_{3.3\text{V}} = 1.7\text{ V} \times I_{3.3\text{V}}$$
At an active current draw of 150 mA, the LDO continuously generates:
$$P_{\text{loss}} = 1.7\text{ V} \times 0.150\text{ A} = 0.255\text{ W} \quad (255\text{ mW})$$
inside the miniature sealed enclosure.

### 2.3 Total Conversion Efficiency

The end-to-end efficiency from the battery chemical cell to the ESP32 3.3 V rail
is:
$$\eta_{\text{total}} = \eta_{\text{boost}} \times \eta_{\text{LDO}} \approx 0.88 \times 0.66 \approx 58.1\%$$
Over 41% of the battery's energy is lost as heat before doing any computational
or radio work.

---

## 3. Operating Current Profiles & Battery Life Calculations

### 3.1 Workload Breakdown

WMR Bridge on ESP32 operates the BTDM (Bluetooth Dual-Mode) subsystem under
heavy radio utilization:

- Classic Bluetooth (BR/EDR): operates as master on 1 or 2 ACL links,
  maintaining sniff or active polling and receiving 46-byte HID input reports at
  about 150 to 180 Hz per controller (about 300 to 360 packets/sec for dual
  controllers).
- BLE GATT Server: advertises or transmits GATT notifications (`FF12` decoded
  56-byte records or `FF14` batched records) to the connected companion phone at
  high throughput.
- CPU: dual Xtensa cores running BTstack, FreeRTOS, frame reassembly,
  CRC/decoding, and circular queue management.
- Power Management: modem-sleep and light-sleep are inhibited during active
  streaming because the radio must service incoming Classic BT slots every few
  milliseconds.

### 3.2 Operating Regimes

| Operating State | Average $I_{3.3\text{V}}$ | Average $I_{5\text{V}}$ | Current at Cell (3.7 V) | Usable Capacity (C-rate adjusted) | Calculated Runtime |
|---|---|---|---|---|---|
| 1. Idle / Standby (BLE advertising, no controllers) | 60 mA | 60 mA | $\frac{5.0 \times 60}{3.7 \times 0.88} \approx 92\text{ mA}$ (0.46C) | 195 mAh | 2.12 hours (about 127 min) |
| 2. Single-Controller Streaming (1 WMR hand, FF12/FF14 to phone) | 120 mA | 120 mA | $\frac{5.0 \times 120}{3.7 \times 0.88} \approx 184\text{ mA}$ (0.92C) | 185 mAh | 1.01 hours (about 60 min) |
| 3. Dual-Controller Streaming (2 WMR hands, FF14 batched to phone) | 150 mA | 150 mA | $\frac{5.0 \times 150}{3.7 \times 0.88} \approx 230\text{ mA}$ (1.15C) | 180 mAh | 0.78 hours (about 47 min) |

Note on High Discharge Rate (above 1C):

At dual-controller load (about 230 mA cell draw), the 200 mAh cell discharges at
about 1.15C. Due to cell internal resistance ($R_{\text{int}} \approx 150$ to
$250\text{ m}\Omega$), cell voltage sags under load. It reaches the ETA9085
low-voltage cutoff (about 3.0 V) before 100% nominal capacity is extracted.
Real-world usable capacity drops to about 180 mAh (90%).

### 3.3 Summary of Battery Life Expectations

- Dual-Controller Active Session: 45 to 52 minutes.
- Single-Controller Active Session: 58 to 65 minutes.
- Recharge Time: 60 to 75 minutes via USB-C (charge-to-play ratio is above
  1.2 : 1).

---

## 4. Thermal & Reliability Analysis

### 4.1 Internal Heat Generation

Within the $24 \times 24 \times 34\text{ mm}$ stacked plastic housing, total power
dissipation during dual-controller streaming is:

- ESP32-PICO-D4 active power: $3.3\text{ V} \times 150\text{ mA} = 495\text{ mW}$
- ATOM Lite LDO dissipation: $(5.0\text{ V} - 3.3\text{ V}) \times 150\text{ mA} = 255\text{ mW}$
- ETA9085 boost converter losses: $P_{\text{in}} - P_{\text{out}} \approx 852\text{ mW} - 750\text{ mW} = 102\text{ mW}$
- Battery internal $I^2 R$ loss: $(0.23\text{ A})^2 \times 0.2\text{ }\Omega \approx 11\text{ mW}$
- Total Dissipated Heat: about 863 mW

### 4.2 Thermal Implications

- Temperature Rise: dispersing nearly 0.9 W in an enclosed plastic casing with no
  airflow or heatsinking causes internal temperatures to rise into the range of
  45 to 55 degrees C.
- Battery Degradation: operating LiPo cells at above 45 degrees C under at least
  1C discharge acceleration accelerates electrolyte decomposition. That increases
  internal resistance and rapidly reduces cell cycle life.
- LDO Thermal Throttling: although the on-board LDO regulator typically handles
  up to 500 mA, thermal dissipation of 255 mW in a miniature SOT-23 / SOT-89
  package pushes the junction temperature close to thermal limiting if ambient
  temperature is high.

---

## 5. Telemetry & Firmware Integration Options

If the Atomic Battery Base is used, battery voltage monitoring can be integrated
without additional hardware components:

1. Pin Mapping:
   - Pin: GPIO 33 (`ADC1_CH5`).
   - Circuit: integrated 1/2 resistor divider between battery positive and
     ground.
   - ADC Measurement: $V_{\text{ADC}} = V_{\text{bat}} / 2$.
2. Voltage Conversion:
   $$V_{\text{bat}} = 2 \times V_{\text{ADC}} = 2 \times \left(\frac{\text{raw\_adc}}{4095}\right) \times V_{\text{ref}}$$
3. State of Charge Mapping:
   - 4.20 V: 100% (fully charged)
   - 3.82 V: about 75%
   - 3.62 V: about 50%
   - 3.48 V: about 25%
   - 3.00 V: 0% (shutdown cutoff)
4. GATT Reporting:
   The bridge could optionally report its own battery status over the BLE `FF10`
   GATT service (for example via an auxiliary characteristic or adding an
   optional bridge status block).

---

## 6. Comparison with Power Alternatives

| Power Architecture | Usable Capacity | Runtime (Dual Controllers) | Runtime (Single Controller) | Form Factor / Weight | Feasibility / Recommendation |
|---|---|---|---|---|---|
| Atomic Battery Base (200 mAh) | 180 mAh usable | about 47 minutes | about 60 minutes | $24\times 24\times 34\text{ mm}$, 21.4 g | Inadequate for VR gaming; suitable only for brief bench tests or demos. |
| ATOM TailBat (190 mAh) | 170 mAh usable | about 44 minutes | about 56 minutes | Clip-on tail, 20 g | Same battery limitation; IP5303 light-load auto-shutoff is also a risk. |
| Direct 3.3V Buck Modification (Custom 500 mAh base) | 460 mAh usable | about 2.8 hours | about 3.5 hours | Custom PCB base, about 35 g | Bypasses 34% LDO loss; provides acceptable session time. |
| Wearable USB-C Power Bank (5,000 mAh pocket/clip) | 3,800 mAh at 5V | about 25 hours | about 31 hours | Pocket size, about 100 g plus short cable | Recommended for untethered VR: standard off-the-shelf hardware, zero battery anxiety. |
| Tethered USB-C / Host PC | Infinite | Continuous | Continuous | Wired USB cable | Standard bench configuration. |

---

## 7. Conclusions & Recommendations

1. Sub-One-Hour Life: with two WMR controllers streaming to a phone, the 200 mAh
   Atomic Battery Base provides 45 to 52 minutes of operation. A single
   controller lasts about 60 minutes.
2. Asymmetric Cycle Time: charging takes 60 to 75 minutes, which is longer than
   the discharge time. Running continuously on battery bases would require
   swapping between multiple bases.
3. Double Conversion Inefficiency: the boost-to-5V followed by LDO-down-to-3.3V
   wastes 42% of the battery capacity.
4. Practical Use Cases:
   - Bench test / calibration sanity check: excellent. Allows testing the
     bridge without a tangled USB cable.
   - Tradeshow / quick wireless demo: feasible for demos up to 30 minutes.
   - Regular VR gaming / tracking: not recommended. A standard 5,000 mAh mobile
     battery pack worn on a pocket or waistband connected via a thin USB-C cable
     provides over 25 hours of continuous operation with none of the thermal or
     battery sag limitations.
