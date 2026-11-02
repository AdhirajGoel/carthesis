#!/usr/bin/env python3
"""Match used listings to the current MSRP and CarPlay reference workbook."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import json
import os
import math


REFERENCE_WORKBOOK = Path(os.environ.get("LOTLOGIC_REFERENCE", str(Path(__file__).resolve().parent / "data" / "reference.json")))


def key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sheet_rows(sheet, required: set[str]):
    rows = sheet.iter_rows(values_only=True)
    for row in rows:
        headers = {key(value): index for index, value in enumerate(row) if value}
        if required <= headers.keys():
            return headers, rows
    raise ValueError(f"{sheet.title} has no usable header row")


@lru_cache(maxsize=4)
def load_reference(path: Path = REFERENCE_WORKBOOK) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """Load and validate current vehicles and CarPlay rules from the reference workbook."""
    path = Path(path)
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        catalog, rules = data.get("catalog"), data.get("carplay")
        if not isinstance(catalog, list) or not catalog or not isinstance(rules, list):
            raise ValueError("Reference JSON needs a nonempty catalog array and a carplay array")
        for row in catalog:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ("make", "model", "trim", "body", "powertrain", "drivetrain")):
                raise ValueError("Invalid reference vehicle fields")
            if not isinstance(row.get("year"), int) or not 1900 <= row["year"] <= 2100:
                raise ValueError("Invalid reference model year")
            if not isinstance(row.get("msrp"), (int, float)) or not math.isfinite(row["msrp"]) or row["msrp"] <= 0:
                raise ValueError("Invalid reference MSRP")
        for row in rules:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ("make", "model", "years", "body", "qualifying")):
                raise ValueError("Invalid CarPlay rule")
        return tuple(catalog), tuple(rules)
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Reading the vehicle reference requires openpyxl") from None

    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Vehicle reference workbook not found: {path}")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        try:
            msrp_sheet = workbook["Current MSRP"]
            carplay_sheet = workbook["Apple CarPlay"]
        except KeyError as error:
            raise RuntimeError(f"{path.name} is missing worksheet {error.args[0]!r}") from None

        headers, rows = _sheet_rows(msrp_sheet, {"make", "model", "trim", "startingmsrp"})
        catalog = []
        for row in rows:
            make = row[headers["make"]] if headers["make"] < len(row) else None
            model = row[headers["model"]] if headers["model"] < len(row) else None
            trim = row[headers["trim"]] if headers["trim"] < len(row) else None
            msrp = _number(row[headers["startingmsrp"]]) if headers["startingmsrp"] < len(row) else None
            if not make and not model and not trim:
                continue
            if not make or not model or not trim or msrp is None or msrp <= 0:
                raise RuntimeError(f"{path.name} has an invalid Current MSRP row")
            year_value = row[headers.get("modelyear", -1)] if headers.get("modelyear", -1) >= 0 else None
            catalog.append({
                "year": int(float(year_value)),
                "make": str(make).strip(),
                "model": str(model).strip(),
                "body": str(row[headers.get("bodystyleversion", -1)] or "").strip()
                if headers.get("bodystyleversion", -1) >= 0 else "",
                "trim": str(trim).strip(),
                "powertrain": str(row[headers.get("powertrain", -1)] or "").strip()
                if headers.get("powertrain", -1) >= 0 else "",
                "drivetrain": str(row[headers.get("drivetrain", -1)] or "").strip()
                if headers.get("drivetrain", -1) >= 0 else "",
                "msrp": int(msrp) if msrp.is_integer() else msrp,
            })

        headers, rows = _sheet_rows(
            carplay_sheet,
            {"make", "model", "modelyears", "bodystyle", "factorycarplayequippedtrims"},
        )
        rules = []
        for row in rows:
            make = row[headers["make"]] if headers["make"] < len(row) else None
            model = row[headers["model"]] if headers["model"] < len(row) else None
            if not make and not model:
                continue
            if not make or not model:
                raise RuntimeError(f"{path.name} has an invalid Apple CarPlay row")
            rules.append({
                "make": str(make).strip(),
                "model": str(model).strip(),
                "years": str(row[headers["modelyears"]] or "").strip(),
                "body": str(row[headers["bodystyle"]] or "").strip(),
                "qualifying": str(row[headers["factorycarplayequippedtrims"]] or "").strip(),
            })
    finally:
        workbook.close()
    if not catalog or not rules:
        raise RuntimeError(f"{path.name} has no usable vehicle reference data")
    return tuple(catalog), tuple(rules)


def _power(value, trim="") -> str:
    power, trim = key(value), key(trim)
    if "plugin" in power or "phev" in power or "prime" in trim:
        return "plugin"
    if power in {"hev", "mhev", "hybrid"} or "hybrid" in power or "hybrid" in trim:
        return "hybrid"
    if "turbo" in power or "turbo" in trim or trim.endswith("xt"):
        return "turbo"
    return "gas"


def _current_power(value) -> str:
    value = key(value)
    if "pluginhybrid" in value:
        return "plugin"
    if "hybrid" in value:
        return "hybrid"
    if "turbo" in value:
        return "turbo"
    return "gas"


def _body_candidates(candidates: list[dict], model: str, body_type) -> list[dict]:
    bodies = {key(row["body"]) for row in candidates}
    if len(bodies) <= 1:
        return candidates
    body = key(body_type)
    if "hatch" in body:
        target = "hatchback"
    elif "sedan" in body or (model == "civic" and "coupe" in body):
        target = "sedan"
    else:
        return []
    return [row for row in candidates if key(row["body"]) == target]


def _historical_target(make: str, model: str, trim: str, power: str):
    """Return (current trim key, current power class) for known renamed trims."""
    if make == "subaru":
        if model == "impreza":
            if "limited" in trim or trim == "rs":
                return "rs", "gas"
            if trim in {"base", "premium", "20i", "i"} or "sport" in trim:
                return "sport", "gas"
        if model == "forester":
            for marker in ("touring", "limited", "wilderness", "sport", "premium", "base"):
                if marker in trim:
                    return marker, "gas"
        if model == "outback":
            if "touring" in trim and "xt" in trim:
                return "touringxt", "turbo"
            if "limited" in trim and "xt" in trim:
                return "limitedxt", "turbo"
            if "onyx" in trim or "wilderness" in trim:
                return "wilderness", "turbo"
            if "touring" in trim:
                return "touring", "gas"
            if "limited" in trim:
                return "limited", "gas"
            if "premium" in trim or "base" in trim:
                return "premium", "gas"
        if model == "crosstrek":
            if power == "hybrid" or "hybrid" in trim:
                return ("limitedhybrid" if "limited" in trim else "sporthybrid"), "hybrid"
            for marker in ("wilderness", "limited", "sport", "premium", "base"):
                if marker in trim:
                    return marker, "gas"

    if make == "mazda":
        if model == "cx30":
            if "turbopremiumplus" in trim:
                return "25turbopremiumplus", "turbo"
            if "turbo" in trim:
                return "25turboaireedition", "turbo"
            if "carbon" in trim:
                return "25scarbonedition", "gas"
            if "premium" in trim:
                return "25spremium", "gas"
            if "preferred" in trim:
                return "25spreferred", "gas"
            if "select" in trim:
                return "25sselectsport", "gas"
            if trim in {"s", "base", "25s"}:
                return "25s", "gas"
        if model == "cx5":
            if any(marker in trim for marker in ("signature", "grandtouringreserve", "premiumplus")):
                return "25spremiumplus", "gas"
            if "grandtouring" in trim or ("premium" in trim and "plus" not in trim):
                return "25spremium", "gas"
            if "carbon" in trim or "preferred" in trim:
                return "25spreferred", "gas"
            if "touring" in trim or "select" in trim:
                return "25sselect", "gas"
            if trim in {"s", "sport", "base", "25s"}:
                return "25s", "gas"
        if model == "mazda3":
            if "turbo" in trim or "premiumplus" in trim:
                return "25turbopremiumplus", "turbo"
            if "carbon" in trim or "premium" in trim:
                return "25scarbonedition", "gas"
            if "preferred" in trim or "touring" in trim:
                return "25spreferred", "gas"
            if "select" in trim:
                return "25sselectsport", "gas"
            if trim in {"s", "sport", "isport", "base", "25s"}:
                return "25s", "gas"

    if make == "honda":
        if model == "civic":
            if "typer" in trim:
                return "typer", "turbo"
            if trim == "si":
                return "si", "turbo"
            if "touring" in trim or "exl" in trim:
                return "sporttouringhybrid", "hybrid"
            if trim == "ex" or "hybrid" in trim:
                return "sporthybrid", "hybrid"
            if "sport" in trim:
                return "sport", "gas"
            if trim in {"lx", "base"}:
                return "lx", "gas"
        if model == "accord":
            if "touring" in trim:
                return "touringhybrid", "hybrid"
            if "sportl" in trim:
                return "sportlhybrid", "hybrid"
            if "exl" in trim:
                return "exlhybrid", "hybrid"
            if trim in {"hybrid", "hybridsport"} or trim == "sport":
                return "sporthybrid", "hybrid"
            if trim in {"ex", "se", "sportse"}:
                return "se", "turbo"
            if trim == "lx":
                return "lx", "turbo"
        if model == "hrv":
            if any(marker in trim for marker in ("touring", "exl", "ex")):
                return "exl", "gas"
            if "sport" in trim:
                return "sport", "gas"
            if trim in {"lx", "base"}:
                return "lx", "gas"
        if model == "crv":
            if "touring" in trim:
                return "sporttouringhybrid", "hybrid"
            if power == "hybrid" and "exl" in trim:
                return "sportlhybrid", "hybrid"
            if power == "hybrid" and (trim == "ex" or "sport" in trim):
                return "sporthybrid", "hybrid"
            if "trailsport" in trim:
                return "trailsporthybrid", "hybrid"
            if "exl" in trim:
                return "exl", "turbo"
            if trim in {"se", "specialedition", "ex"}:
                return "ex", "turbo"
            if trim == "lx":
                return "lx", "turbo"

    if make == "toyota":
        if model == "corolla":
            if power == "hybrid" or "hybrid" in trim:
                if "xle" in trim or "xse" in trim:
                    return "hybridxle", "hybrid"
                if "se" in trim:
                    return "hybridse", "hybrid"
                return "hybridle", "hybrid"
            if "xse" in trim or "xle" in trim:
                return "xse", "gas"
            if any(marker in trim for marker in ("nightshade", "apex", "se")):
                return "se", "gas"
            if trim in {"l", "le", "leeco", "base"}:
                return "le", "gas"
        if model == "camry":
            if "nightshade" in trim:
                return "nightshade", "hybrid"
            if "xse" in trim or "trd" in trim:
                return "xse", "hybrid"
            if "xle" in trim:
                return "xle", "hybrid"
            if "se" in trim:
                return "se", "hybrid"
            if trim in {"l", "le", "hybrid", "hybridle"}:
                return "le", "hybrid"
        if model == "rav4":
            if power == "plugin" or "prime" in trim:
                if "grsport" in trim:
                    return "grsport", "plugin"
                if "xse" in trim:
                    return "xse", "plugin"
                if "woodland" in trim:
                    return "woodland", "plugin"
                return "se", "plugin"
            if "limited" in trim:
                return "limited", "hybrid"
            if "xse" in trim:
                return "xse", "hybrid"
            if any(marker in trim for marker in ("woodland", "adventure", "trdoffroad")):
                return "woodland", "hybrid"
            if "xle" in trim:
                return "xlepremium", "hybrid"
            if trim == "se":
                return "se", "hybrid"
            if trim in {"l", "le", "base"}:
                return "le", "hybrid"
        if model == "corollacross":
            if power == "hybrid" or "hybrid" in trim:
                if "xse" in trim:
                    return "hybridxse", "hybrid"
                if "se" in trim:
                    return "hybridse", "hybrid"
                return "hybrids", "hybrid"
            if "xle" in trim:
                return "xle", "gas"
            if trim == "le":
                return "le", "gas"
            if trim == "l":
                return "l", "gas"
    return None


def match_current_vehicle(car: dict, catalog: tuple[dict, ...]) -> dict | None:
    """Return one deterministic current equivalent, or None when no safe match exists."""
    make, model, trim = key(car.get("make")), key(car.get("model")), key(car.get("trim"))
    if not make or not model or not trim:
        return None
    candidates = [row for row in catalog if key(row["make"]) == make and key(row["model"]) == model]
    candidates = _body_candidates(candidates, model, car.get("body_type"))
    if not candidates:
        return None

    used_power = _power(car.get("powertrain"), car.get("trim"))
    exact = [row for row in candidates if key(row["trim"]) == trim]
    if exact:
        same_power = [row for row in exact if _current_power(row["powertrain"]) == used_power]
        if len(same_power) == 1:
            return same_power[0]
        available_powers = {_current_power(row["powertrain"]) for row in candidates}
        if len(exact) == 1 and used_power not in available_powers:
            return exact[0]

    target = _historical_target(make, model, trim, used_power)
    if not target:
        return None
    target_trim, target_power = target
    matches = [
        row for row in candidates
        if key(row["trim"]) == target_trim and _current_power(row["powertrain"]) == target_power
    ]
    return matches[0] if len(matches) == 1 else None


def _year_matches(spec: str, year: int) -> bool:
    spec = spec.replace("–", "-").replace("—", "-").strip()
    try:
        if spec.endswith("+"):
            return year >= int(spec[:-1])
        if "-" in spec:
            start, end = spec.split("-", 1)
            return int(start) <= year <= int(end)
        return year == int(spec)
    except ValueError:
        return False


def _body_matches(spec, body_type) -> bool:
    spec, body = key(spec), key(body_type)
    if spec in {"all", "allbodystyles"}:
        return True
    if spec == "sedanhatchback":
        return "sedan" in body or "hatch" in body
    if spec == "sedancoupe":
        return "sedan" in body or "coupe" in body
    if spec == "hatchback":
        return "hatch" in body
    return bool(body) and spec in body


CARPLAY_ORDERS = {
    ("mazda", "cx5"): [
        ("sport",), ("touring",), ("grandtouring",), ("grandtouringreserve",), ("signature",),
    ],
    ("mazda", "mazda3"): [
        ("20", "20s"), ("s", "25s", "base"), ("select",), ("preferred",),
        ("carbon",), ("premium",), ("turbo",), ("turbopremium",), ("turbopremiumplus",),
    ],
    ("honda", "civic"): [
        ("lx",), ("sport",), ("ex",), ("exl",), ("touring", "sporttouring"), ("si",), ("typer",),
    ],
    ("honda", "accord"): [
        ("lx", "hybrid"), ("sport", "se", "sportse", "hybridsport"), ("ex",),
        ("exl", "hybridexl"), ("sportl",), ("touring", "hybridtouring"),
    ],
    ("honda", "hrv"): [("lx",), ("sport",), ("ex",), ("exl",), ("touring",)],
    ("honda", "crv"): [
        ("lx",), ("se", "specialedition"), ("ex",), ("exl",), ("touring", "sporttouring"),
    ],
}


def _marker_matches(marker: str, trim: str) -> bool:
    return marker == trim if len(marker) <= 2 else marker in trim


def _trim_rank(make: str, model: str, trim: str):
    order = CARPLAY_ORDERS.get((make, model), ())
    for rank in range(len(order) - 1, -1, -1):
        if any(_marker_matches(marker, trim) for marker in order[rank]):
            return rank
    return None


def factory_carplay(
    year, make, model, trim="", body_type="", powertrain="", rules: tuple[dict, ...] | None = None,
) -> bool | None:
    """Evaluate the matching rule from the Apple CarPlay worksheet."""
    try:
        year = int(float(year))
    except (TypeError, ValueError):
        return None
    make_key, model_key, trim_key = key(make), key(model), key(trim)
    rules = load_reference()[1] if rules is None else rules
    matches = [
        rule for rule in rules
        if key(rule["make"]) == make_key and key(rule["model"]) == model_key
        and _year_matches(rule["years"], year)
    ]
    if key(body_type):
        matches = [rule for rule in matches if _body_matches(rule["body"], body_type)]
    if len(matches) != 1:
        return None
    qualifying = matches[0]["qualifying"]
    qualifier_key = key(qualifying)
    if qualifier_key == "alltrims":
        return True
    if not trim_key:
        return None
    if "allhybridtrims" in qualifier_key and _power(powertrain, trim) == "hybrid":
        return True
    primary = qualifying.split(";", 1)[0].strip()
    if " and above" in primary.casefold():
        threshold = key(primary.casefold().split(" and above", 1)[0])
        rank = _trim_rank(make_key, model_key, trim_key)
        threshold_rank = _trim_rank(make_key, model_key, threshold)
        return None if rank is None or threshold_rank is None else rank >= threshold_rank
    if "," in primary:
        return any(key(part) in trim_key for part in primary.split(",") if key(part))
    return None


def enrich_cars(cars: list[dict], reference=None) -> dict:
    """Add auditable current-equivalent, MSRP difference, and CarPlay fields in place."""
    catalog, rules = reference or load_reference()
    unmatched = set()
    unverified = 0
    matched = 0
    for car in cars:
        current = match_current_vehicle(car, catalog)
        price = _number(car.get("price"))
        if current and price is not None:
            current_msrp = current["msrp"]
            difference = abs(float(current_msrp) - price)
            car["current_trim_match"] = " | ".join(str(value) for value in (
                current["year"], current["make"], current["model"], current["body"],
                current["trim"], current["powertrain"],
            ))
            car["current_msrp"] = current_msrp
            car["msrp_difference"] = int(difference) if difference.is_integer() else round(difference, 2)
            matched += 1
        else:
            car["current_trim_match"] = ""
            car["current_msrp"] = ""
            car["msrp_difference"] = ""
            unmatched.add(" | ".join(str(car.get(field, "")) for field in (
                "year", "make", "model", "trim", "body_type", "powertrain",
            )))
        carplay = factory_carplay(
            car.get("year"), car.get("make"), car.get("model"), car.get("trim"),
            car.get("body_type"), car.get("powertrain"), rules,
        )
        car["carplay"] = "yes" if carplay is True else "no" if carplay is False else "unverified"
        unverified += carplay is None
    return {
        "matched": matched,
        "unmatched": len(cars) - matched,
        "unmatched_examples": tuple(sorted(unmatched)[:10]),
        "carplay_unverified": unverified,
    }


def self_test() -> None:
    catalog, rules = load_reference()
    assert len(catalog) == 105 and len(rules) == 28

    cases = [
        (("Subaru", "Impreza", "Limited", "Hatchback", "Combustion"), "RS"),
        (("Subaru", "Forester", "Touring", "SUV", "Combustion"), "Touring"),
        (("Subaru", "Outback", "Onyx Edition", "Wagon", "Combustion"), "Wilderness"),
        (("Subaru", "Crosstrek", "Hybrid", "SUV", "HEV"), "Sport Hybrid"),
        (("Mazda", "CX-30", "Turbo Premium", "SUV", "Combustion"), "2.5 Turbo Aire Edition"),
        (("Mazda", "CX-5", "Grand Touring Reserve", "SUV", "Combustion"), "2.5 S Premium Plus"),
        (("Mazda", "Mazda3", "Select AWD", "Sedan", "Combustion"), "2.5 S Select Sport"),
        (("Mazda", "Mazda3", "Premium", "Hatchback", "Combustion"), "2.5 S Carbon Edition"),
        (("Honda", "Civic", "EX", "Coupe", "Combustion"), "Sport Hybrid"),
        (("Honda", "Accord", "Hybrid Touring", "Sedan", "HEV"), "Touring Hybrid"),
        (("Honda", "Accord", "Sport SE", "Sedan", "Combustion"), "SE"),
        (("Honda", "HR-V", "EX", "SUV", "Combustion"), "EX-L"),
        (("Honda", "CR-V", "Hybrid EX-L", "SUV", "HEV"), "Sport-L Hybrid"),
        (("Honda", "CR-V", "EX", "SUV", "Combustion"), "EX"),
        (("Toyota", "Corolla", "L", "Sedan", "Combustion"), "LE"),
        (("Toyota", "Corolla", "XSE", "Hatchback", "Combustion"), "XSE"),
        (("Toyota", "Camry", "TRD", "Sedan", "Combustion"), "XSE"),
        (("Toyota", "RAV4", "XLE", "SUV", "Combustion"), "XLE Premium"),
        (("Toyota", "RAV4", "Prime XSE", "SUV", "PHEV"), "XSE"),
        (("Toyota", "Corolla Cross", "XLE", "SUV", "Combustion"), "XLE"),
    ]
    for (make, model, trim, body, powertrain), expected in cases:
        matched = match_current_vehicle({
            "make": make, "model": model, "trim": trim, "body_type": body,
            "powertrain": powertrain,
        }, catalog)
        assert matched and matched["trim"] == expected, (make, model, trim, matched)
    assert match_current_vehicle({
        "make": "Honda", "model": "Civic", "trim": "Mystery", "body_type": "Sedan",
        "powertrain": "Combustion",
    }, catalog) is None
    assert match_current_vehicle({
        "make": "Honda", "model": "Pilot", "trim": "EX-L", "body_type": "SUV",
        "powertrain": "Combustion",
    }, catalog) is None

    assert factory_carplay(2020, "Mazda", "CX-30", "S", "SUV", rules=rules) is False
    assert factory_carplay(2020, "Mazda", "CX-30", "Select", "SUV", rules=rules) is True
    assert factory_carplay(2019, "Mazda", "CX-5", "Sport", "SUV", rules=rules) is False
    assert factory_carplay(2019, "Mazda", "CX-5", "Touring", "SUV", rules=rules) is True
    assert factory_carplay(2021, "Mazda", "Mazda3", "2.0", "Sedan", rules=rules) is False
    assert factory_carplay(2021, "Mazda", "Mazda3", "S", "Sedan", rules=rules) is True
    assert factory_carplay(2019, "Honda", "Civic", "Sport", "Hatchback", rules=rules) is False
    assert factory_carplay(2019, "Honda", "Civic", "EX", "Hatchback", rules=rules) is True
    assert factory_carplay(2020, "Honda", "Accord", "LX", "Sedan", rules=rules) is False
    assert factory_carplay(2022, "Honda", "HR-V", "Sport", "SUV", rules=rules) is True
    assert factory_carplay(2022, "Honda", "CR-V", "EX", "SUV", "HEV", rules) is True
    assert factory_carplay(2021, "Subaru", "Forester", "Base", "SUV", rules=rules) is True
    assert factory_carplay(2019, "Toyota", "Corolla", "LE", "Sedan", rules=rules) is None
    assert factory_carplay(2019, "Toyota", "Corolla", "SE", "Hatchback", rules=rules) is True
    assert factory_carplay(2020, "Toyota", "Corolla", "LE", "Sedan", rules=rules) is True
    assert factory_carplay(2019, "Toyota", "Camry", "LE", "Sedan", rules=rules) is True
    assert factory_carplay(2019, "Toyota", "Camry", "LE", rules=rules) is True
    assert factory_carplay(2019, "Honda", "Civic", "Sport", rules=rules) is None
    assert factory_carplay(2019, "Toyota", "RAV4", "LE", "SUV", rules=rules) is True
    assert factory_carplay(2021, "Toyota", "Corolla Cross", "L", "SUV", rules=rules) is None
    assert factory_carplay(2022, "Toyota", "Corolla Cross", "L", "SUV", rules=rules) is True

    cars = [{
        "price": 40_000, "year": 2022, "make": "Toyota", "model": "Camry", "trim": "LE",
        "body_type": "Sedan", "powertrain": "Combustion",
    }]
    stats = enrich_cars(cars, (catalog, rules))
    assert cars[0]["current_msrp"] == 29_600 and cars[0]["msrp_difference"] == 10_400
    assert cars[0]["carplay"] == "yes" and stats["matched"] == 1
    unknown = [{
        "price": 20_000, "year": 2022, "make": "Honda", "model": "Pilot", "trim": "EX-L",
        "body_type": "SUV", "powertrain": "Combustion",
    }]
    stats = enrich_cars(unknown, (catalog, rules))
    assert unknown[0]["carplay"] == "unverified" and stats["carplay_unverified"] == 1


if __name__ == "__main__":
    self_test()
    print("Vehicle-reference self-test passed")
