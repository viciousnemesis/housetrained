"""CSV import (PUP-35). Each row goes through the same form as the logging pages, so units, choices and checks match.

Rows refer to pets, bowls and walkers by name. Nothing is imported unless every row is valid; rows that match
something already logged are skipped.
"""
import csv
import io
from dataclasses import dataclass, field

from django.db import transaction
from django.utils.translation import gettext as _

from . import forms
from .models import AccidentSpot, Container, Fill, Meal, Measurement, Pet, PottyBreak, Puke, Sleep, Walk

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
YES = {"1", "y", "yes", "true", "x", "on"}


@dataclass
class Kind:
    label: str
    form: type
    columns: list
    example: list
    model: type = None


KINDS = {
    "potty": Kind(_("Potty breaks"), forms.PottyBreakForm, ["pet", "time", "pee", "poop", "location", "notes"],
                  ["Ruby", "2026-10-07 07:10", "yes", "no", "outside", ""], PottyBreak),
    "walk": Kind(_("Walks"), forms.WalkForm,
                 ["pets", "start", "end", "walker", "distance", "distance_unit", "notes"],
                 ["Ruby; Rex", "2026-10-07 07:00", "2026-10-07 07:25", "Max", "1.2", "km", ""], Walk),
    "sleep": Kind(_("Sleep"), forms.SleepForm, ["pet", "start", "end", "kind", "notes"],
                  ["Ruby", "2026-10-07 13:00", "2026-10-07 14:30", "nap", ""], Sleep),
    "meal": Kind(_("Meals"), forms.MealForm,
                 ["pet", "time", "food", "amount", "unit", "water", "water_amount", "water_unit", "notes"],
                 ["Ruby", "2026-10-07 07:30", "yes", "60", "g", "no", "", "", ""], Meal),
    "fill": Kind(_("Bowl fills"), forms.FillForm, ["bowl", "time", "amount", "unit", "eaten_right_away", "notes"],
                 ["Food bowl", "2026-10-07 07:30", "0.5", "bowls", "yes", ""], Fill),
    "measurement": Kind(_("Measurements"), forms.MeasurementForm, ["pet", "date", "kind", "value", "unit", "notes"],
                        ["Ruby", "2026-10-07", "weight", "12.5", "lb", ""], Measurement),
    "puke": Kind(_("Puke"), forms.PukeForm, ["pet", "time", "location", "found_later", "spot", "contents", "notes"],
                 ["Ruby", "2026-10-07 08:15", "inside", "no", "Kitchen", "grass", ""], Puke),
}


@dataclass
class Row:
    number: int
    values: dict
    errors: list = field(default_factory=list)
    duplicate: bool = False
    form: object = None


def template_csv(kind):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(KINDS[kind].columns)
    writer.writerow(KINDS[kind].example)
    return out.getvalue()


def _yes(value):
    return (value or "").strip().lower() in YES


class Importer:
    def __init__(self, household, user, kind):
        self.household, self.user, self.kind = household, user, KINDS[kind]
        self.kind_key = kind
        self.pets = {p.name.strip().lower(): p for p in household.pets.all()}
        self.bowls = {c.name.strip().lower(): c for c in Container.objects.filter(household=household)}
        self.members = {}
        for u in household.members.all():
            for name in (u.first_name, u.username, u.get_full_name()):
                if name:
                    self.members.setdefault(name.strip().lower(), u)

    def read(self, text):
        """Parse and check every row. Returns (rows, problems) where problems are file-level messages."""
        if len(text.encode()) > MAX_BYTES:
            return [], [_("The file is too big (2 MB at most).")]
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        if not reader.fieldnames:
            return [], [_("The file is empty.")]
        headers = [h.strip().lower() for h in reader.fieldnames]
        missing = [c for c in self.kind.columns if c not in headers and c != "notes"]
        if missing:
            return [], [_("Missing columns: %(columns)s. Download the template to see the layout.")
                        % {"columns": ", ".join(missing)}]
        rows, seen = [], set()
        for number, raw in enumerate(reader, start=2):
            if number - 1 > MAX_ROWS:
                return [], [_("Too many rows (%(max)d at most).") % {"max": MAX_ROWS}]
            values = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not any(values.values()):
                continue
            row = self.check(Row(number, values))
            key = tuple(sorted(values.items()))
            row.duplicate = row.duplicate or key in seen  # the same row twice in the file
            seen.add(key)
            rows.append(row)
        if not rows:
            return [], [_("No rows to import.")]
        return rows, []

    def pet(self, row, name):
        pet = self.pets.get(name.strip().lower())
        if not pet:
            row.errors.append(_("Unknown pet “%(name)s”.") % {"name": name})
        return pet

    def data_for(self, row):
        v = row.values
        k = self.kind_key
        if k == "walk":
            names = [n for n in v.get("pets", "").replace(",", ";").split(";") if n.strip()]
            pets = [p for p in (self.pet(row, n) for n in names) if p]
            walker = self.members.get(v.get("walker", "").lower())
            data = {"pets": [p.pk for p in pets], "start": v.get("start"), "end": v.get("end"),
                    "distance": v.get("distance"), "distance_unit": v.get("distance_unit") or "km",
                    "notes": v.get("notes", "")}
            if walker:
                data["walker_choice"] = str(walker.pk)
            elif v.get("walker"):
                data.update(walker_choice="other", walker_name=v["walker"])
            return data
        if k == "fill":
            bowl = self.bowls.get(v.get("bowl", "").lower())
            if not bowl:
                row.errors.append(_("Unknown bowl “%(name)s”.") % {"name": v.get("bowl", "")})
            data = {"container": bowl.pk if bowl else "", "time": v.get("time"), "amount": v.get("amount"),
                    "unit": v.get("unit"), "notes": v.get("notes", "")}
            if _yes(v.get("eaten_right_away")):
                data["eaten_now"] = "on"
            return data
        pet = self.pet(row, v.get("pet", ""))
        data = {"pets": [pet.pk] if pet else [], "notes": v.get("notes", "")}
        if k == "potty":
            data.update(time=v.get("time"), location=v.get("location", "").lower())
            for flag in ("pee", "poop"):
                if _yes(v.get(flag)):
                    data[flag] = "on"
        elif k == "puke":
            data.update(time=v.get("time"), location=v.get("location", "").lower() or "inside",
                        contents=v.get("contents", "").lower() or Puke.FOOD)
            if v.get("spot"):  # only spots already named: checking a file doesn't add any
                spot = AccidentSpot.objects.filter(household=self.household, name__iexact=v["spot"].strip()).first()
                if spot:
                    data["spot"] = spot.pk
                else:
                    row.errors.append(_("Unknown spot “%(name)s”.") % {"name": v["spot"]})
            if _yes(v.get("found_later")):
                data["found_later"] = "on"
        elif k == "sleep":
            data.update(start=v.get("start"), end=v.get("end"), kind=v.get("kind", "").lower() or "nap")
        elif k == "meal":
            data.update(time=v.get("time"), amount=v.get("amount"), unit=v.get("unit") or "g",
                        water_amount=v.get("water_amount"), water_unit=v.get("water_unit") or "ml")
            for flag in ("food", "water"):
                if _yes(v.get(flag)):
                    data[flag] = "on"
        elif k == "measurement":
            unit, kind = v.get("unit", ""), v.get("kind", "").lower()
            data.update(date=v.get("date"), kind=kind, value=v.get("value"))
            data["weight_unit" if kind == Measurement.WEIGHT else "length_unit"] = unit or (
                "kg" if kind == Measurement.WEIGHT else "cm")
        return data

    def check(self, row):
        data = self.data_for(row)
        form = self.kind.form(data, household=self.household, user=self.user)
        if not form.is_valid():
            known = bool(row.errors)  # an unknown pet or bowl already explains the empty field
            columns = {"pets": "pet", "container": "bowl", "eaten_now": "eaten_right_away", "walker_choice": "walker"}
            for name, errors in form.errors.items():
                if known and name in ("pets", "container"):
                    continue
                label = "" if name == "__all__" else f"{columns.get(name, name)}: "
                row.errors.extend(f"{label}{e}" for e in errors)
        else:
            row.form = form
            row.duplicate = self.exists(form.cleaned_data)
        return row

    def exists(self, data):
        k = self.kind_key
        pets = data.get("pets")
        if k == "potty":
            return PottyBreak.objects.filter(pet__in=pets, time=data["time"]).exists()
        if k == "meal":
            return Meal.objects.filter(pet__in=pets, time=data["time"]).exists()
        if k == "puke":
            return Puke.objects.filter(pet__in=pets, time=data["time"]).exists()
        if k == "sleep":
            return Sleep.objects.filter(pet__in=pets, start=data["start"]).exists()
        if k == "measurement":
            return Measurement.objects.filter(pet__in=pets, date=data["date"], kind=data["kind"]).exists()
        if k == "walk":
            return Walk.objects.filter(household=self.household, start=data["start"], pets__in=pets).exists()
        if k == "fill":
            return Fill.objects.filter(container=data["container"], time=data["time"]).exists()
        return False

    @transaction.atomic
    def save(self, rows):
        """Save every new row. Call only when no row has errors."""
        saved = 0
        for row in rows:
            if row.duplicate:
                continue
            form = row.form
            entries = form.save_entries() if hasattr(form, "save_entries") else [form.save()]
            saved += len(entries)
        return saved
