"""Housetrained pet data model. See docs/housetrained/data-model.md.

Amounts are stored in canonical units: grams for food and litter, millilitres for water, kilograms for weight and
centimetres for lengths. Conversion for entry and display happens outside the models.
"""
import datetime

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
    water_cutoff_hours = models.PositiveSmallIntegerField(
        _("pick up water before bed"), default=2,
        help_text=_("Hours before the usual bedtime to pick up the water bowl (for the water reminder)."),
    )
    minutes_to_outside = models.PositiveSmallIntegerField(
        _("time to get outside"), default=10,
        help_text=_("Minutes from deciding to go to standing outside (leash, shoes, stairs or elevator). "
                    "Next Walk tells you to head out this much before the pet is expected to need to go."),
    )
    bedtime = models.TimeField(
        _("bedtime"), default=datetime.time(23, 0),
        help_text=_("Quiet hours start: after the bedtime walk, the next one is at wake-up."))
    wake_time = models.TimeField(_("wake-up"), default=datetime.time(6, 30), help_text=_("Quiet hours end."))
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
    picture_crop = models.JSONField(
        null=True, blank=True, editable=False,
        help_text="Part of the picture to show, as fractions of its size: {x, y, w, h}.",
    )
    picture_cropped = models.ImageField(upload_to="pets/cropped/", blank=True, editable=False)
    active = models.BooleanField(_("active"), default=True, help_text=_("Untick once a pet is rehomed or has passed."))
    notes = models.TextField(_("notes"), blank=True)
    retry_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="Taken out but nothing happened: when to try again (cleared by the next potty break).",
    )
    triggers_cleared_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="Disregard on the Now card: meals and wake-ups up to this time no longer drive Next Walk.",
    )
    # The schedule (PUP-93): set by the people, suggested by the coach. Blank pee every: from the pet's age.
    EVERY = [(m, f'{m // 60}h {m % 60:02d}m' if m % 60 and m >= 60 else (f'{m // 60}h' if m >= 60 else f'{m}m')) for m in range(30, 12 * 60 + 1, 15)]
    pee_every = models.PositiveSmallIntegerField(
        _("pee every"), null=True, blank=True, choices=EVERY,
        help_text=_("Head out this long after the last pee. Blank: from age (age in months + 1 hour)."))
    poop_every = models.PositiveSmallIntegerField(
        _("poop every"), null=True, blank=True, choices=EVERY,
        help_text=_("Expect a poop this long after the last one. Blank: twice pee every."))
    after_meal = models.PositiveSmallIntegerField(
        _("out after a meal"), default=15, help_text=_("Minutes after eating or drinking to go out."))
    schedule_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
    coach_snoozed_until = models.DateTimeField(null=True, blank=True, editable=False)
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

    @property
    def card_picture(self):
        """The cropped square if the picture was cropped, else the picture itself."""
        return self.picture_cropped or self.picture

    def make_cropped_picture(self):
        """Save the cropped square of the picture (the original is kept, so it can be cropped again)."""
        import io

        from django.core.files.base import ContentFile
        from PIL import Image, ImageOps

        if self.picture_cropped:
            self.picture_cropped.delete(save=False)
        if not self.picture or not self.picture_crop:
            return
        crop = self.picture_crop
        with self.picture.open("rb") as handle:
            image = ImageOps.exif_transpose(Image.open(handle))
            image.load()
        width, height = image.size
        box = (
            max(0, round(crop["x"] * width)), max(0, round(crop["y"] * height)),
            min(width, round((crop["x"] + crop["w"]) * width)), min(height, round((crop["y"] + crop["h"]) * height)),
        )
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            return
        cropped = image.crop(box).convert("RGB")
        cropped.thumbnail((800, 800))
        out = io.BytesIO()
        cropped.save(out, "JPEG", quality=88)
        self.picture_cropped.save(f"{self.slug or 'pet'}-{self.pk or 'new'}.jpg", ContentFile(out.getvalue()), save=False)

    def allowed_potty_locations(self):
        """The pet's chosen potty locations, or sensible ones for its type."""
        if self.potty_locations:
            return list(self.potty_locations)
        return {
            self.DOG: [PottyBreak.OUTSIDE, PottyBreak.INSIDE, PottyBreak.PAD],
            self.CAT: [PottyBreak.LITTER_BOX, PottyBreak.INSIDE],
        }.get(self.type, [code for code, _label in PottyBreak.LOCATIONS])

    @property
    def pee_minutes(self):
        from .schedule import age_rule

        return self.pee_every or age_rule(self.birth_date)

    @property
    def poop_minutes(self):
        return self.poop_every or min(self.pee_minutes * 2, 12 * 60)

    def save(self, *args, **kwargs):
        if not self.pk and self.pee_every is None:
            from .schedule import age_rule

            self.pee_every = age_rule(self.birth_date)  # prefilled once; the coach suggests changes from there
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


class PottyBreakManager(models.Manager):
    """Real potty breaks only: "nothing" entries (no pee, no poop) never count towards stats or Next Walk."""

    def get_queryset(self):
        return super().get_queryset().exclude(pee=False, poop=False)


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
    # Accident context (PUP-76): where in the house, and what was going on.
    UNSUPERVISED, AFTER_PLAY, AFTER_DRINKING, EXCITED, AFTER_CRATE = (
        "unsupervised", "after_play", "after_drinking", "excited", "after_crate")
    CONTEXTS = [(UNSUPERVISED, _("Unsupervised")), (AFTER_PLAY, _("After play")),
                (AFTER_DRINKING, _("After drinking")), (EXCITED, _("Excited / visitors")),
                (AFTER_CRATE, _("After the crate"))]
    spot = models.ForeignKey("AccidentSpot", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="accidents", verbose_name=_("where"))
    context = models.JSONField(_("what was going on"), default=list, blank=True)
    # Health (PUP-79): blank means normal.
    LESS, MORE = "less", "more"
    AMOUNTS = [("", _("Normal")), (LESS, _("Less than normal")), (MORE, _("More than normal"))]
    PEE_COLOURS = [("", _("Normal")), ("light", _("Too light")), ("dark", _("Too dark"))]
    CONSISTENCIES = [("", _("Just right")), ("hard", _("Too hard")), ("soft", _("Too soft"))]
    POOP_COLOURS = [("", _("Brown")), ("black", _("Black")), ("red", _("Red / bloody")), ("green", _("Green")),
                    ("yellow", _("Yellow")), ("grey", _("Grey / white")), ("other", _("Other"))]
    pee_amount = models.CharField(_("pee amount"), max_length=10, blank=True, choices=AMOUNTS)
    pee_colour = models.CharField(_("pee colour"), max_length=10, blank=True, choices=PEE_COLOURS)
    poop_amount = models.CharField(_("poop amount"), max_length=10, blank=True, choices=AMOUNTS)
    poop_consistency = models.CharField(_("poop consistency"), max_length=10, blank=True, choices=CONSISTENCIES)
    poop_colour = models.CharField(_("poop colour"), max_length=10, blank=True, choices=POOP_COLOURS)
    found_later = models.BooleanField(
        _("found it later"), default=False,
        help_text=_("Didn't see it happen, so the time is only when it was found. Next Walk doesn't count it."),
    )
    container = models.ForeignKey(
        Container, on_delete=models.SET_NULL, null=True, blank=True, related_name="potty_breaks",
        help_text=_("Which litter box, e.g. the litter robot."),
    )

    objects = PottyBreakManager()
    everything = models.Manager()  # including "nothing" entries: the log list, recent activity, editing

    class Meta:
        ordering = ["-time"]
        indexes = [models.Index(fields=["pet", "time"])]
        base_manager_name = "everything"

    def __str__(self):
        return f"{self.pet} potty break at {self.time:%Y-%m-%d %H:%M}"

    @property
    def nothing(self):
        return not self.pee and not self.poop


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
    """A pet ate and/or drank. Next Walk goes out a set time after it. Separate from filling a bowl (a Fill),
    which is what left the bag."""

    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name="meals")
    time = models.DateTimeField(_("time"), default=timezone.now)
    food = models.BooleanField(_("food"), default=True)
    water = models.BooleanField(_("water"), default=False)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    PORTIONS = [(0.25, "¼"), (0.5, "½"), (0.75, "¾"), (1.0, _("All"))]
    portion = models.FloatField(_("how much of the bowl"), null=True, blank=True, choices=PORTIONS)
    water_portion = models.FloatField(_("how much of the bowl"), null=True, blank=True, choices=PORTIONS)
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


# --- Babies (Baby Buddy's children) ------------------------------------------------------------
class BabyHousehold(models.Model):
    """Which household a Baby Buddy child belongs to (PUP-71). Children without one are hidden from everyone
    (e.g. pets that came over from a Baby Buddy install as children)."""

    child = models.OneToOneField("core.Child", on_delete=models.CASCADE, related_name="household_link")
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="babies")

    def __str__(self):
        return f"{self.child} in {self.household}"


# --- Notifications (PUP-58) ----------------------------------------------------------------
class PushKey(models.Model):
    """The server's VAPID key pair for browser push, made on first use."""

    public_key = models.TextField()
    private_key = models.TextField()
    created = models.DateTimeField(auto_now_add=True)


class PushSubscription(models.Model):
    """One browser or phone that receives a user's notifications."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    device = models.CharField(_("device"), max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} on {self.device or 'a device'}"


class NotificationPreference(models.Model):
    """Which reminders a person wants."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    walk_due = models.BooleanField(_("Walk due"), default=True,
                                   help_text=_("When a pet's next walk is due, from its schedule."))
    walk_lead_minutes = models.PositiveSmallIntegerField(
        _("minutes before"), default=5, help_text=_("How long before the head-out time the walk reminder comes."))
    low_supply = models.BooleanField(_("Buy more"), default=True,
                                     help_text=_("When a bag is down to its reorder days (once a day)."))
    water_cutoff = models.BooleanField(_("Water bowl"), default=False,
                                       help_text=_("Pick it up before bedtime, put it back in the morning."))
    weekly_progress = models.BooleanField(_("Weekly progress"), default=False,
                                          help_text=_("Monday morning: accidents this week against the week before."))


class ReminderLog(models.Model):
    """Reminders already sent, so each goes out once even with several server processes checking."""

    key = models.CharField(max_length=255, unique=True)
    created = models.DateTimeField(auto_now_add=True)


class AccidentSpot(models.Model):
    """A place in the house where accidents happen (named once in Setup)."""

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="accident_spots")
    name = models.CharField(_("name"), max_length=100)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
