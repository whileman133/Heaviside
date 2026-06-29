# CircuiTikZ components (scraped from the manual)

403 components across 27 categories, scraped from `circuitikzmanual.tex` by `components/scrape_manual.py` (manual + source probe (anchors/options/parameters recovered by compiling each component)). Options and parameters come from the reference macros and `node[…]`/`to[…]` draw examples; the *family option candidates* under each heading are a best-effort harvest of the section prose (noisy — candidates, not ground truth).

## Amplifiers  (10)

*Family option candidates (prose): plus, minus, leftedge, noinv input down, noinv input up, noinv output up, noinv output down, out up, out down, amp plus, amp minus, font2, font*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Buffer | `buffer` | node | out, bout, in, bin | — | — |
| Operational amplifier compliant to DIN/EN 60617 standard | `en amp` | node | +, -, out, in up, in down, bin up, bin down, bout | noinv input up | amp plus, en amp text |
| Fully differential instrumentation amplifier | `fd inst amp` | node | +, -, out, in up, in down, bin up, bin down, bout, out +, out -, refv up, refv down | noinv input up, noinv output down | amp plus |
| Fully differential operational amplifier | `fd op amp` | node | +, -, out, in up, in down, bin up, bin down, bout, out +, out - | noinv input up, noinv output down | amp plus |
| transconductance amplifier | `gm amp` | node | +, -, out, in up, in down, bin up, bin down, bout | noinv input up | amp plus |
| plain instrumentation amplifier | `inst amp` | node | +, -, out, in up, in down, bin up, bin down, bout, refv up, refv down | noinv input up | amp plus |
| instrumentation amplifier with amplification resistance terminals | `inst amp ra` | node | +, -, out, in up, in down, bin up, bin down, bout, refv up, refv down, ra+, ra- | noinv input up | amp plus |
| Operational amplifier | `op amp` | node | +, -, out, in up, in down, bin up, bin down, bout | noinv input up | amp plus |
| Plain amplifier | `plain amp` | node | +, -, out, in up, in down, bin up, bin down, bout | — | component text |
| Plain amplifier, one input | `plain mono amp` | node | out, bout, in, bin | — | — |

## Switches, buttons and jumpers  (39)

*Family option candidates (prose): thickness, arrow, relative thickness, dash, switch end arrow, switch start arrow, tjumper connections, cout 2, switches, hlines thickness, channels, angle, none, cw, ccw, both, rotary switch, aout 1, aout 2, sqout 1, latexslim, switchable start arrow, wiper start arrow, switch arrows*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Bare jumper | `bare jumper` | path | out, in, cin, a, b, top arc, out.n (sub-node) | — | bipole nodes, nodes width |
| Closed double solder jumper | `closed double solder jumper` | path | out, in, a, b, tap, tap up, tap down | — | bipole nodes |
| Closed jumper | `closed jumper` | path | out, in, cin, a, b, top arc, in.s (sub-node) | — | bipole nodes, bipoles/jumper/shape, nodes width |
| Closed solder jumper | `closed solder jumper` | path | out, in, a, b | — | bipole nodes |
| Closing normally closed switch | `closing normal closed switch` | path | a, b | — | bipole nodes, switch end arrow |
| Closing normally open switch | `closing normal open switch` | path | a, b | — | bipole nodes, switch end arrow |
| Closing switch | `closing switch` | path | a, b | — | bipole nodes, switch end arrow |
| Cute closed switch | `cute closed switch` | path | out, in, cin, a, b | — | bipole nodes, nodes width |
| Cute closing switch | `cute closing switch` | path | out, in, cin, a, b | — | bipole nodes, nodes width, switch end arrow |
| Cute open switch | `cute open switch` | path | out, in, cin, a, b, out.s (sub-node) | — | bipole nodes, nodes width |
| Cute opening switch | `cute opening switch` | path | out, in, cin, a, b | — | bipole nodes, nodes width, switch end arrow |
| Cute spdt down | `cute spdt down` | node | in, cin, out 1, cout 1, out 2 | — | — |
| Cute spdt down with arrow | `cute spdt down arrow` | node | in, cin, out 1, cout 1, out 2 | — | switch end arrow |
| Cute spdt mid | `cute spdt mid` | node | in, cin, out 1, cout 1, out 2 | — | — |
| Cute spdt mid with arrow | `cute spdt mid arrow` | node | in, cin, out 1, cout 1, out 2 | — | switch end arrow, switch start arrow |
| Cute spdt up | `cute spdt up` | node | in, cin, out 1, cout 1, out 2 | — | — |
| Cute spdt up with arrow | `cute spdt up arrow` | node | in, cin, out 1, cout 1, out 2 | — | switch end arrow, switch start arrow |
| proximeter switch, inline | `inline proximeter` | path | hlines nw, hlines ne, hlines sw, hlines se | — | bipole nodes |
| Left double solder jumper | `left double solder jumper` | path | out, in, a, b, tap, tap up, tap down | — | bipole nodes |
| Normally closed switch | `normal closed switch` | path | a, b | — | bipole nodes |
| Normally open switch | `normal open switch` | path | a, b | — | bipole nodes |
| Normally closed push button | `normally closed push button` | path | tip, a, b | — | bipole nodes, nodes width |
| Normally closed push button (in open position) | `normally closed push button open` | path | tip, a, b | — | bipole nodes, nodes width |
| Normally open push button (in closed position) | `normally open push button closed` | path | tip, a, b | — | bipole nodes, nodes width |
| Open double solder jumper | `open double solder jumper` | path | out, in, a, b, tap, tap up, tap down | — | bipole nodes |
| Open jumper | `open jumper` | path | out, in, cin, a, b, top arc, out.s (sub-node) | — | bipole nodes, nodes width |
| Open solder jumper | `open solder jumper` | path | out, in, a, b | — | bipole nodes |
| Opening normally closed switch | `opening normal closed switch` | path | a, b | — | bipole nodes, switch end arrow |
| Opening normally open switch | `opening normal open switch` | path | a, b | — | bipole nodes, switch end arrow |
| Opening switch | `opening switch` | path | a, b | — | bipole nodes, switch end arrow |
| proximeter | `proximeter` | node | hlines nw, hlines ne, hlines sw, hlines se | — | — |
| Normally open push button | `push button` | path | tip, a, b | — | bipole nodes, nodes width |
| Reed switch | `reed` | path | a, b | — | bipole nodes |
| Right double solder jumper | `right double solder jumper` | path | out, in, a, b, tap, tap up, tap down | — | bipole nodes |
| Rotary switch | `rotaryswitch` | node | in, cin, out 1, cout 1, out 2, out 1.n (sub-node), out 4.w (sub-node) | — | — |
| spdt | `spdt` | node | in, out 1, out 2 | — | — |
| Switch | `switch` | path | a, b | — | bipole nodes, switch end arrow |
| Three-pins jumper (see later for connections) | `three-pins jumper` | path | out, in, cin, a, b, tap, top arc left, top arc right, out.n (sub-node) | — | bipole nodes, nodes width, tjumper connections |
| Toggle switch | `toggle switch` | path | out 1, out 2, a, b | — | bipole nodes |

## Transistors  (31)

*Family option candidates (prose): thickness, emptycircle, nocircle, no arrows, opto end arrow, opto arrows, tr circle, schottky base size, ferroel gate, transistors, outer base thickness, relative thickness, dash, photo, bodydiode, collectors, emitters, schottky base, fetsolderdot, nofetsolderdot, solderdot, nosolderdot, doublegate, gate2, emitter, legacytransistorstext, legacy, arrowmos, noarrowmos, opto, split gate, source arrow, gate asym, no schottky base, no ferroel base, modifier thickness, hinata exc, none, tr gap fill, default base in, njfet base in, isfet base in, partial border, partial border dash, transistor bodydiode, source, drain, collector, inner up, inner down, pnp1*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| bjt npn | `bjtnpn` | node | B, C, E, nobase, C1, E1, cbase, vcenter, gcenter | schottky base | bjt multi height, bjt pins width, collectors, emitters |
| bjt pnp | `bjtpnp` | node | B, C, E, nobase, C1, E1, cbase, vcenter, gcenter | schottky base | bjt pins width, collectors, emitters |
| Gallium Nitride hemt (a "styled" hemt) | `GaN hemt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | — |
| hemt | `hemt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | — |
| isfet | `isfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, solderdot | — |
| Lnigbt | `Lnigbt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | tr gap fill |
| Lpigbt | `Lpigbt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | tr gap fill |
| nfet | `nfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate | tr gap fill |
| nfet depletion | `nfetd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate | tr gap fill |
| N-type graphene FET | `ngfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode | — |
| nigbt | `nigbt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | tr gap fill |
| nigfetd | `nigfetd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate, solderdot | tr gap fill |
| nigfete | `nigfete` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate, solderdot, tr circle | tr gap fill |
| nigfetebulk | `nigfetebulk` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate, solderdot | tr gap fill |
| n-type JFET | `njfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, kink, gate | bodydiode, nobase, nogate | — |
| nmos | `nmos` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | arrowmos, bodydiode, bulk, ferroel gate, nobase, nogate | tr gap fill |
| nmos depletion | `nmosd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | arrowmos, bodydiode, bulk, ferroel gate, nobase, nogate | tr gap fill |
| npn | `npn` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, arrows, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, no schottky base, nobase, nogate, photo, schottky base, tr circle | — |
| n-type UJT | `nujt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, B1, B2, kink, gate | bodydiode, nobase, nogate, tr circle | — |
| pfet | `pfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, no ferroel gate, nobase, nogate | tr gap fill |
| pfet depletion | `pfetd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate | tr gap fill |
| pgfet | `pgfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode | — |
| pigbt | `pigbt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate | tr gap fill |
| pigfetd | `pigfetd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate, solderdot | tr gap fill |
| pigfete | `pigfete` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate, solderdot | tr gap fill |
| pigfetebulk | `pigfetebulk` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, doublegate, ferroel gate, nobase, nogate | tr gap fill |
| p-type JFET | `pjfet` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, kink, gate | bodydiode, nobase, nogate | — |
| pmos | `pmos` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | arrowmos, bodydiode, bulk, emptycircle, ferroel gate, nobase, nocircle, nogate | tr gap fill |
| pmos depletion | `pmosd` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | arrowmos, bodydiode, bulk, ferroel gate, nobase, nocircle, nogate | tr gap fill |
| pnp | `pnp` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, arrows, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, gate | bodydiode, nobase, nogate, photo, schottky base | — |
| p-type UJT | `pujt` | node | B, C, E, body C in, body E in, body C out, body E out, nobase, circle C, circle E, nobulk, centergap, G, D, S, bulk, nogate, G2, G1, B1, B2, kink, gate | bodydiode, nobase, nogate, tr circle | — |

## Grounds and supply voltages  (12)

*Family option candidates (prose): arrow, legacy, power supplies*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Chassis ground | `cground` | node | — | — | — |
| European style ground | `eground` | node | — | — | — |
| European style ground, version 2 | `eground2` | node | — | — | — |
| Ground | `ground` | node | — | — | — |
| Noiseless ground | `nground` | node | — | — | — |
| Protective ground | `pground` | node | — | — | — |
| Reference ground | `rground` | node | — | — | — |
| Signal ground | `sground` | node | — | — | — |
| Thicker tailless reference ground | `tground` | node | — | — | — |
| Tailless ground | `tlground` | node | — | — | — |
| VCC/VDD | `vcc` | node | — | — | — |
| VEE/VSS | `vee` | node | — | — | — |

## Crossings  (3)

*Family option candidates (prose): crossing vertical, dash, size, choke, none*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Jumper style non-contact crossing | `crossing` | path | a, b | — | bipole nodes |
| Jumper-style crossing node | `jump crossing` | node | — | — | — |
| Plain style crossing node | `plain crossing` | node | — | — | — |

## Arrows  (4)

*Family option candidates (prose): tunable start arrow, latexslim, tunable end arrow*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Arrow for current and voltage | `currarrow` | node | tip | — | — |
| Arrow used for the flows, with a text anchor | `flowarrow` | node | tip | — | — |
| Arrow that is anchored at its tip, useful for block diagrams. | `inputarrow` | node | tip | — | — |
| Arrow the same size of currarrow but only filled. | `trarrow` | node | tip, btip | — | — |

## Terminal shapes  (6)

*Family option candidates (prose): open nodes fill, white*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Connected terminal | `circ` | node | — | — | — |
| Diamond-square terminal | `diamondpole` | node | — | — | — |
| Unconnected terminal | `ocirc` | node | — | — | — |
| Open diamond-square terminal | `odiamondpole` | node | — | — | — |
| Open square-shape terminal | `osquarepole` | node | — | — | — |
| Square-shape terminal | `squarepole` | node | — | — | — |

## Connectors  (7)

*Family option candidates (prose): connectors, thickness*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| BNC connector | `bnc` | node | hot, zero, shield | — | — |
| IEC 60617 connector | `iec connector` | path | a, b | — | bipole nodes, connectors/scale |
| IEC 60617 connector | `iecconnshape` | node | a, b | — | — |
| IEC 60617 male plug, left side | `iecplugL` | node | — | — | — |
| IEC 60617 male plug, right side | `iecplugR` | node | — | — | — |
| IEC 60617 female socket, left side | `iecsocketL` | node | — | — | — |
| IEC 60617 female socket, right side | `iecsocketR` | node | — | — | — |

## Block diagram components  (47)

*Family option candidates (prose): inner blocks dashed, matthuszagh, frankplow, olfline, dl1chb, t1, t2, text in, text out, dashed blocks pattern*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| A/D converter | `adc` | path | a, b | — | bipole nodes |
| adder | `adder` | node | out, in, geocenter, in 1, in 2 | — | — |
| Comparison, all-or-nothing | `allornothing` | path | a, b | — | bipole nodes |
| allpass | `allpass` | path | a, b | — | bipole nodes |
| amplifier; use t=… to add a text | `amp` | path | a, b | — | bipole nodes |
| bandpass | `bandpass` | path | a, b | — | bipole nodes |
| bandstop | `bandstop` | path | a, b | — | bipole nodes |
| circulator | `circulator` | node | out, in, geocenter, in 1, in 2 | — | — |
| Coupler | `coupler` | node | port1, port2, port3, port4 | — | — |
| Coupler with rounded arrows | `coupler2` | node | port1, port2, port3, port4 | — | — |
| D/A converter | `dac` | path | a, b | — | bipole nodes |
| detector | `detector` | path | a, b | — | bipole nodes |
| DSP | `dsp` | path | a, b | — | bipole nodes |
| FFT | `fft` | path | a, b | — | bipole nodes |
| Optical Fiber | `fiber` | path | a, b | — | bipole nodes |
| Generic fourport | `fourport` | node | port1, port2, port3, port4 | — | — |
| generic splitter | `genericsplitter` | node | in, out2, out1 | — | — |
| gridnode | `gridnode` | node | — | — | — |
| highpass | `highpass` | path | a, b | — | bipole nodes |
| simplified highpass (with only 2 waves) | `highpass2` | path | a, b | — | bipole nodes |
| instrumentation amplifier; use t=… to add a text | `iamp` | path | a, b | — | bipole nodes |
| lowpass | `lowpass` | path | a, b | — | bipole nodes |
| simplified lowpass (with only 2 waves) | `lowpass2` | path | a, b | — | bipole nodes |
| mixer | `mixer` | node | out, in, geocenter, in 1, in 2 | — | — |
| Mach Zehnder Modulator | `mzm` | node | out, in, mod | — | — |
| oscillator | `oscillator` | node | out, in, geocenter, in 1, in 2 | — | — |
| phase shifter | `phaseshifter` | path | a, b | — | bipole nodes |
| π attenuator | `piattenuator` | path | a, b | — | bipole nodes |
| single phase AC/AC converter | `sacac` | path | a, b, ac up in, ac down in, ac up out, ac down out | — | bipole nodes |
| single phase AC/DC converter | `sacdc` | path | a, b, dc up out, dc down out, ac up in, ac down in | — | bipole nodes, blocks dc out segments |
| Saturation | `saturation` | path | a, b | — | bipole nodes |
| single phase DC/AC converter | `sdcac` | path | a, b, dc up in, dc down in, ac up out, ac down out | — | bipole nodes |
| single wire DC/DC converter | `sdcdc` | path | a, b, dc up in, dc down in, dc up out, dc down out | — | bipole nodes, blocks dc in segments |
| Sigmoid | `sigmoid` | path | a, b | — | bipole nodes |
| resistive splitter | `splitter` | node | in, out2, out1 | — | — |
| three phases AC/DC converter | `tacac` | path | a, b, ac up in, ac down in, ac up out, ac down out, ac mid in, ac mid out | — | bipole nodes |
| three phases AC/DC converter | `tacdc` | path | a, b, dc up out, dc down out, ac up in, ac down in, ac mid in | — | bipole nodes |
| T attenuator | `tattenuator` | path | a, b | — | bipole nodes |
| three phases AC/DC converter | `tdcac` | path | a, b, dc up in, dc down in, ac up out, ac down out, ac mid out | — | bipole nodes |
| generic two port (use t=… to specify text) | `twoport` | path | a, b | — | bipole nodes, bipoles/twoport/width |
| generic two port split (use t1=… and t2=… to specify text) | `twoportsplit` | path | a, b | — | bipole nodes, t1, t2 |
| VGA | `vamp` | path | a, b | — | bipole nodes, tunable end arrow |
| vco | `vco` | path | a, b | — | bipole nodes |
| var. phase shifter | `vphaseshifter` | path | a, b | — | bipole nodes, tunable end arrow |
| var. π attenuator | `vpiattenuator` | path | a, b | — | bipole nodes, tunable end arrow |
| var. T attenuator | `vtattenuator` | path | a, b | — | bipole nodes, tunable end arrow |
| wilkinson divider | `wilkinson` | node | in, out2, out1 | — | — |

## Electronic Tubes  (7)

*Family option candidates (prose): thickness, partial border, none, partial border dash, ferdymercury*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Tube Diode | `diodetube` | node | anode, cathode, filament 1, filament 2, cathode 1, cathode 2 | filament, fullcathode, nocathode | — |
| Dynode | `dynode` | node | arc | — | — |
| Magnetron | `magnetron` | node | anode, cathode1, cathode2 | — | — |
| Pentode | `pentode` | node | anode, cathode, filament 1, filament 2, cathode 1, cathode 2, control, screen, suppressor | filament, fullcathode, nocathode | circuitikz/tubes/height, circuitikz/tubes/width |
| Pentode with suppressor grid connected to cathode | `pentode suppressor to cathode` | node | anode, cathode, filament 1, filament 2, cathode 1, cathode 2, control, screen | filament, fullcathode, nocathode | — |
| Tetrode | `tetrode` | node | anode, cathode, filament 1, filament 2, cathode 1, cathode 2, control, screen | filament, fullcathode, nocathode | — |
| Triode | `triode` | node | anode, cathode, filament 1, filament 2, cathode 1, cathode 2, control | filament, fullcathode, nocathode | circuitikz/tubes/height |

## RF components  (15)

*Family option candidates (prose): tline, bare*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Legacy antenna (with tails) | `antenna` | node | — | — | — |
| Bare Antenna | `bareantenna` | node | — | — | — |
| Bare RX Antenna | `bareRXantenna` | node | waves | — | — |
| Bare TX Antenna | `bareTXantenna` | node | waves | — | — |
| DIN antenna | `dinantenna` | node | — | — | — |
| match | `match` | node | — | — | — |
| Microstrip linear stub | `mslstub` | node | — | — | mstlinelen |
| Microstrip port | `msport` | node | — | — | — |
| Microstrip radial stub | `msrstub` | node | — | — | — |
| Microstrip transmission line | `mstline` | path | a, b | — | bipole nodes, mstlinelen |
| Legacy receiving antenna (with tails) | `rxantenna` | node | — | — | — |
| Transmission line | `TL` | path | a, b | — | bipole nodes, bipoles/tline/bare, bipoles/tline/width |
| Transmission line stub | `tlinestub` | node | — | — | — |
| Legacy transmitting antenna (with tails) | `txantenna` | node | — | — | — |
| Waves | `waves` | node | — | — | — |

## Electro-Mechanical Devices  (1)

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Motor | `elmech` | node | — | — | — |

## Double bipoles (transformers)  (4)

*Family option candidates (prose): inductor, cute inductors, inner, inductors, heigth, cthick, choke, none, inward, inline, pgfkeys, misc*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Generic double bipole (configurable components) | `double bipole` | node | B1, B2, A1, A2, inner dot A1, inner dot A2, inner dot B1, inner dot B2, outer dot A1, outer dot A2, outer dot B1, outer dot B2, L.south (sub-node), R.west (sub-node) | — | — |
| Gyrator | `gyrator` | node | B1, B2, A1, A2, inner dot A1, inner dot A2, inner dot B1, inner dot B2, outer dot A1, outer dot A2, outer dot B1, outer dot B2 | — | — |
| Transformer (cute inductor) | `transformer` | node | B1, B2, A1, A2, inner dot A1, inner dot A2, inner dot B1, inner dot B2, outer dot A1, outer dot A2, outer dot B1, outer dot B2 | — | — |
| Transformer core (cute inductor) | `transformer core` | node | B1, B2, A1, A2, inner dot A1, inner dot A2, inner dot B1, inner dot B2, outer dot A1, outer dot A2, outer dot B1, outer dot B2 | — | — |

## Logic gates  (40)

*Family option candidates (prose): logic ports, thickness, inner, angle, ieeestd ports, european ports font, european not symbol, and port, or port, ieee, ieeestd, tgate, double tgate, american ports, european ports, americanports, buffer port, nand port, nor port, not port, xor port, xnor port, schmitt port, invschmitt port, europeanports, ieee ports, pointy, roundy, legacy, nor, xor, xnor, inner inputs, not radius, no inputs pin, triangle, circle, ieee circle, direct*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| American and port | `american and port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| American buffer port | `american buffer port` | node | out, bout, in, bin, in 1, bin 1 | — | component text |
| American nand port | `american nand port` | node | out, bout, in 1, in 2, bin 1, bin 2 | no input leads | number inputs |
| American nor port | `american nor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| American not port | `american not port` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| American or port | `american or port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| American xnor port | `american xnor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| American xor port | `american xor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| European and port | `european and port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| European blank not port | `european blank not port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| European blank port | `european blank port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| European buffer port | `european buffer port` | node | out, bout, in, bin, in 1, bin 1 | — | component text |
| European nand port | `european nand port` | node | out, bout, in 1, in 2, bin 1, bin 2 | no input leads | number inputs |
| European nor port | `european nor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| European not port | `european not port` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| European or port | `european or port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| European xnor port | `european xnor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| European xor port | `european xor port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | number inputs |
| IEEE style double transmission gate | `ieee double tgate` | node | out, bout, in, bin, in 1, bin 1, notgate, gate, bnotgate, bgate | — | — |
| IEEE style transmission gate | `ieee tgate` | node | out, bout, in, bin, in 1, bin 1, notgate, gate, bnotgate, bgate | — | — |
| IEEE standard "and" port | `ieeestd and port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| IEEE standard buffer port | `ieeestd buffer port` | node | out, bout, in, bin, in 1, bin 1 | — | component text |
| Inverting Schmitt port matched to IEEE standard ports | `ieeestd invschmitt port` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| IEEE standard "nand" port | `ieeestd nand port` | node | out, bout, in 1, in 2, bin 1, bin 2 | no input leads | inner inputs, number inputs |
| IEEE standard "nor" port | `ieeestd nor port` | node | out, bout, in 1, in 2, bin 1, bin 2, not (sub-node) | — | inner inputs, number inputs |
| IEEE standard "not" port | `ieeestd not port` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| IEEE standard "or" port | `ieeestd or port` | node | out, bout, in 1, in 2, bin 1, bin 2 | — | inner inputs, number inputs |
| Schmitt port matched to IEEE standard ports | `ieeestd schmitt port` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| IEEE standard "xnor" port | `ieeestd xnor port` | node | out, bout, in 1, in 2, bin 1, bin 2, ibin 1 | — | baselen, inner inputs, number inputs, pin length, xor bar distance |
| IEEE standard "xor" port xor | `ieeestd xor port` | node | out, bout, in 1, in 2, bin 1, bin 2, ibin 1 | — | inner inputs, number inputs |
| "buffer" logic port | `inline buffer` | path | out, bout, in, bin, in 1, bin 1 | — | bipole nodes |
| double transmission gate | `inline double tgate` | path | out, bout, in, bin, in 1, bin 1, notgate, gate, bnotgate, bgate | — | bipole nodes |
| Inverting Schmitt logic port | `inline invschmitt` | path | out, bout, in, bin, in 1, bin 1 | — | bipole nodes |
| "not" logic port | `inline not` | path | out, bout, in, bin, in 1, bin 1 | european ports | bipole nodes |
| Schmitt logic port | `inline schmitt` | path | out, bout, in, bin, in 1, bin 1 | — | bipole nodes |
| transmission gate | `inline tgate` | path | out, bout, in, bin, in 1, bin 1, notgate, gate, bnotgate, bgate | — | bipole nodes |
| Inverting Schmitt trigger | `invschmitt` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| Inverting dot for IEEE ports | `notcirc` | node | — | — | — |
| Non-Inverting Schmitt trigger | `schmitt` | node | out, bout, in, bin, in 1, bin 1 | — | — |
| Schmitt symbol to add to input pins if needed | `schmitt symbol` | node | — | — | — |

## Flip-flops  (9)

*Family option candidates (prose): clock wedge size, logic ports, flipflops, t0, t1, t6, tu, td, c0, c6, cu, cd, n0, n6, nu, nd, flipflop def, pin spacing, font, fontud, ieee ports, european ports, european not symbol, cirle, ieee circle*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| clock wedge shape | `clockwedge` | node | — | — | — |
| Blank (void) flip flop | `flipflop` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | — | — |
| Example custom flip flop | `flipflop AB` | node | — | — | — |
| Edge-triggered synchronous flip-flop D | `flipflop D` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | — | external pins width |
| Edge-triggered synchronous flip-flop JK | `flipflop JK` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | add async SR, dot on notQ, rotated numbers | external pins width |
| Example custom flip flop | `flipflop myJK` | node | — | — | — |
| flip-flop SR | `flipflop SR` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | — | external pins width |
| Edge-triggered synchronous flip-flop T | `flipflop T` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | — | external pins width |
| D-type latch | `latch` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, bup, bdown, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | — | external pins width |

## Multiplexer and de-multiplexer  (6)

*Family option candidates (prose): logic ports, wedge inversion mark, muxdemuxes, yashpalgoyal1304, muxdemux def, square pins, mux 4by2, thickness, external pins thickness, no inputs lead, all, lpin, rpin, bpin, tpin, blpin, brpin, bbpin, btpin, clock wedge size, wi, wo, xsep, ysep, pgf, bgpicture, double tgate*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| ALU | `ALU` | node | bpin 1, bpin 2, lpin 1, lpin 2, blpin 1, blpin 2, bbpin 1, rpin 1, brpin 1 | — | external pins width |
| Circle-shaped inversion marker | `circleinv` | node | geocenter, apex | — | — |
| Demultiplexer 1→2³ with Lh=4, Rh=8, NL=1, NB=3, NR=8 | `demux` | node | bpin 1, bpin 2, bpin 3, lpin 1, blpin 1, bbpin 1, rpin 1, brpin 1 | — | external pins width |
| mux-demux | `muxdemux` | node | bpin 1, bpin 2, bpin 3, lpin 1, lpin 2, blpin 1, blpin 2, bbpin 1, rpin 1, brpin 1 | — | external pins width, muxdemux def |
| One-bit adder | `one bit adder` | node | bpin 1, lpin 1, lpin 2, blpin 1, blpin 2, bbpin 1, rpin 1, brpin 1 | — | external pins width |
| Inversion marker for European logic symbols | `wedgeinv` | node | apex | — | — |

## Chips (integrated circuits)  (2)

*Family option candidates (prose): thickness, external pins thickness, font, pin spacing, num pins, no inputs lead, topmark, draw only pins, all, pin*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Dual-in-Line Package chip | `dipchip` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | hide numbers | draw only pins, external pad fraction, external pins width, num pins |
| Quad-Flat Package chip | `qfpchip` | node | pin 1, pin 2, pin 3, bpin 1, bpin 6, pin 6, pin 5, pin 4, bpin 2, bpin 3, bpin 5, bpin 4, dot | rotated numbers | draw only pins, external pad fraction, external pins width, num pins |

## Seven segment displays  (1)

*Family option candidates (prose): thickness, segment sep, box sep, seven segment val, seven segment bits, none, empty, on, off, bits, val*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Seven segment display | `bare7seg` | node | a, b, c, d, f, g, dot | — | — |

## Path-style components  (1)

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| resistor, american style | `resistor` | path | a, b | — | bipole nodes |

## Resistive bipoles  (18)

*Family option candidates (prose): european resistors, thickness, modifier thickness, wiper end arrow, americanresistors, europeanresistors, zigs, none, tunable end arrow, latexslim, tunable start arrow, wiper start arrow, photoresistor, opto*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Generic asymmetric bipole | `ageneric` | path | a, b | — | bipole nodes |
| Generic (symmetric) bipole | `generic` | path | a, b | — | bipole nodes |
| Ligth-Dependent resistor | `ldR` | path | arrows, a, b | — | bipole nodes |
| Memristor | `memristor` | path | a, b | — | bipole nodes |
| Metal-Oxide varistor | `mov` | path | a, b | — | bipole nodes |
| Open circuit | `open` | path | a, b | — | voltage |
| Photoresistor | `phR` | path | arrows, a, b | — | bipole nodes |
| potentiometer, american style | `pR` | path | tip, a, b, wiper | — | bipole nodes, wiper pos |
| Resistor | `R` | path | a, b | — | a2, bipole nodes, current arrow scale, l2, l2 halign, l2 valign, resistors/scale, voltage, voltage shift, voltage/bump b |
| Short circuit | `short` | path | a, b | — | bipole nodes, nodes width |
| Resistive sensor | `sR` | path | tip, a, b, wiper, label | — | bipole nodes, label distance |
| Tunable generic bipole | `tgeneric` | path | tip, a, b, wiper | — | bipole nodes, tunable end arrow |
| Thermistor | `thR` | path | tip, a, b, wiper, label | — | bipole nodes |
| NTC thermistor | `thRn` | path | a, b | — | bipole nodes |
| PTC thermistor | `thRp` | path | a, b | — | bipole nodes |
| Varistor | `varistor` | path | a, b | — | bipole nodes |
| Variable resistor | `vR` | path | tip, a, b, wiper | — | bipole nodes, tunable end arrow, tunable start arrow |
| Crossed generic (symmetric) bipole | `xgeneric` | path | a, b | — | bipole nodes |

## Instruments  (12)

*Family option candidates (prose): waveform, rotated instruments, straight instruments, ramps, pgf, rmaterwa*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Ammeter | `ammeter` | path | a, b | — | bipole nodes |
| Current loop (symbolic) | `iloop` | path | a, b, i | — | bipole nodes |
| Current loop (real) | `iloop2` | path | a, b, i+, i- | — | bipole nodes |
| Ohmmeter | `ohmmeter` | path | a, b | — | bipole nodes |
| Oscilloscope | `oscope` | path | in 1, in 2, a, b | — | bipole nodes |
| QUCS-style current probe | `qiprobe` | path | a, b, v+, v- | — | bipole nodes, current arrow scale |
| QUCS-style power probe | `qpprobe` | path | a, b, v+, v- | — | bipole nodes, current arrow scale, nodes width |
| QUCS-style voltage probe | `qvprobe` | path | a, b, v+, v- | — | bipole nodes, nodes width |
| Round meter (use t=... for the symbol) | `rmeter` | path | a, b | — | bipole nodes |
| Round meter with arrow (use t=... for the symbol) | `rmeterwa` | path | a, b | straight instruments | bipole nodes |
| Square meter (use t=... for the symbol) | `smeter` | path | in 1, in 2, a, b | rotated instruments | bipole nodes |
| Voltmeter | `voltmeter` | path | a, b | — | bipole nodes |

## Capacitors and inductors: dynamical bipoles  (12)

*Family option candidates (prose): capacitors, inductor, cthick, cute inductors, american inductors, inductors, polar capacitor, cuteinductors, americaninductors, europeaninductors, coils, lr dot, ur dot*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Capacitive sensor | `capacitive sensor` | path | tip, a, b, wiper, label | — | bipole nodes |
| Capacitor | `capacitor` | path | a, b | — | bipole nodes |
| Constant Phase Element | `cpe` | path | a, b | — | bipole nodes, capacitors/width |
| Curved (polarized) capacitor | `curved capacitor` | path | a, b | — | bipole nodes |
| Choke | `cute choke` | path | a, b, midtap | onelinechoke, twolineschoke | bipole nodes |
| Electrolytic capacitor | `ecapacitor` | path | a, b | — | bipole nodes |
| Ferroelectric capacitor | `feC` | path | a, b, kink left, kink right, curve left, curve right | — | bipole nodes |
| Inductor | `L` | path | a, b, midtap, core west, core east | — | bipole nodes, inductors/coils, inductors/width, l2, label distance, loops |
| Piezoelectric Element | `piezoelectric` | path | a, b | — | bipole nodes |
| Inductive sensor | `sL` | path | tip, a, b, wiper, label, midtap, core west, core east | — | bipole nodes |
| Variable capacitor | `variable capacitor` | path | tip, a, b, wiper | — | bipole nodes, tunable end arrow |
| Variable inductor | `vL` | path | tip, a, b, wiper, midtap, core west, core east | — | bipole nodes, tunable end arrow |

## Diodes and such  (56)

*Family option candidates (prose): diode, gto gate end arrow, led arrows from anode, pd arrows to anode, full diodes, stroke diodes, empty diodes, led arrows from cathode, pd arrows to cathode, opto arrows, opto end arrow, opto start arrow, relative thickness, empty, full, stroke, fulldiode, strokediode, emptydiode, legacy, gto gate, gto gate start arrow, opto, end arrow, none*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Standard GTO with bar-type gate on anode (shape depends on package option) | `agtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty GTO, bar-type on anode | `empty agtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty bidirectionaldiode | `empty bidirectionaldiode` | path | a, b | — | bipole nodes |
| Empty diode | `empty diode` | path | a, b | — | bipole nodes |
| Empty GTO | `empty gto` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty GTO, bar-type | `empty gtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty laser diode | `empty laser diode` | path | arrows, a, b | — | bipole nodes |
| Empty led | `empty led` | path | arrows, a, b | led arrows from cathode | bipole nodes |
| Empty photodiode | `empty photodiode` | path | arrows, a, b | pd arrows to cathode | bipole nodes |
| Empty PUT | `empty put` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty Schottky diode | `empty Schottky diode` | path | a, b | — | bipole nodes |
| Empty Shockley diode | `empty Shockley diode` | path | a, b | — | bipole nodes |
| Empty thyristor | `empty thyristor` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty triac | `empty triac` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Empty tunnel diode | `empty tunnel diode` | path | a, b | — | bipole nodes |
| Empty TVS diode, transorb | `empty TVS diode` | path | a, b | — | bipole nodes |
| Empty varcap | `empty varcap` | path | a, b | — | bipole nodes |
| Empty Zener diode | `empty Zener diode` | path | a, b | — | bipole nodes |
| Empty ZZener diode | `empty ZZener diode` | path | a, b | — | bipole nodes |
| Full GTO, bar-type on anode | `full agtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full bidirectionaldiode | `full bidirectionaldiode` | path | a, b | — | bipole nodes |
| Full diode | `full diode` | path | a, b | — | bipole nodes |
| Full GTO | `full gto` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full GTO, bar-type | `full gtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full laser diode | `full laser diode` | path | arrows, a, b | — | bipole nodes |
| Full led | `full led` | path | arrows, a, b | led arrows from cathode | bipole nodes |
| Full photodiode | `full photodiode` | path | arrows, a, b | pd arrows to cathode | bipole nodes |
| Full PUT | `full put` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full Schottky diode | `full Schottky diode` | path | a, b | — | bipole nodes |
| Full Shockley diode | `full Shockley diode` | path | a, b | — | bipole nodes |
| Full thyristor | `full thyristor` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full triac | `full triac` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Full tunnel diode | `full tunnel diode` | path | a, b | — | bipole nodes |
| Full TVS diode, transorb | `full TVS diode` | path | a, b | — | bipole nodes |
| Full varcap | `full varcap` | path | a, b | — | bipole nodes |
| Full Zener diode | `full Zener diode` | path | a, b | — | bipole nodes |
| Full ZZener diode | `full ZZener diode` | path | a, b | — | bipole nodes |
| Standard GTO (shape depends on package option) | `gto` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Standard GTO with bar-type gate (shape depends on package option) | `gtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Standard Programmable Unipolar Transistor (shape depends on package option) | `put` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke GTO, bar-type on anode | `stroke agtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke diode | `stroke diode` | path | a, b | — | bipole nodes |
| Stroke GTO | `stroke gto` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke GTO, bar type | `stroke gtobar` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke laser diode | `stroke laser diode` | path | arrows, a, b | — | bipole nodes |
| Stroke led | `stroke led` | path | arrows, a, b | led arrows from cathode | bipole nodes |
| Stroke photodiode | `stroke photodiode` | path | arrows, a, b | pd arrows to cathode | bipole nodes |
| Stroke PUT | `stroke put` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke Schottky diode | `stroke Schottky diode` | path | a, b | — | bipole nodes |
| Stroke thyristor | `stroke thyristor` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Stroke tunnel diode | `stroke tunnel diode` | path | a, b | — | bipole nodes |
| Stroke varcap | `stroke varcap` | path | a, b | — | bipole nodes |
| Stroke Zener diode | `stroke Zener diode` | path | a, b | — | bipole nodes |
| Stroke ZZener diode | `stroke ZZener diode` | path | a, b | — | bipole nodes |
| Standard thyristor (shape depends on package option) | `thyristor` | path | G, anode, cathode, gate, a, b | — | bipole nodes |
| Standard triac (shape depends on package option) | `triac` | path | G, anode, cathode, gate, a, b | — | bipole nodes |

## Sources and generators  (36)

*Family option candidates (prose): angle, rotate, inner plus, inner minus, sign rotation, batteries, sources, csources, europeancurrents, current source, isource, americancurrents, europeanvoltages, voltage source, vsource, americanvoltages, cisource, cvsource, delta, wye, eyw, zig, oosource, thickness, auto, margin, cvsourceam, vsourceam, straight*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Controlled current source (american style) | `american controlled current source` | path | a, b | — | bipole nodes, current arrow scale |
| Controlled voltage source (american style) | `american controlled voltage source` | path | a, b | — | bipole nodes |
| Current source (american style) | `american current source` | path | a, b | — | bipole nodes, current arrow scale |
| Voltage source (american style) | `american voltage source` | path | a, b | — | bipole nodes |
| Randall Munroe's baertty | `baertty` | path | a, b | — | bipole nodes |
| Battery | `battery` | path | a, b | — | bipole nodes, bipole/is voltage |
| Single battery cell | `battery1` | path | a, b | — | bipole nodes |
| Single battery cell | `battery2` | path | a, b | — | bipole nodes, voltage |
| Controlled sinusoidal current source | `controlled sinusoidal current source` | path | a, b | — | bipole nodes |
| Controlled sinusoidal voltage source | `controlled sinusoidal voltage source` | path | a, b | — | bipole nodes |
| Current source (cute european style) | `cute european controlled current source` | path | a, b | — | bipole nodes |
| Voltage source (cute european style) | `cute european controlled voltage source` | path | a, b | — | bipole nodes |
| Current source (cute european style) | `cute european current source` | path | a, b | — | bipole nodes |
| Voltage source (cute european style) | `cute european voltage source` | path | a, b | — | bipole nodes |
| DC current source | `dcisource` | path | a, b | — | bipole nodes, current arrow scale |
| DC voltage source | `dcvsource` | path | a, b | — | bipole nodes |
| Empty controlled source | `empty controlled source` | path | a, b | — | bipole nodes |
| Empty voltage source | `esource` | path | a, b | — | bipole nodes |
| Controlled current source (european style) | `european controlled current source` | path | a, b | — | bipole nodes |
| Controlled voltage source (european style) | `european controlled voltage source` | path | a, b | — | bipole nodes |
| Current source (european style) | `european current source` | path | a, b | — | bipole nodes |
| Voltage source (european style) | `european voltage source` | path | a, b | — | bipole nodes |
| Double Zero style current source | `ioosource` | path | a, b, centerprim, centersec | — | bipole nodes |
| Sinusoidal current source | `noise current source` | path | a, b | — | bipole nodes |
| Sinusoidal voltage source | `noise voltage source` | path | a, b | — | bipole nodes |
| Norator element (admits any combination of V and I) | `norator` | path | a, b | — | bipole nodes |
| Nullator element (virtual short circuit; forces V and I to zero) | `nullator` | path | a, b | — | bipole nodes |
| transformer with three windings | `ooosource` | path | a, b, centerprim, centersec, prim1, prim2, sec1, sec2, sec3, tert1, tert2, tert3, centertert | — | bipole nodes, prim, sec, tert |
| transformer source | `oosourcetrans` | path | a, b, centerprim, centersec | — | bipole nodes, prim, sec, sources/scale |
| Photovoltaic module source | `pvmodule` | path | a, b | — | bipole nodes |
| Photovoltaic-voltage source | `pvsource` | path | a, b | — | bipole nodes |
| Sinusoidal current source | `sinusoidal current source` | path | a, b | — | bipole nodes |
| Sinusoidal voltage source | `sinusoidal voltage source` | path | a, b | — | bipole nodes |
| Square voltage source | `square voltage source` | path | a, b | — | bipole nodes |
| Double Zero style voltage source | `voosource` | path | a, b, centerprim, centersec | — | bipole nodes |
| Triangle voltage source | `vsourcetri` | path | a, b | — | bipole nodes |

## Mechanical Analogy  (5)

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Mechanical Damping | `damper` | path | a, b | — | bipole nodes |
| Mechanical Inerter | `inerter` | path | a, b | — | bipole nodes |
| Mechanical Mass | `mass` | path | a, b | — | bipole nodes |
| Mechanical Stiffness | `spring` | path | a, b | — | bipole nodes |
| Mechanical viscoelastic element | `viscoe` | path | a, b | — | bipole nodes |

## Miscellaneous bipoles  (16)

*Family option candidates (prose): bar thickness, gap, europeangfsurgearrester, gf surge arrester, americangfsurgearrester, dots*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Asymmetric fuse | `afuse` | path | a, b | — | bipole nodes |
| American gas filled surge arrester | `american gas filled surge arrester` | path | a, b | — | bipole nodes |
| Barrier | `barrier` | path | a, b | — | bipole nodes |
| Bulb | `bulb` | path | a, b | — | bipole nodes |
| Buzzer | `buzzer` | path | a, b | — | bipole nodes |
| European gas filled surge arrester | `european gas filled surge arrester` | path | a, b | — | bipole nodes |
| Fuse | `fuse` | path | a, b | — | bipole nodes |
| Lamp | `lamp` | path | a, b | — | bipole nodes |
| loudspeaker | `loudspeaker` | path | a, b | — | bipole nodes |
| mic | `mic` | path | a, b | — | bipole nodes |
| Open barrier | `openbarrier` | path | a, b | — | bipole nodes |
| Reversed buzzer | `rbuzzer` | path | a, b | — | bipole nodes |
| Squid | `squid` | path | a, b | — | bipole nodes |
| Thermocouple | `thermocouple` | path | a, b | — | bipole nodes |
| tail-less mic | `tlmic` | path | a, b | — | bipole nodes |
| "wiggly" fuse | `wfuse` | path | a, b | — | bipole nodes, bipoles/wfuse/dots, nodes width |

## Multiple wires (buses)  (3)

*Family option candidates (prose): olfline*

| Component | Keyword | Type | Anchors | Options | Parameters |
|---|---|---|---|---|---|
| Double line multiple wires | `bmultiwire` | path | a, b | — | bipole nodes |
| Single line multiple wires | `multiwire` | path | a, b | — | bipole nodes |
| Triple line multiple wires | `tmultiwire` | path | a, b | — | bipole nodes |

