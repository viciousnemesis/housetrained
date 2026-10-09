"""Unit conversion. Stored units: grams (food, litter), millilitres (water), kilograms (weight), centimetres (length),
metres (walk distance)."""
MASS = {"g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
VOLUME = {"ml": 1.0, "fl oz": 29.5735295625}
WEIGHT = {"kg": 1.0, "lb": 0.45359237}
LENGTH = {"cm": 1.0, "in": 2.54}
DISTANCE = {"m": 1.0, "km": 1000.0, "mi": 1609.344}
TABLES = {"mass": MASS, "volume": VOLUME, "weight": WEIGHT, "length": LENGTH, "distance": DISTANCE}

DEFAULTS = {
    "metric": {"mass": "g", "volume": "ml", "weight": "kg", "length": "cm", "distance": "km"},
    "imperial": {"mass": "oz", "volume": "fl oz", "weight": "lb", "length": "in", "distance": "mi"},
}


def choices(table):
    return [(unit, unit) for unit in table]


def to_stored(value, unit, table):
    return value * table[unit]


def from_stored(value, unit, table):
    return value / table[unit]


def fmt(value, unit):
    return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"


def show(value, kind, household, digits=1):
    """A stored value as text in the household's units, e.g. show(907, "mass", h) -> "32 oz"."""
    unit = DEFAULTS[household.units][kind]
    value = from_stored(value, unit, TABLES[kind])
    if unit in ("g", "ml") and digits == 1:
        digits = 0  # to the nearest gram or millilitre
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}"
