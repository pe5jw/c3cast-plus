"""Band profiles: QO-100 (via the satellite) or 2400 (direct 13 cm, no satellite).

Each profile keeps its own receiver frequency conversion, per kind of receiver:
  rtlsdr  the RTL-SDR (software demodulator), IF range 24-1766 MHz
  nim     MiniTiouner / PicoTuner (hardware demodulator), IF range 144-2450 MHz
  hackrf  HackRF One (software demodulator), IF range 1-6000 MHz: 2.4 GHz directly, no converter
The receiver tunes  IF = RF - LO + correction  (all MHz). LO 0 means "no converter": the frequency goes to the
tuner as it is (a MiniTiouner then runs in its direct mode, with the LNB supply off).

Typical LOs: 9750 standard LNB (QO-100), 9739 or other values for modified / PLL LNBs,
1500 Nooelec Ham It Down (2.4 GHz -> about 900 MHz for an RTL-SDR), 0 MiniTiouner direct on 2.4 GHz.
"""

PROFILES = ("qo100", "2400")
KINDS = ("rtlsdr", "nim", "hackrf")
LABELS = {"qo100": "QO-100", "2400": "2.4 GHz direct"}

# Received RF range per profile, MHz: what a receiver may be tuned to.
RX_RANGE = {"qo100": (10490.0, 10500.0), "2400": (2300.0, 2450.0)}
# IF range each kind of receiver can tune, MHz.
IF_RANGE = {"rtlsdr": (24.0, 1766.0), "nim": (144.0, 2450.0), "hackrf": (1.0, 6000.0)}

DEFAULT_PROFILE = "qo100"
DEFAULT_OFFSETS = {
    "qo100": {"rtlsdr": {"lo": 9750.0, "corr": 0.0}, "nim": {"lo": 9750.0, "corr": 0.0}, "hackrf": {"lo": 9750.0, "corr": 0.0}},
    "2400": {"rtlsdr": {"lo": 1500.0, "corr": 0.0}, "nim": {"lo": 0.0, "corr": 0.0}, "hackrf": {"lo": 0.0, "corr": 0.0}},
}
LO_MAX = 20000.0          # MHz
CORR_MAX = 50.0           # MHz, either way


def kind_of(tuner):
    """'rtlsdr', 'hackrf' or 'nim' for a tuner id from tuner_assignment (rtlsdr / hackrf / minitiouner / picotuner)."""
    return tuner if tuner in ("rtlsdr", "hackrf") else "nim"


def get_profile(cfg):
    value = cfg.get("profile")
    return value if value in PROFILES else DEFAULT_PROFILE


def get_offsets(cfg):
    """{profile: {kind: {lo, corr}}} from config.json; anything missing or broken falls back to the defaults."""
    saved = cfg.get("rx_offsets") if isinstance(cfg.get("rx_offsets"), dict) else {}
    result = {}
    for profile in PROFILES:
        result[profile] = {}
        for kind in KINDS:
            default = DEFAULT_OFFSETS[profile][kind]
            entry = (saved.get(profile) or {}).get(kind) if isinstance(saved.get(profile), dict) else None
            try:
                lo, corr = float(entry["lo"]), float(entry["corr"])
                if not (0 <= lo <= LO_MAX and abs(corr) <= CORR_MAX):
                    raise ValueError
            except (TypeError, KeyError, ValueError):
                lo, corr = default["lo"], default["corr"]
            result[profile][kind] = {"lo": lo, "corr": corr}
    return result


def if_of(rf_mhz, lo, corr):
    return rf_mhz - lo + corr


def check_offsets(data):
    """(clean {profile: {kind: {lo, corr}}}, error text). Every kind must put the middle of its profile's band inside
    its IF range: a wrong LO is caught here, not when someone clicks a signal."""
    if not isinstance(data, dict):
        return None, "expected receiver offsets"
    clean = {}
    for profile in PROFILES:
        clean[profile] = {}
        for kind in KINDS:
            try:
                entry = data[profile][kind]
                lo = round(float(str(entry["lo"]).replace(",", ".")), 6)
                corr = round(float(str(entry["corr"]).replace(",", ".")), 6)
            except (TypeError, KeyError, ValueError):
                return None, f"{LABELS[profile]} / {kind}: LO and correction must be numbers in MHz"
            if not 0 <= lo <= LO_MAX:
                return None, f"{LABELS[profile]}: LO must be 0 to {LO_MAX:.0f} MHz"
            if abs(corr) > CORR_MAX:
                return None, f"{LABELS[profile]}: correction must be within ±{CORR_MAX:.0f} MHz"
            low, high = RX_RANGE[profile]
            middle = if_of((low + high) / 2, lo, corr)
            lo_if, hi_if = IF_RANGE[kind]
            if not lo_if <= middle <= hi_if:
                name = {"rtlsdr": "RTL-SDR", "hackrf": "HackRF"}.get(kind, "MiniTiouner / PicoTuner")
                return None, (f"{LABELS[profile]}, {name}: LO {lo:g} MHz gives an IF of {middle:.1f} MHz, "
                              f"outside the {lo_if:g}-{hi_if:g} MHz this receiver can tune")
            clean[profile][kind] = {"lo": lo, "corr": corr}
    return clean, ""
