# BIO-Coprocessor — Pinout Diagram
### GF180MCU 0.5×1 Slot (1936 × 5122 µm)

```
                              N O R T H
              ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
              │ANA5 │ANA4 │ANA3 │ VSS │ VDD │ANA2 │ANA1 │ANA0 │
              │ NC  │ NC  │ NC  │     │     │ NC  │ NC  │ NC  │
              ├─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┤
              │                                                │
  ┌───────────┤                                                ├───────────┐
  │ NC     [43│                                                │0]  SPI    │
  │ NC     [42│                                                │    MISO   │
  │ NC     [41│                                                │1]  IRQ[0] │
  │ VSS       │                                                │       VSS │
  │ VDD       │                                                │       VDD │
  │ NC     [40│                                                │2]  IRQ[1] │
  │ NC     [39│                                                │3]  GPIO0  │
  │ NC     [38│                                                │4]  GPIO1  │
  │ NC     [37│                                                │5]  GPIO2  │
  │ NC     [36│                                                │6]  GPIO3  │
  │ NC     [35│                                                │7]  GPIO4  │
  │GPIO31  [34│                                                │8]  GPIO5  │
  │GPIO30  [33│          B I O - C o p r o c e s s o r         │9]  GPIO6  │
  │ VSS       │                                                │10] GPIO7  │
  │ VDD       │            Dual-Core PicoRV32                  │       VSS │
  │GPIO29  [32│            8× SRAM (4 KB)                      │       VDD │
  │GPIO28  [31│            32 GPIO Bidir                       │11] GPIO8  │
  │GPIO27  [30│            GF180MCU 5V                         │12] GPIO9  │
  │GPIO26  [29│            10 MHz                              │13] GPIO10 │
  │GPIO25  [28│                                                │14] GPIO11 │
  │GPIO24  [27│                                                │15] GPIO12 │
  │GPIO23  [26│                                                │16] GPIO13 │
  │GPIO22  [25│                                                │17] GPIO14 │
  │GPIO21  [24│                                                │18] GPIO15 │
  │ VSS       │                                                │19] GPIO16 │
  │ VDD       │                                                │       VSS │
  │GPIO20  [23│                                                │       VDD │
  │GPIO19  [22│                                                │20] GPIO17 │
  │           │                                                │21] GPIO18 │
  └───────────┤                                                ├───────────┘
              │                                                │
              ├─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┤
              │ CLK │ RST │SCLK │ VSS │ VDD │MOSI │CS_N │SPARE│
              │     │  ↓  │     │     │     │     │ PU  │ PU  │
              └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
                              S O U T H

  W                                                            E
  E                                                            A
  S                                                            S
  T                                                            T
```

### Signal Map

```
┌──────────┬────────────────────────────────────────────────────────┐
│   SOUTH  │  System + SPI Control                                 │
├──────────┼────────────────────────────────────────────────────────┤
│  CLK     │  Chip clock input (10 MHz)                            │
│  RST     │  Active-low reset (async assert, sync release)        │
│  SCLK    │  SPI clock input (≤1.2 MHz)                           │
│  MOSI    │  SPI master-out, slave-in                             │
│  CS_N    │  SPI chip select, active low (internal pull-up)       │
│  SPARE   │  Test point input (internal pull-up)                  │
├──────────┼────────────────────────────────────────────────────────┤
│   EAST   │  SPI Data + IRQ + GPIO[0:18]                          │
├──────────┼────────────────────────────────────────────────────────┤
│  [0]     │  SPI MISO — data output to host                      │
│  [1]     │  IRQ[0] — interrupt from core 0                      │
│  [2]     │  IRQ[1] — interrupt from core 1                      │
│  [3:21]  │  GPIO[0:18] — bidirectional, directly mapped         │
├──────────┼────────────────────────────────────────────────────────┤
│   WEST   │  GPIO[19:31] + Unused                                 │
├──────────┼────────────────────────────────────────────────────────┤
│  [22:34] │  GPIO[19:31] — bidirectional, directly mapped        │
│  [35:43] │  NC — input buffer off, pulled low                   │
├──────────┼────────────────────────────────────────────────────────┤
│   NORTH  │  Unused                                               │
├──────────┼────────────────────────────────────────────────────────┤
│  [0:5]   │  Analog pads — unconnected                           │
├──────────┼────────────────────────────────────────────────────────┤
│  POWER   │  8× VDD (5V) + 8× VSS distributed around all sides  │
└──────────┴────────────────────────────────────────────────────────┘
```

### Pad Counts

```
Active signals:  41  (32 GPIO + 4 SPI + 2 IRQ + CLK + RST + SPARE)
Power pads:      16  (8× VDD + 8× VSS)
Unused bidir:     9  (input off, pull-down)
Unused analog:    6
Total pads:      72
```
