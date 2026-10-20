"""Demo data (PUP-40): a household with a puppy and a cat and two weeks of believable entries.

    python manage.py housetrained_demo            # adds a "Demo home" for the first admin
    python manage.py housetrained_demo --user bob # ... for another user
    python manage.py housetrained_demo --delete   # removes the demo household again
"""
import datetime
import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from pets.models import Command as TrainingCommand
from pets.models import (
    Container, Fill, Household, Meal, Measurement, Membership, Note, Pet, PottyBreak, Product, Sleep, SupplyItem,
    TrainingLog, Walk,
)

NAME = "Demo home"


class Command(BaseCommand):
    help = "Creates a demo household (a puppy, a cat, two weeks of entries) for trying Housetrained out."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Username that owns the demo household (default: the first admin).")
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--delete", action="store_true", help="Remove the demo household instead.")

    def handle(self, *args, user=None, days=14, delete=False, **options):
        users = get_user_model().objects
        owner = users.filter(username=user).first() if user else users.filter(is_superuser=True).order_by("pk").first()
        if not owner:
            raise CommandError("No such user." if user else "Create an admin user first.")
        if delete:
            count, _ = Household.objects.filter(name=NAME, memberships__user=owner).delete()
            self.stdout.write(f"Removed the demo household ({count} records).")
            return
        if Household.objects.filter(name=NAME, memberships__user=owner).exists():
            raise CommandError(f"{owner} already has a demo household; use --delete first.")
        with transaction.atomic():
            self.build(owner, days, random.Random(42))
        self.stdout.write(self.style.SUCCESS(
            f"Created '{NAME}' for {owner} with a puppy (Biscuit) and a cat (Miso). "
            "It's that user's first household only if they had none; remove it with --delete."))

    def build(self, owner, days, rnd):
        now = timezone.localtime()
        today = now.date()
        home = Household.objects.create(name=NAME, units=Household.METRIC)
        Membership.objects.create(household=home, user=owner, role=Membership.OWNER)
        puppy = Pet.objects.create(household=home, name="Biscuit", type=Pet.DOG, breed="Labrador",
                                   birth_date=today - datetime.timedelta(weeks=12))
        cat = Pet.objects.create(household=home, name="Miso", type=Pet.CAT,
                                 birth_date=today - datetime.timedelta(days=3 * 365))
        kibble = Product.objects.create(household=home, name="Puppy kibble", brand="Demo", category=Product.DRY_FOOD,
                                        package_size=6800, reorder_days=7)
        cat_food = Product.objects.create(household=home, name="Cat biscuits", category=Product.DRY_FOOD,
                                          package_size=2000)
        litter = Product.objects.create(household=home, name="Clumping litter", category=Product.LITTER,
                                        package_size=10000)
        opened = today - datetime.timedelta(days=days)
        kibble_bag = SupplyItem.objects.create(product=kibble, size=6800, opened=opened, price=45)
        cat_bag = SupplyItem.objects.create(product=cat_food, size=2000, opened=opened)
        litter_bag = SupplyItem.objects.create(product=litter, size=10000, opened=opened)
        kibble_bag.pets.set([puppy]), cat_bag.pets.set([cat]), litter_bag.pets.set([cat])
        bowl = Container.objects.create(household=home, name="Biscuit's bowl", kind=Container.FOOD_BOWL,
                                        amount_per_bowl=120, default_supply=kibble_bag)
        feeder = Container.objects.create(household=home, name="Cat feeder", kind=Container.FEEDER,
                                          default_supply=cat_bag)
        water = Container.objects.create(household=home, name="Water bowl", kind=Container.WATER_BOWL,
                                         amount_per_bowl=500)
        box = Container.objects.create(household=home, name="Litter box", kind=Container.LITTER_BOX,
                                       default_supply=litter_bag)
        bowl.pets.set([puppy]), feeder.pets.set([cat]), water.pets.set([puppy, cat]), box.pets.set([cat])

        def at(day, hour, minute=0):
            moment = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))
            return moment if moment <= now else None

        for back in range(days, -1, -1):
            day = today - datetime.timedelta(days=back)
            accident_chance = 0.25 * back / days + 0.05  # fewer accidents as training sticks
            # The puppy: three meals, walks every ~3 hours, a nap after lunch.
            for hour in (7, 12, 17):
                when = at(day, hour, rnd.randint(0, 20))
                if when:
                    fill = Fill.objects.create(container=bowl, time=when, contents=Fill.FOOD, amount=60, bowls=0.5,
                                               supply_item=kibble_bag, logged_by=owner)
                    Meal.objects.create(pet=puppy, time=when, amount=60, product=kibble, fill=fill, logged_by=owner)
            for hour in (6, 9, 12, 15, 18, 21):
                start = at(day, hour, rnd.randint(0, 40))
                if not start:
                    continue
                end = start + datetime.timedelta(minutes=rnd.randint(10, 30))
                walk = Walk.objects.create(household=home, start=start, end=min(end, now), walker=owner,
                                           logged_by=owner, distance=rnd.choice([None, 600, 900, 1200]))
                walk.pets.set([puppy])
                PottyBreak.objects.create(pet=puppy, time=start + datetime.timedelta(minutes=5), pee=True,
                                          poop=hour in (6, 12, 18), walk=walk, logged_by=owner)
            if rnd.random() < accident_chance:
                when = at(day, rnd.choice([10, 14, 20]), rnd.randint(0, 50))
                if when:
                    PottyBreak.objects.create(pet=puppy, time=when, pee=True, poop=rnd.random() < 0.3,
                                              location=PottyBreak.INSIDE, logged_by=owner)
            nap = at(day, 13, 15)
            if nap:
                Sleep.objects.create(pet=puppy, start=nap, end=min(nap + datetime.timedelta(minutes=75), now),
                                     kind=Sleep.NAP, logged_by=owner)
            # The cat grazes: the feeder gets topped up, the litter box gets used and refreshed.
            when = at(day, 8)
            if when:
                Fill.objects.create(container=feeder, time=when, contents=Fill.FOOD, amount=50,
                                    supply_item=cat_bag, logged_by=owner)
                Fill.objects.create(container=water, time=when, contents=Fill.WATER, amount=500, bowls=1,
                                    logged_by=owner)
            for hour in (9, 16, 23):
                when = at(day, hour, rnd.randint(0, 50))
                if when:
                    PottyBreak.objects.create(pet=cat, time=when, pee=True, poop=hour == 16,
                                              location=PottyBreak.LITTER_BOX, container=box, logged_by=owner)
            if back % 3 == 0 and at(day, 19):
                Fill.objects.create(container=box, time=at(day, 19), contents=Fill.LITTER, amount=900,
                                    supply_item=litter_bag, logged_by=owner)
            if back % 7 == 0:
                Measurement.objects.create(pet=puppy, date=day, kind=Measurement.WEIGHT,
                                           value=round(9 + (days - back) * 0.12, 1), logged_by=owner)
        Note.objects.create(household=home, pet=puppy, time=now - datetime.timedelta(days=2), logged_by=owner,
                            text="Vet check: all good. Next vaccines in 4 weeks.")
        # Training (PUP-150): a few commands getting better over the two weeks, a few tries a day.
        for name, how, when, start, status in (
                ("Sit", "Treat over the nose, then up and back. Say it once.", "Before meals, at the door", 3,
                 TrainingCommand.KNOWN),
                ("Down", "From a sit, treat to the floor between the paws.", "Settling in a cafe or at the vet", 2,
                 TrainingCommand.WORKING),
                ("Leave it", "Closed fist with a treat; reward from the other hand.", "Dropped food, things on walks",
                 1, TrainingCommand.WORKING),
                ("Come", "Happy voice, run backwards, big reward.", "In the garden, off the lead later", 1,
                 TrainingCommand.WORKING)):
            command = TrainingCommand.objects.create(pet=puppy, name=name, how_to=how, when_to_use=when, status=status)
            for back in range(days - 1, -1, -1):
                level = min(5, start + (days - back) * (5 - start) / days)
                for hour in rnd.sample([9, 12, 15, 18], 2):
                    when_at = at(today - datetime.timedelta(days=back), hour, rnd.randint(0, 50))
                    if when_at:
                        TrainingLog.objects.create(command=command, time=when_at, logged_by=owner,
                                                   score=max(1, min(5, round(level + rnd.choice((-1, 0, 0, 1))))))
