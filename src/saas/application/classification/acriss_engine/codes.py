"""ACRISS letter dictionaries — the standard itself, as data.

Source: engine spec §3, §4, §6, §7, §10. These are the FULL standard tables;
which 4-char codes are materialized for the product is a separate concern
(acriss_codes.yaml). The engine always reasons in full ACRISS.
"""
from __future__ import annotations

CATEGORY_NAMES: dict[str, str] = {
    "M": "Mini",
    "N": "Mini Elite",
    "E": "Economy",
    "H": "Economy Elite",
    "C": "Compact",
    "D": "Compact Elite",
    "I": "Intermediate",
    "J": "Intermediate Elite",
    "S": "Standard",
    "R": "Standard Elite",
    "F": "Fullsize",
    "G": "Fullsize Elite",
    "P": "Premium",
    "U": "Premium Elite",
    "L": "Luxury",
    "W": "Luxury Elite",
    "O": "Oversize",
    "X": "Special",
}

# normal → Elite pairs (§3)
ELITE_OF: dict[str, str] = {
    "M": "N", "E": "H", "C": "D", "I": "J",
    "S": "R", "F": "G", "P": "U", "L": "W",
}

TYPE_NAMES: dict[str, str] = {
    "B": "2-3 Door",
    "C": "2/4 Door",
    "D": "4-5 Door",
    "W": "Wagon/Estate",
    "V": "Passenger Van",
    "L": "Limousine/Sedan",
    "S": "Sport",
    "T": "Convertible",
    "F": "SUV",
    "J": "Convertible SUV",
    "X": "Special",
    "P": "Pickup 2 Door",
    "Q": "Pickup 4 Door",
    "Z": "Special Offer Car",
    "E": "Coupe",
    "M": "Monospace",
    "R": "Recreational Vehicle",
    "H": "Motor Home",
    "Y": "2 Wheel Vehicle",
    "N": "Roadster",
    "G": "Crossover",
    "K": "Commercial Van/Truck",
}

TRANSMISSION_NAMES: dict[str, str] = {
    "M": "Manual Unspecified Drive",
    "N": "Manual 4WD",
    "C": "Manual AWD",
    "A": "Automatic Unspecified Drive",
    "B": "Automatic 4WD",
    "D": "Automatic AWD",
}

FUEL_NAMES: dict[str, str] = {
    "R": "Unspecified Fuel/Power Combustion Engine + Air",
    "N": "Unspecified Fuel/Power Combustion Engine + No Air",
    "D": "Diesel + Air",
    "Q": "Diesel + No Air",
    "H": "Hybrid",
    "I": "Plug-in Hybrid",
    "E": "Electric",
    "C": "Electric",
    "L": "LPG/Compressed Gas + Air",
    "S": "LPG/Compressed Gas + No Air",
    "A": "Hydrogen + Air",
    "B": "Hydrogen + No Air",
    "M": "Multi Fuel/Power + Air",
    "F": "Multi Fuel/Power + No Air",
    "V": "Petrol + Air",
    "Z": "Petrol + No Air",
    "U": "Ethanol + Air",
    "X": "Ethanol + No Air",
}

# Passenger Van first-two-letter table (§10). When the vehicle IS a passenger
# van, the first letter stops meaning the normal category and encodes capacity.
PASSENGER_VAN_FIRST_TWO: dict[str, str] = {
    "IV": "6+ seats",
    "JV": "Elite 6+ seats or 5+2",
    "SV": "7+ seats",
    "RV": "Elite 7+ seats",
    "FV": "7+ seats plus more space",
    "GV": "Elite 7+ seats plus more space",
    "PV": "8+ seats",
    "UV": "Elite 8+ seats",
    "LV": "9+ seats",
    "WV": "Elite 9+ seats",
    "XV": "12+ seats",
    "OV": "15+ seats",
}


def passenger_van_first_letter(seats: int, elite: bool = False) -> str:
    """Capacity → first letter for a confirmed passenger van (§10)."""
    if seats >= 15:
        return "O"
    if seats >= 12:
        return "X"
    if seats >= 9:
        return "W" if elite else "L"
    if seats >= 8:
        return "U" if elite else "P"
    if seats >= 7:
        return "R" if elite else "S"
    return "J" if elite else "I"  # 6+ (or 5+2 elite)
