"""Housetrained pet data model. See docs/housetrained/data-model.md.

Amounts are stored in canonical units: grams for food and litter, millilitres for water, kilograms for weight and
centimetres for lengths. Conversion for entry and display happens outside the models.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .site_settings import PlatformSettings


# --- Households -------------------------------------------------------------------
class Household(models.Model):
    METRIC, IMPERIAL = "metric", "imperial"

    name = models.CharField(_("name"), max_length=255)
    units = models.CharField(
        _("display units"), max_length=10, choices=[(METRIC, _("Metric")), (IMPERIAL, _("Imperial"))], default=METRIC
    )
    time_zone = models.CharField(_("default time zone"), max_length=100, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="households")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    OWNER, MEMBER, VIEWER = "owner", "member", "viewer"

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(
        _("role"), max_length=10, default=MEMBER,
        choices=[(OWNER, _("Owner")), (MEMBER, _("Member")), (VIEWER, _("Read-only"))],
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["household", "user"], name="unique_membership")]

    def __str__(self):
        return f"{self.user} in {self.household}"


# --- Pets ---------------------------------------------------------------------------
class Pet(models.Model):
    DOG, CAT, OTHER = "dog", "cat", "other"

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="pets")
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(max_length=100, editable=False)
    type = models.CharField(_("type"), max_length=10, choices=[(DOG, _("Dog")), (CAT, _("Cat")), (OTHER, _("Other"))])
    breed = models.CharField(_("breed"), max_length=255, blank=True)
    sex = models.CharField(
        _("sex"), max_length=10, blank=True, choices=[("female", _("Female")), ("male", _("Male"))]
    )
    birth_date = models.DateField(_("birth date"), null=True, blank=True)
    birth_date_estimated = models.BooleanField(_("birth date is an estimate"), default=False)
    picture = models.ImageField(_("picture"), upload_to="pets/", blank=True)
    active = models.BooleanField(_("active"), default=True, help_text=_("Untick once a pet is rehomed or has passed."))
    notes = models.TextField(_("notes"), blank=True)
    potty_locations = models.JSONField(
        _("potty locations"), default=list, blank=True,
        help_text=_("Where this pet goes. Only these show when logging a potty break."),
    )

    site_settings = PlatformSettings()  # site-wide switches, on the Site Settings page

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["household", "slug"], name="unique_pet_slug")]

    def __str__(self):
        return self.name

    def allowed_potty_locations(self):
        """The pet's chosen potty locations, or sensible ones for its type."""
        if self.potty_locations:
            return list(self.potty_locations)
        return {
            self.DOG: [PottyBreak.OUTSIDE, PottyBreak.INSIDE, PottyBreak.PAD],
            self.CAT: [PottyBreak.LITTER_BOX, PottyBreak.INSIDE],
        }.get(self.type, [code for code, _label in PottyBreak.LOCATIONS])

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "pet"
            slug, n = base, 2
            while Pet.objects.filter(household=self.household, slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Logged(models.Model):
    """Who logged an entry, plus free-text notes."""

    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(_("notes"), blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


# --- Containers, products and supplies ------------------------------------------------------
class Container(models.Model):
    FOOD_BOWL, FEEDER = "food_bowl", "feeder"
    WATER_BOWL, FOUNTAIN = "water_bowl", "fountain"
    LITTER_BOX, AUTO_LITTER_BOX = "litter_box", "auto_litter_box"
    KINDS = [
        (FOOD_BOWL, _("Food bowl")),
        (FEEDER, _("Automatic feeder")),
        (WATER_BOWL, _("Water bowl")),
        (FOUNTAIN, _("Water fountain")),
        (LITTER_BOX, _("Litter box")),
        (AUTO_LITTER_BOX, _("Automatic litter box")),
    ]

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="containers")
    name = models.CharField(_("name"), max_length=255)
    kind = models.CharField(_("kind"), max_length=20, choices=KINDS)
    pets = models.ManyToManyField(Pet, related_name="containers", blank=True, verbose_name=_("used by"))
    default_supply = models.ForeignKey(
        "SupplyItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name=_("default supply"),
    )
    amount_per_bowl = models.FloatField(
        _("amount per bowl"), null=True, blank=True,
        help_text=_("Grams (or millilitres for water) in one full bowl. Set by calibrating: tare, fill, weigh."),
    )
    active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]

    @property
    def holds_water(self):
        return self.kind in (self.WATER_BOWL, self.FOUNTAIN)

    def __str__(self):
        return self.name


class Product(models.Model):
    DRY_FOOD, WET_FOOD, TREATS, LITTER, OTHER = "dry_food", "wet_food", "treats", "litter", "other"

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(_("name"), max_length=255)
    brand = models.CharField(_("brand"), max_length=255, blank=True)
    category = models.CharField(
        _("category"), max_length=20,
        choices=[(DRY_FOOD, _("Dry food")), (WET_FOOD, _("Wet food")), (TREATS, _("Treats")),
                 (LITTER, _("Litter")), (OTHER, _("Other"))],
    )
    package_size = models.FloatField(_("usual package size (g)"), null=True, blank=True)
    reorder_days = models.PositiveIntegerField(
        _("remind when this many days are left"), default=7
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.brand} {self.name}".strip()


class SupplyItem(models.Model):
    """One physical bag or box of a product."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="items")
    size = models.FloatField(_("size (g)"))
    opened = models.DateField(_("opened"), null=True, blank=True)
    finished = models.DateField(_("finished"), null=True, blank=True)
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, null=True, blank=True)
    pets = models.ManyToManyField(Pet, related_name="supplies", blank=True, verbose_name=_("shared by"))

    class Meta:
        ordering = ["-opened"]

    def __str__(self):
        return f"{self.product} ({self.opened or 'unopened'})"


class Fill(Logged):
    """Food, water or litter put into a container, optionally drawn from a supply item."""

    FOOD, WATER, LITTER = "food", "water", "litter"

    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name="fills")
    time = models.DateTimeField(_("time"), default=timezone.now)
    contents = models.CharField(
        _("contents"), max_length=10, choices=[(FOOD, _("Food")), (WATER, _("Water")), (LITTER, _("Litter"))]
    )
    amount = models.FloatField(
        _("amount"), null=True, blank=True,
        help_text=_("Grams (millilitres for water). Empty when entered in bowls on an uncalibrated container."),
    )
    bowls = models.FloatField(_("bowls"), null=True, blank=True, help_text=_("Set when the fill was entered in bowls."))
    supply_item = models.ForeignKey(
        SupplyItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="fills"
    )

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["container", "time"])]

    def __str__(self):
        return f"{self.get_contents_display()} into {self.container}"


# --- Activities ---------------------------------------------------------------------------------
class Walk(Logged):
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="walks")
    pets = models.ManyToManyField(Pet, related_name="walks")
    start = models.DateTimeField(_("start"), default=timezone.now)
    end = models.DateTimeField(_("end"), null=True, blank=True, help_text=_("Empty while the walk is in progress."))
    walker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    walker_name = models.CharField(_("walker name"), max_length=255, blank=True, help_text=_("For walkers without an account."))
    distance = models.FloatField(_("distance (m)"), null=True, blank=True)

    class Meta:
        ordering = ["-start"]
        indexes = [models.Index(fields=["household", "start"])]

    def __str__(self):
        return f"Walk at {self.start:%Y-%m-%d %H:%M}"


class PottyBreak(Logged):
    OUTSIDE, INSIDE, PAD, LITTER_BOX = "outside", "inside", "pad", "litter_box"
    LOCATIONS = [(OUTSIDE, _("Outside")), (INSIDE, _("Inside")), (PAD, _("Pad")), (LITTER_BOX, _("Litter box"))]

    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="potty_breaks")
    time = models.DateTimeField(_("time"), default=timezone.now)
    pee = models.BooleanField(_("pee"), default=False)
    poop = models.BooleanField(_("poop"), default=False)
    location = models.CharField(
        _("location"), max_length=20, default=OUTSIDE,
        choices=LOCATIONS,
    )
    walk = models.ForeignKey(Walk, on_delete=models.SET_NULL, null=True, blank=True, related_name="potty_breaks")
    container = models.ForeignKey(
        Container, on_delete=models.SET_NULL, null=True, blank=True, related_name="potty_breaks",
        help_text=_("Which litter box, e.g. the litter robot."),
    )

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["pet", "time"])]

    def __str__(self):
        return f"{self.pet} potty break at {self.time:%Y-%m-%d %H:%M}"


class Sleep(Logged):
    NAP, NIGHT = "nap", "night"

    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="sleeps")
    start = models.DateTimeField(_("start"), default=timezone.now)
    end = models.DateTimeField(_("end"), null=True, blank=True, help_text=_("Empty while asleep."))
    kind = models.CharField(_("type"), max_length=10, default=NAP, choices=[(NAP, _("Nap")), (NIGHT, _("Night"))])

    class Meta:
        ordering = ["-start"]
        indexes = [models.Index(fields=["pet", "start"])]

    def __str__(self):
        return f"{self.pet} {self.get_kind_display().lower()} at {self.start:%Y-%m-%d %H:%M}"


class Meal(Logged):
    """A pet ate and/or drank. What it takes in drives potty predictions. Separate from filling a bowl (a Fill),
    which is what left the bag."""

    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="meals")
    time = models.DateTimeField(_("time"), default=timezone.now)
    food = models.BooleanField(_("food"), default=True)
    water = models.BooleanField(_("water"), default=False)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    amount = models.FloatField(_("amount eaten (g)"), null=True, blank=True)
    water_amount = models.FloatField(_("amount drunk (ml)"), null=True, blank=True)
    fill = models.ForeignKey(
        "Fill", on_delete=models.CASCADE, null=True, blank=True, related_name="meals",
        help_text=_("Set when the meal was logged together with filling the bowl; goes away with that fill."),
    )

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["pet", "time"])]

    def __str__(self):
        return f"{self.pet} ate at {self.time:%Y-%m-%d %H:%M}"


class Measurement(Logged):
    WEIGHT, HEIGHT, LENGTH = "weight", "height", "length"
    UNITS = {WEIGHT: "kg", HEIGHT: "cm", LENGTH: "cm"}

    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="measurements")
    date = models.DateField(_("date"), default=timezone.localdate)
    kind = models.CharField(
        _("kind"), max_length=20, choices=[(WEIGHT, _("Weight")), (HEIGHT, _("Height")), (LENGTH, _("Length"))]
    )
    value = models.FloatField(_("value"), help_text=_("Kilograms for weight, centimetres for height and length."))

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["pet", "kind", "date"])]

    def __str__(self):
        return f"{self.pet} {self.kind} {self.value:g} {self.UNITS.get(self.kind, '')}"


class Medication(Logged):
    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="medications")
    time = models.DateTimeField(_("time"), default=timezone.now)
    name = models.CharField(_("name"), max_length=255)
    dose = models.CharField(_("dose"), max_length=255, blank=True)
    next_dose_interval = models.DurationField(_("next dose in"), null=True, blank=True)

    class Meta:
        ordering = ["-time"]

    def __str__(self):
        return f"{self.pet} {self.name}"


class Note(models.Model):
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="notes")
    pet = models.ForeignKey(
        Pet, on_delete=models.CASCADE, null=True, blank=True, related_name="pet_notes",
        help_text=_("Empty for a household note."),
    )
    time = models.DateTimeField(_("time"), default=timezone.now)
    text = models.TextField(_("note"))
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-time"]

    def __str__(self):
        return self.text[:50]
