"""Export everything as CSV (PUP-117): a zip with one file per kind, in the same columns as the importer, so a file
can be imported back. A few extra columns carry details the importer doesn't need (it ignores them).

Amounts are in the stored units (grams, millilitres, metres, kilograms, centimetres), times in the household's
local time, as the importer reads them.
"""
import csv
import io
import zipfile

from django.utils import timezone

from .models import (
    Command, Container, Fill, Meal, Measurement, Note, Pet, PottyBreak, Puke, Sleep, TrainingLog, Walk,
)


def _time(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


def _yes(flag):
    return "yes" if flag else "no"


def _num(value):
    return "" if value is None else f"{value:g}"


def _walker(walk):
    if walk.walker:
        return walk.walker.first_name or walk.walker.username
    return walk.walker_name


def tables(household):
    """{filename: (columns, rows)} for the household."""
    pets = Pet.objects.filter(household=household)
    potty = PottyBreak.everything.filter(pet__household=household).select_related("pet", "spot").order_by("time")
    walks = Walk.objects.filter(household=household).select_related("walker").prefetch_related("pets").order_by("start")
    sleeps = Sleep.objects.filter(pet__household=household).select_related("pet").order_by("start")
    meals = Meal.objects.filter(pet__household=household).select_related("pet").order_by("time")
    pukes = Puke.objects.filter(pet__household=household).select_related("pet", "spot").order_by("time")
    fills = Fill.objects.filter(container__household=household).select_related("container").order_by("time")
    measurements = Measurement.objects.filter(pet__household=household).select_related("pet").order_by("date")
    notes = Note.objects.filter(household=household).select_related("pet").order_by("time")
    commands = Command.objects.filter(pet__household=household).select_related("pet").order_by("pet__name", "name")
    training = TrainingLog.objects.filter(command__pet__household=household).select_related("command__pet").order_by("time")
    return {
        "potty.csv": (["pet", "time", "pee", "poop", "location", "notes", "found_later", "spot"], [
            [b.pet.name, _time(b.time), _yes(b.pee), _yes(b.poop), b.location, b.notes, _yes(b.found_later),
             b.spot.name if b.spot_id else ""] for b in potty]),
        "walk.csv": (["pets", "start", "end", "walker", "distance", "distance_unit", "notes"], [
            ["; ".join(p.name for p in w.pets.all()), _time(w.start), _time(w.end), _walker(w), _num(w.distance),
             "m" if w.distance is not None else "", w.notes] for w in walks]),
        "puke.csv": (["pet", "time", "location", "found_later", "spot", "contents", "notes"], [
            [p.pet.name, _time(p.time), p.location, _yes(p.found_later), p.spot.name if p.spot_id else "", p.contents,
             p.notes] for p in pukes]),
        "sleep.csv": (["pet", "start", "end", "kind", "notes"], [
            [s.pet.name, _time(s.start), _time(s.end), s.kind, s.notes] for s in sleeps]),
        "meal.csv": (["pet", "time", "food", "amount", "unit", "water", "water_amount", "water_unit", "notes",
                      "portion", "water_portion"], [
            [m.pet.name, _time(m.time), _yes(m.food), _num(m.amount), "g" if m.amount is not None else "",
             _yes(m.water), _num(m.water_amount), "ml" if m.water_amount is not None else "", m.notes,
             _num(m.portion), _num(m.water_portion)] for m in meals]),
        "fill.csv": (["bowl", "time", "amount", "unit", "eaten_right_away", "notes", "contents"], [
            [f.container.name, _time(f.time), _num(f.amount if f.amount is not None else f.bowls),
             ("ml" if f.contents == Fill.WATER else "g") if f.amount is not None else ("bowls" if f.bowls else ""),
             _yes(f.meals.exists()), f.notes, f.contents] for f in fills]),
        "measurement.csv": (["pet", "date", "kind", "value", "unit", "notes"], [
            [m.pet.name, m.date.isoformat(), m.kind, _num(m.value), Measurement.UNITS[m.kind], m.notes]
            for m in measurements]),
        "notes.csv": (["pet", "time", "text"], [[n.pet.name if n.pet_id else "", _time(n.time), n.text] for n in notes]),
        "commands.csv": (["pet", "command", "status", "how_to", "when_to_use"], [
            [c.pet.name, c.name, c.status, c.how_to, c.when_to_use] for c in commands]),
        "training.csv": (["pet", "command", "time", "score", "notes"], [
            [t.command.pet.name, t.command.name, _time(t.time), t.score, t.notes] for t in training]),
        "pets.csv": (["name", "type", "breed", "sex", "birth_date", "pee_every_minutes", "poop_every_minutes",
                      "out_after_meal_minutes", "active", "notes"], [
            [p.name, p.type, p.breed, p.sex, p.birth_date.isoformat() if p.birth_date else "", _num(p.pee_minutes),
             _num(p.poop_minutes), _num(p.after_meal), _yes(p.active), p.notes] for p in pets]),
        "bowls.csv": (["name", "kind", "used_by", "amount_per_bowl", "active"], [
            [c.name, c.kind, "; ".join(p.name for p in c.pets.all()), _num(c.amount_per_bowl), _yes(c.active)]
            for c in Container.objects.filter(household=household).prefetch_related("pets")]),
    }


def export_zip(household):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (columns, rows) in tables(household).items():
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow(columns)
            writer.writerows(rows)
            archive.writestr(name, text.getvalue().encode("utf-8-sig"))  # BOM: opens cleanly in Excel
    return out.getvalue()
