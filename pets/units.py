"""Unit conversion. Stored units: grams (food, litter), millilitres (water), kilograms (weight), centimetres (length),
metres (walk distance)."""
MASS = {"g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
VOLUME = {"ml": 1.0, "fl oz": 29.5735295625}
WEIGHT = {"kg": 1.0, "lb": 0.45359237}
LENGTH = {"cm": 1.0, "in": 2.54}
DISTANCE = {"m": 1.0, "km": 1000.0, "mi": 1609.344}
TABLES = {"mass": MASS, "volume": VOLUME, "weight": WEIGHT, "length": LENGTH, "distance": DISTANCE, "bag": MASS}

DEFAULTS = {
    "metric": {"mass": "g", "volume": "ml", "weight": "kg", "length": "cm", "distance": "km", "bag": "kg"},
    "imperial": {"mass": "oz", "volume": "fl oz", "weight": "lb", "length": "in", "distance": "mi", "bag": "lb"},
}

# One unit per kind when a household doesn't use one system for everything (PUP-140): what each kind is, and the
# units it can be shown in.
KINDS = ["mass", "volume", "weight", "length", "distance", "bag"]
PER_KIND = {"mass": ["g", "oz"], "volume": ["ml", "fl oz"], "weight": ["kg", "lb"], "length": ["cm", "in"],
            "distance": ["km", "mi"], "bag": ["kg", "lb"]}


def unit_for(household, kind):
    """The unit a household sees for a kind: its own pick when units are set one by one, else its system's."""
    if not household.same_units:
        picked = getattr(household, f"unit_{kind}", "")
        if picked:
            return picked
    return DEFAULTS[household.units][kind]


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
    unit = unit_for(household, kind)
    value = from_stored(value, unit, TABLES[kind])
    if unit in ("g", "ml") and digits == 1:
        digits = 0  # to the nearest gram or millilitre
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}"
