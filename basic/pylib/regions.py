"""Regionen aus der Postleitzahl (erste Ziffer = Leitzone)."""

ZONES = (
    ("0", "Ost"),
    ("1", "Nordost"),
    ("2", "Nord"),
    ("3", "Mitte"),
    ("4", "West"),
    ("5", "West"),
    ("6", "Südwest"),
    ("7", "Süd"),
    ("8", "Süd"),
    ("9", "Südost"),
)


def region_of(zip_code):
    if not zip_code:
        return None
    for prefix, name in ZONES:
        if zip_code.startswith(prefix):
            return name
    return None
