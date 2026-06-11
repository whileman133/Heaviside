# Component Library

This document lists every component Heaviside currently implements, the explicit
(structured) options each one exposes in the properties inspector, and a
representative list of CircuiTikZ symbols that are **not** yet implemented.

The underlying symbol set is [CircuiTikZ](https://github.com/circuitikz/circuitikz)
(the app renders against **v1.6.7**). The registry that drives the palette,
codegen, and canvas lives in [`components/definitions.json`](components/definitions.json)
and [`app/components/registry.py`](app/components/registry.py).

---

## Conventions: options every component shares

The "Explicit options" column below lists only the **structured** controls unique
to a component (a checkbox, spinbox, or dropdown). In addition, **every** placed
component supports the following, so they are not repeated per row:

- **Rotation** — in 90° steps, and **horizontal/vertical mirror**.
- **Stroke width** — the outline width in points (all kinds except pure text).
- **Free-form CircuiTikZ options** — a text field for any raw option string (e.g.
  `l=$R_1$, v=$V_s$`). The accepted **label slots** depend on the component:
  - **Two-terminal passives, sources, meters** accept a label plus **voltage**
    and/or **current** annotations: `l, l_, v, v^, i, i_` (sources are `v`-only or
    `i`-only as appropriate).
  - **Transistors, gates, op-amps, supplies** accept a label only (`l`).
  - **Grounds, switches** are unlabeled (annotate via a nearby node/wire).
- **Drawing primitives** (rectangle, circle, text) additionally expose **fill
  color**, **line style** (solid/dashed/dotted), and font controls.

---

## Implemented components

### Resistors

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Resistor | `R` | `R` | — |
| Variable Resistor | `vR` | `vR` | — |
| Thermistor | `thermistor` | `thermistor` | — |
| Thermistor (NTC) | `thermistor ntc` | `thermistor ntc` | — |
| Thermistor (PTC) | `thermistor ptc` | `thermistor ptc` | — |
| Photoresistor (LDR) | `photoresistor` | `photoresistor` | — |
| Varistor | `varistor` | `varistor` | — |
| Memristor | `memristor` | `memristor` | — |
| Resistor (European) | `eR` | `european resistor` | — |
| Potentiometer (European) | `epot` | `european potentiometer` | — |
| Thermistor (European) | `ethermistor` | `european resistive sensor` | — |
| Variable Resistor (European) | `evR` | `variable european resistor` | — |
| Potentiometer | `pR` | `pR` | — |

### Capacitors

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Capacitor | `C` | `C` | — |
| Electrolytic Capacitor | `eC` | `eC` | — |
| Polarized Capacitor | `pC` | `pC` | — |
| Variable Capacitor | `vC` | `vC` | — |
| Ferroelectric Capacitor | `feC` | `feC` | — |
| Curved Capacitor | `cC` | `cC` | — |
| Capacitive Sensor | `sC` | `sC` | — |
| Varactor | `varcap` | `varcap` | — |
| Piezoelectric Crystal | `piezoelectric` | `piezoelectric` | — |
| Constant Phase Element | `cpe` | `cpe` | — |

### Inductors

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Inductor | `L` | `L` | — |
| Inductor (Cute) | `cuteL` | `cute inductor` | — |
| Inductor (European) | `eL` | `european inductor` | — |
| Variable Inductor | `vL` | `vL` | — |
| Inductive Sensor | `sL` | `sL` | — |
| Transformer | `transformer` | `transformer` | 4 winding-polarity **dots** |
| Transformer (Iron Core) | `transformer core` | `transformer core` | 4 winding-polarity **dots** |
| Transformer (Cute) | `cute transformer` | `transformer` | 4 winding-polarity **dots** |
| Transformer (Cute, Iron Core) | `cute transformer core` | `transformer core` | 4 winding-polarity **dots** |
| Transformer (European) | `european transformer` | `transformer` | 4 winding-polarity **dots** |
| Transformer (European, Iron Core) | `european transformer core` | `transformer core` | 4 winding-polarity **dots** |
| Choke | `choke` | `cute choke` | — |

### Diodes

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Diode | `D` | `D` | **filled** |
| Zener Diode | `zD` | `zD` | **filled** |
| Schottky Diode | `sD` | `sD` | **filled** |
| Tunnel Diode | `tD` | `tD` | **filled** |
| TVS Diode | `zzD` | `zzD` | **filled** |
| LED | `leD` | `leD` | **filled** |
| Photodiode | `photodiode` | `photodiode` | — |
| Thyristor (SCR) | `thyristor` | `thyristor` | 3rd terminal: wireable **gate** |
| TRIAC | `triac` | `triac` | 3rd terminal: wireable **gate** |

### Transistors

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| NPN BJT | `npn` | `npn` | — |
| PNP BJT | `pnp` | `pnp` | — |
| N-MOSFET (enh.) | `nigfete` | `nigfete` | **body_diode** |
| N-MOSFET (depl.) | `nigfetd` | `nigfetd` | **body_diode** |
| P-MOSFET (enh.) | `pigfete` | `pigfete` | **body_diode** |
| P-MOSFET (depl.) | `pigfetd` | `pigfetd` | **body_diode** |
| N-MOSFET (4-terminal) | `nfet` | `nfet` | **body_diode** |
| P-MOSFET (4-terminal) | `pfet` | `pfet` | **body_diode** |
| N-MOS (Simplified) | `nmos` | `nmos` | — |
| P-MOS (Simplified) | `pmos` | `pmos` | — |
| N-MOS Depletion (Simplified) | `nmosd` | `nmosd` | — |
| P-MOS Depletion (Simplified) | `pmosd` | `pmosd` | — |
| N-JFET | `njfet` | `njfet` | — |
| P-JFET | `pjfet` | `pjfet` | — |
| N-IGBT | `nigbt` | `nigbt` | — |
| P-IGBT | `pigbt` | `pigbt` | — |
| ISFET | `isfet` | `isfet` | — |

### Tubes

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Triode | `triode` | `triode` | — |
| Vacuum Diode | `diodetube` | `diodetube` | — |
| Tetrode | `tetrode` | `tetrode` | — |
| Pentode | `pentode` | `pentode` | — |

### Amplifiers

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Op-Amp | `op amp` | `op amp` | — |
| Fully-Differential Op-Amp | `fd op amp` | `fd op amp` | — |
| Transconductance Amplifier | `gmamp` | `gm amp` | — |
| Instrumentation Amplifier | `instamp` | `inst amp` | — |
| Schmitt Trigger | `schmitt` | `schmitt` | — |
| Schmitt Trigger (Inverting) | `invschmitt` | `invschmitt` | — |

### Blocks

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Amplifier | `amp` | `amp` | — |
| ADC | `adc` | `adc` | — |
| DAC | `dac` | `dac` | — |
| Lowpass Filter | `lowpass` | `lowpass` | — |
| Highpass Filter | `highpass` | `highpass` | — |
| Bandpass Filter | `bandpass` | `bandpass` | — |
| Allpass Filter | `allpass` | `allpass` | — |
| Phase Shifter | `phaseshifter` | `phaseshifter` | — |
| Detector | `detector` | `detector` | — |
| VCO | `vco` | `vco` | — |
| Gyrator | `gyrator` | `gyrator` | — |

### Gates (Am)

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| NOT Gate (Inverter) | `not` | `not port` | Size |
| Buffer | `buffer` | `buffer port` | Size |
| AND Gate | `and` | `and port` | **Inputs (2–16)**, Size |
| NAND Gate | `nand` | `nand port` | **Inputs (2–16)**, Size |
| OR Gate | `or` | `or port` | **Inputs (2–16)**, Size |
| NOR Gate | `nor` | `nor port` | **Inputs (2–16)**, Size |
| XOR Gate | `xor` | `xor port` | **Inputs (2–16)**, Size |
| XNOR Gate | `xnor` | `xnor port` | **Inputs (2–16)**, Size |

### Gates (Eu)

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| NOT Gate (European) | `enot` | `european not port` | Size |
| Buffer Gate (European) | `ebuffer` | `european buffer port` | Size |
| AND Gate (European) | `eand` | `european and port` | **Inputs (2–16)**, Size |
| NAND Gate (European) | `enand` | `european nand port` | **Inputs (2–16)**, Size |
| OR Gate (European) | `eor` | `european or port` | **Inputs (2–16)**, Size |
| NOR Gate (European) | `enor` | `european nor port` | **Inputs (2–16)**, Size |
| XOR Gate (European) | `exor` | `european xor port` | **Inputs (2–16)**, Size |
| XNOR Gate (European) | `exnor` | `european xnor port` | **Inputs (2–16)**, Size |

### Logic

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| D Flip-Flop | `flipflop D` | `flipflop D` | Size |
| T Flip-Flop | `flipflop T` | `flipflop T` | Size |
| SR Flip-Flop | `flipflop SR` | `flipflop SR` | Size |
| JK Flip-Flop | `flipflop JK` | `flipflop JK` | Size |
| Multiplexer | `mux` | `muxdemux` | **Inputs (2–16)**, **Selects (1–4)**, Size |
| Demultiplexer | `demux` | `muxdemux` | **Outputs (2–16)**, **Selects (1–4)**, Size |
| ALU | `ALU` | `ALU` | Size |
| Adder | `adder` | `one bit adder` | Size |

### Switches

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Switch (NO) | `nos` | `nos` | — |
| Switch (NC) | `ncs` | `ncs` | — |
| Closing Switch | `closing` | `closing switch` | — |
| Opening Switch | `opening` | `opening switch` | — |
| SPST Switch | `spst` | `spst` | — |
| Push Button | `pushbutton` | `push button` | — |
| SPDT Switch | `spdt` | `spdt` | — |
| Switch (NO, Cute) | `cute open switch` | `cute open switch` | — |
| Switch (NC, Cute) | `cute closed switch` | `cute closed switch` | — |
| SPDT Switch (Cute, Up) | `cute spdt up` | `cute spdt up` | — |
| SPDT Switch (Cute, Down) | `cute spdt down` | `cute spdt down` | — |
| SPDT Switch (Cute, Mid) | `cute spdt mid` | `cute spdt mid` | — |
| Rotary Switch | `rotaryswitch` | `rotaryswitch` | — |
| Reed Switch | `reed` | `reed` | — |
| Toggle Switch | `toggle switch` | `toggle switch` | — |

### Sources

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Voltage Source | `V` | `V` | — |
| Current Source | `I` | `I` | — |
| AC Voltage Source | `vsourcesin` | `vsourcesin` | — |
| AC Current Source | `isourcesin` | `isourcesin` | — |
| Square Voltage Source | `vsourcesquare` | `vsourcesquare` | — |
| Triangle Voltage Source | `vsourcetri` | `vsourcetri` | — |
| Noise Voltage Source | `vsourceN` | `vsourceN` | — |
| DC Voltage Source | `dcvsource` | `dcvsource` | — |
| DC Current Source | `dcisource` | `dcisource` | — |
| VCVS | `cV` | `cV` | — |
| VCCS | `cI` | `cI` | — |
| Current Source (European) | `eI` | `european current source` | — |
| Voltage Source (European) | `eV` | `european voltage source` | — |
| Controlled Current Source (European) | `ecI` | `european controlled current source` | — |
| Controlled Voltage Source (European) | `ecV` | `european controlled voltage source` | — |

### Supplies

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Cell | `battery1` | `battery1` | — |
| VCC | `vcc` | `vcc` | — |
| VDD | `vdd` | `vdd` | — |
| VEE | `vee` | `vee` | — |
| VSS | `vss` | `vss` | — |
| Battery | `battery` | `battery` | — |

### Instruments

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Ammeter | `ammeter` | `ammeter` | — |
| Voltmeter | `voltmeter` | `voltmeter` | — |
| Ohmmeter | `ohmmeter` | `ohmmeter` | — |
| Oscilloscope | `oscope` | `oscope` | — |
| Meter | `rmeter` | `rmeter` | — |

### Grounds

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Ground | `ground` | `ground` | — |
| Reference Ground | `rground` | `rground` | — |
| Signal Ground | `sground` | `sground` | — |
| Noiseless Ground | `nground` | `nground` | — |
| Protective Earth | `pground` | `pground` | — |
| Chassis Ground | `cground` | `cground` | — |
| Earth Ground | `eground` | `eground` | — |

### Transducers

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Loudspeaker | `loudspeaker` | `loudspeaker` | — |
| Microphone | `mic` | `mic` | — |
| Buzzer | `buzzer` | `buzzer` | — |

### Antennas

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Antenna | `antenna` | `antenna` | — |

### Misc

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Fuse | `fuse` | `fuse` | — |
| Fuse (Asymmetric) | `afuse` | `afuse` | — |
| Lamp | `lamp` | `lamp` | — |
| Light Bulb | `bulb` | `bulb` | — |
| SQUID | `squid` | `squid` | — |
| Jumper | `jumper` | `jumper` | — |
| Transmission Line | `tline` | `tline` | — |
| Generic Bipole | `bipole` | `twoport` | — |

### Annotations

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Voltage Annotation | `open` | `open` | — |
| Current Annotation | `short` | `short` | — |

### Drawing

| Component | Kind | CircuiTikZ shape | Explicit options |
|---|---|---|---|
| Text | `text_node` | `text_node` | — |
| Rectangle | `rect` | `rectangle` | — |
| Circle | `circle` | `circle` | — |

---

## Not yet implemented

The big library build-out covered the bulk of the commonly-used CircuiTikZ parts.
What remains falls into a few groups — mostly things that need more than a data
entry (parametric pin layouts or new canvas items), plus some niche or
near-duplicate symbols. As before, every keyword below was checked against the
installed CircuiTikZ; this is representative, not an exhaustive enumeration.

### Needs new architecture (deferred)
- **Configurable IC packages** — `dipchip` / `qfpchip`, with user-set pin counts
  per side (parametric, like the mux but in two dimensions).
- **Multi-port RF / DSP block library** — `mixer`, `circulator`, `oscillator`,
  `wilkinson`, `match`, directional couplers, etc. (circle/box shapes whose
  several ports sit at compass points; need per-port terminal handling).

### Remaining discrete parts (could be added later)
- **Tubes:** `magnetron`, `dynode`, and the second cathode/filament leg
  (`cathode 2`). The control, screen, and suppressor grids **are** exposed
  (tetrode: control + screen; pentode: control + screen + suppressor).
- **Transistors:** HEMTs and a few exotics are **not present** in the installed
  CircuiTikZ version, so they are intentionally absent (not invented).
- **Mechanical:** rotating machines (`elmech` motor/generator), bell.
- **Antennas:** the directional `rxantenna` / `txantenna` (the omni `antenna` is in).
- **Grounds:** the `tground` / `tlground` variants (seven ground styles already ship).
- **Connectors / terminals:** `bnc`, jacks, plugs, and the bare connection dots
  `ocirc` / `circ` (the canvas already draws junction/solder dots as a wire feature).
