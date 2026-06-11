#!/usr/bin/env python3
"""Probe which CircuiTikZ keywords actually compile in the installed version.

*** ONE-SHOT BOOTSTRAP TOOL *** — a discovery aid for the ``add_*`` authoring
scripts, which carry their own tables and overwrite curated
``components/definitions.json`` entries when re-run.
``components/generate_components.py`` is the sanctioned re-render path (it
treats definitions.json as the source of truth and only regenerates geometry).

For each candidate keyword, try it as a bipole (``to[kw]``) and as a node
(``node[kw]``). Report class (bipole/node/none). Ground truth — never invents.
"""
from __future__ import annotations
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.components import render  # noqa: E402

# Candidate keywords to test, drawn from the CircuiTikZ manual families that
# COMPONENTS.md lists as unimplemented. Anything that doesn't compile is dropped.
CANDIDATES = [
    # --- resistors / sensors ---
    "varistor", "photoresistor", "ldr", "thermistor", "thermistor ntc",
    "thermistor ptc", "phr", "ephotoresistor", "evaristor",
    # --- capacitors ---
    "vC", "feC", "cC", "sC", "capacitor", "piezoelectric", "PZ",
    # --- inductors ---
    "vL", "sL", "cute choke", "choke", "gyrator", "nv",
    # --- diodes / thyristors ---
    "Do", "sD", "zD", "zDo", "tD", "Tunnel", "Zener", "schottky diode",
    "diode", "VC", "Dbattery", "leDo", "photodiode", "pDo", "LED",
    "diac", "Diac", "triac", "Triac", "thyristor", "Tro", "Tyo",
    "thyristor anode gate", "scr", "SCR", "puthyristor", "Tp",
    "Tpe", "Tpb", "Tnce", "full bridge", "fullgenericbridge", "bridge",
    "varcap", "qiprobe", "tvsdiode", "tvsdiodeo", "laser",
    # --- transistors ---
    "nigbt", "pigbt", "Lnigbt", "nmos", "pmos", "isfet", "nhemt", "phemt",
    "nbjt", "pbjt", "uA741", "njfet", "pjfet", "nigfetbase",
    "Tn", "Tp", "Tnpn", "Tpnp", "nfet", "pfet", "nmosd", "pmosd",
    # --- electron tubes ---
    "triode", "tube", "diodetube", "pentode", "tetrode", "magnetron",
    "dynode", "nodot",
    # --- amplifiers / converters ---
    "fd op amp", "inst amp", "gm amp", "plain amp", "en amp", "amp",
    "diamondpole", "adc", "dac", "DAC", "ADC", "schmitt", "invschmitt",
    "comparator", "fd inst amp", "inst amp ra",
    # --- sources ---
    "battery", "battery1", "battery2", "dcvsource", "dcisource",
    "vsourcesquare", "vsourcetri", "vsourcetri", "isourcesquare", "isourcetri",
    "sqV", "tV", "esource", "ioosource", "ioosourcesin", "solar cell",
    "vsourceN", "vsourcenoise", "isourcenoise", "vsource", "isource",
    "sI", "sV", "sinusoidal voltage source", "ac source", "pmos",
    "sV", "sqV", "rmeter", "rmeterwa", "vsourceAM", "current source",
    # --- switches ---
    "spst", "cute spdt up", "cute spdt down", "normal open switch",
    "normally open switch", "normally closed switch", "cute open switch",
    "cute closed switch", "ncs", "nos", "spdt", "push button",
    "reed", "toggle switch", "rotaryswitch", "rotary switch",
    "cute spdt mid", "ihfet", "cspst", "ospst",
    # --- instruments ---
    "oscope", "rmeter", "rmeterwa", "qfprobe", "iloop", "iloop2", "vmeter",
    # --- electromechanical / transducers ---
    "elmech", "motor", "M", "generator", "G", "loudspeaker", "speaker",
    "mic", "buzzer", "bell", "loudspeaker", "amplifier",
    # --- antennas ---
    "antenna", "rxantenna", "txantenna", "dinantenna",
    # --- connectors / nodes / IC ---
    "ocirc", "circ", "bnc", "jack", "plug", "dipchip", "qfpchip",
    "nodal", "nodalanchored", "border", "iecconnector", "european plug",
    "tlineload", "match", "wilkinson", "tline", "tlinestub",
    # --- crystal / protection / misc ---
    "xtal", "quartz", "cbreaker", "circuit breaker", "spark gap", "sparkgap",
    "barrier", "surge", "fuse", "afuse", "squid", "jj", "josephson",
    "vbarrier", "lamp", "bulb", "lampNeon", "neon lamp", "fuse",
    # --- block-diagram / RF ---
    "mixer", "oscillator", "circulator", "wave", "waves", "phaseshifter",
    "detector", "vco", "lowpass", "highpass", "bandpass", "allpass",
    "lowpass2", "highpass2", "bandpass2", "fourier", "twoport",
    "ground", "tground", "tlground", "vss", "vdd", "vcc", "vee",
]


def _ink(body: str) -> tuple[int, int] | None:
    """(num paths, num glyphs) of a rendered body, or None if it fails to compile."""
    try:
        svg, _ = render.render_svg(body, border_pt=2)
    except render.RenderError:
        return None
    g = render.parse_geometry(svg)
    return (len(g.get("paths", [])), len(g.get("glyphs", [])))


# A plain wire `to[unknown]` draws nothing but the connecting line; a real bipole
# draws strictly more ink. So the wire baseline is the discriminator for to[].
_WIRE_BASELINE = _ink(r"\draw (0,0) to[hv__nonsense__xyz] (2,0);")


def classify(kw: str) -> tuple[str, str]:
    """Return (kw, class): 'bipole' (to[] draws more than a plain wire), 'node'
    (a real \\node shape), or 'none'."""
    bip = _ink(rf"\draw (0,0) to[{kw}] (2,0);")
    if bip is not None and bip != _WIRE_BASELINE:
        return kw, "bipole"
    nod = _ink(rf"\node[{kw}] (X) at (0,0) {{}};")
    if nod is not None:
        return kw, "node"
    return kw, "none"


def main() -> int:
    seen = list(dict.fromkeys(CANDIDATES))  # de-dup, keep order
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(classify, seen))
    bip = [k for k, c in results if c == "bipole"]
    nod = [k for k, c in results if c == "node"]
    non = [k for k, c in results if c == "none"]
    print(f"WIRE BASELINE = {_WIRE_BASELINE}")
    print(f"\nBIPOLES ({len(bip)}): " + ", ".join(bip))
    print(f"\nNODES ({len(nod)}): " + ", ".join(nod))
    print(f"\nNOT FOUND ({len(non)}): " + ", ".join(non))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
