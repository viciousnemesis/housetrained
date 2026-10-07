"""Copy existing Baby Buddy data into the pet tables (PUP-61).

Runs once: it does nothing if a household already exists. Baby Buddy's own tables are left untouched.
See docs/housetrained/data-model.md, "Migration of existing data".
"""
import re

from django.db import migrations

WATER_NOTE = re.compile(r"^\s*(lots of |bit of )?water\b", re.IGNORECASE)


def migrate(apps, schema_editor):
    Household = apps.get_model("pets", "Household")
    if Household.objects.exists():
        return

    User = apps.get_model("auth", "User")
    Membership = apps.get_model("pets", "Membership")
    Pet = apps.get_model("pets", "Pet")
    Container = apps.get_model("pets", "Container")
    Product = apps.get_model("pets", "Product")
    Fill = apps.get_model("pets", "Fill")
    Walk = apps.get_model("pets", "Walk")
    PottyBreak = apps.get_model("pets", "PottyBreak")
    Sleep = apps.get_model("pets", "Sleep")
    Meal = apps.get_model("pets", "Meal")
    Measurement = apps.get_model("pets", "Measurement")
    Medication = apps.get_model("pets", "Medication")
    Note = apps.get_model("pets", "Note")
    core = {name: apps.get_model("core", name) for name in (
        "Child", "DiaperChange", "TummyTime", "Feeding", "Sleep", "Note", "Weight", "Height", "Medication", "Timer",
    )}

    home = Household.objects.create(name="Home")
    for user in User.objects.all():
        Membership.objects.create(household=home, user=user, role="owner" if user.is_superuser else "member")
    walkers = {u.first_name.strip().lower(): u for u in User.objects.exclude(first_name="")}

    pets = {}
    for child in core["Child"].objects.all():
        pets[child.pk] = Pet.objects.create(
            household=home, name=f"{child.first_name} {child.last_name}".strip(), slug=child.slug, type="dog",
            birth_date=child.birth_date, picture=child.picture.name if child.picture else "",
        )
    if not pets:
        return

    for c in core["DiaperChange"].objects.all():
        PottyBreak.objects.create(
            pet=pets[c.child_id], time=c.time, pee=c.wet, poop=c.solid,
            location="inside" if c.color == "inside" else "outside", notes=c.notes or "",
        )

    for t in core["TummyTime"].objects.all():
        walker = walkers.get((t.milestone or "").strip().lower())
        walk = Walk.objects.create(
            household=home, start=t.start, end=t.end, walker=walker,
            walker_name="" if walker else (t.milestone or ""), notes=t.notes or "",
        )
        walk.pets.set([pets[t.child_id]])
    # A potty break that happened during a walk belongs to it.
    for walk in Walk.objects.filter(household=home).prefetch_related("pets"):
        PottyBreak.objects.filter(
            pet__in=walk.pets.all(), time__gte=walk.start, time__lte=walk.end, location="outside", walk=None,
        ).update(walk=walk)

    bowl = water_bowl = None
    feedings = core["Feeding"].objects.all()
    if feedings.exists():
        bowl = Container.objects.create(household=home, name="Food bowl", kind="food_bowl")
        bowl.pets.set(pets.values())
        dry_food = Product.objects.create(household=home, name="Dry food", category="dry_food")
    for f in feedings:
        if f.amount:
            Fill.objects.create(container=bowl, time=f.start, contents="food", amount=f.amount, notes=f.notes or "")
        else:
            Meal.objects.create(pet=pets[f.child_id], time=f.start, product=dry_food, notes=f.notes or "")

    for s in core["Sleep"].objects.all():
        Sleep.objects.create(
            pet=pets[s.child_id], start=s.start, end=s.end, kind="nap" if s.nap else "night", notes=s.notes or "",
        )

    for n in core["Note"].objects.all():
        if WATER_NOTE.match(n.note):
            if water_bowl is None:
                water_bowl = Container.objects.create(household=home, name="Water bowl", kind="water_bowl")
                water_bowl.pets.set(pets.values())
            Fill.objects.create(container=water_bowl, time=n.time, contents="water", notes=n.note)
        else:
            Note.objects.create(household=home, pet=pets.get(n.child_id), time=n.time, text=n.note)

    for kind, model, field in (("weight", "Weight", "weight"), ("height", "Height", "height")):
        for m in core[model].objects.all():
            Measurement.objects.create(
                pet=pets[m.child_id], date=m.date, kind=kind, value=getattr(m, field), notes=m.notes or "",
            )

    for m in core["Medication"].objects.all():
        dose = f"{m.dosage:g} {m.dosage_unit or ''}".strip() if m.dosage is not None else ""
        Medication.objects.create(
            pet=pets[m.child_id], time=m.time, name=m.name, dose=dose,
            next_dose_interval=m.next_dose_interval, notes=m.notes or "",
        )

    # Running walk/sleep timers become walks and sleeps in progress.
    for timer in core["Timer"].objects.filter(name__in=("Walk", "Sleep"), child__isnull=False):
        if timer.name == "Walk":
            walk = Walk.objects.create(household=home, start=timer.start, walker=timer.user)
            walk.pets.set([pets[timer.child_id]])
        else:
            Sleep.objects.create(pet=pets[timer.child_id], start=timer.start)


class Migration(migrations.Migration):
    dependencies = [
        ("pets", "0001_initial"),
        ("core", "0038_tummytime_notes"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(migrate, migrations.RunPython.noop)]
