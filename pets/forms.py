import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from housetrained.widgets import DateInput, DateTimeInput, TimeInput

from . import units
from .models import (
    AccidentContext, AccidentSpot, Command, Container, Fill, Household, Meal, Measurement, Note, Pet, PottyBreak,
    PottyLocation, Product, Puke, Sleep, SupplyItem, TrainingLog, Walk, accident_locations, location_choices,
    location_is_accident,
)
from .widgets import (
    DurationSelect, FoodPicker, PetCheckboxSelect, PetRadioSelect, PictureInput, PillCheckboxSelect, PillRadioSelect,
)


class PetsForm(forms.ModelForm):
    """Scopes choices to the household and renders with the shared fieldset layouts."""

    fieldsets = []
    # Fields that only apply after a choice: {field: (controlling field, values that show it)}. ANY means "has a
    # value". In the page a hidden field is disabled, so it keeps what was typed if it comes back but isn't sent;
    # clean() also empties it, so it's never saved.
    show_when = {}
    ANY = "*"

    def __init__(self, *args, household, user, **kwargs):
        self.household, self.user = household, user
        super().__init__(*args, **kwargs)
        self.show_when = dict(self.show_when)
        for field in self.fields.values():
            model = getattr(getattr(field, "queryset", None), "model", None)
            if model is Pet:
                field.queryset = Pet.objects.filter(household=household, active=True)
            elif model is Container:
                field.queryset = Container.objects.filter(household=household, active=True)
            elif model is Product:
                field.queryset = Product.objects.filter(household=household)
            elif model is SupplyItem:
                field.queryset = SupplyItem.objects.filter(product__household=household, finished=None)
            elif model is get_user_model():
                field.queryset = household.members.all()
        if not self.instance.pk and "pets" in self.fields:
            self.fields.pop("pet", None)
            self.fieldsets = [{**fs, "fields": [f for f in fs["fields"] if f != "pet"]} for fs in self.fieldsets]

    @property
    def hydrated_fielsets(self):
        bound = {field.name: field for field in self}
        return [
            {
                "layout": "default" if fs.get("layout") == "advanced" else fs.get("layout", "default"),
                "layout_attrs": fs.get("layout_attrs", {}),
                "fields": [bound[name] for name in fs["fields"] if name in bound and not bound[name].is_hidden],
            }
            for fs in self.fieldsets
        ]

    def default_unit(self, kind):
        return units.unit_for(self.household, kind)

    @staticmethod
    def _as_text(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(getattr(value, "pk", value))

    @staticmethod
    def _conditions(rule):
        """A rule is (control, values), or a list of them that must all hold."""
        return rule if isinstance(rule, list) else [rule]

    def _shown(self, name, data):
        for control, values in self._conditions(self.show_when[name]):
            value = data.get(control)
            if values == self.ANY:
                if value in (None, "", [], False):
                    return False
            elif self._as_text(value) not in {self._as_text(v) for v in values}:
                return False
        return True

    def active_show_when(self):
        return {name: rule for name, rule in self.show_when.items()
                if name in self.fields and all(control in self.fields for control, _v in self._conditions(rule))}

    # Unit pickers that sit inside their amount box instead of on a row of their own.
    UNIT_OF = {"unit": "amount", "food_unit": "food_added", "water_unit": "water_added", "distance_unit": "distance",
               "weight_unit": "value",
               "length_unit": "value", "bowl_unit": "amount_per_bowl", "new_food_size_unit": "new_food_size",
               "low_unit": "low_at", "food_unit_second": "food_added_second", "new_food_size_unit_second": "new_food_size_second"}

    @property
    def unit_pairs_json(self):
        pairs = {}
        for unit, amount in self.UNIT_OF.items():
            if unit in self.fields and amount in self.fields:
                pairs.setdefault(amount, []).append(unit)
        for name in ("package_size", "size"):
            if name in self.fields and "size_unit" in self.fields:
                pairs.setdefault(name, []).append("size_unit")
        return pairs

    @property
    def show_when_json(self):
        return {name: [[control, values if values == self.ANY else [self._as_text(v) for v in values]]
                       for control, values in self._conditions(rule)]
                for name, rule in self.active_show_when().items()}

    def full_clean(self):
        for name in self.active_show_when():
            self.fields[name].required = False
        super().full_clean()

    def clean(self):
        data = super().clean()
        for name in self.active_show_when():
            if not self._shown(name, data):
                field = self.fields[name]
                if isinstance(field, (forms.ModelMultipleChoiceField, forms.MultipleChoiceField)):
                    data[name] = []
                elif isinstance(field, forms.ModelChoiceField):
                    data[name] = None
                elif isinstance(field, forms.BooleanField):
                    data[name] = False
                elif isinstance(field, forms.ChoiceField):
                    data[name] = ""
                else:
                    data[name] = getattr(field, "empty_value", None)
        return data


class PerPetForm(PetsForm):
    """New entries can be logged for several pets at once; editing works on one pet."""

    pets = forms.ModelMultipleChoiceField(queryset=Pet.objects.none(), widget=PetCheckboxSelect, label=_("Pets"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields.pop("pets")
        else:
            self.fields["pets"].queryset = Pet.objects.filter(household=self.household, active=True)

    def save_entries(self):
        if self.instance.pk:
            return [self.save()]
        entries = []
        for pet in self.cleaned_data["pets"]:
            entry = self.save(commit=False)
            entry.pk = None
            entry._state.adding = True
            entry.pet = pet
            entry.logged_by = self.user
            self.before_save(entry)
            entry.save()
            entries.append(entry)
        return entries

    def before_save(self, entry):
        pass


HEALTH_FIELDS = ["pee_amount", "pee_colour", "poop_amount", "poop_consistency", "poop_colour"]


class WhereForm(PerPetForm):
    """The rows a potty break and a puke share (PUP-182): the location (the household's own too, and "+ New"), then
    for an accident location Found it later, Where and What was going on (each with "+ New", PUP-180)."""

    # Typed into a "+ New" pill (PUP-180): added to the household's choices when the entry is saved.
    spot_new = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput)
    context_new = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput)
    show_when = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_when = dict(self.show_when)  # this form's own: it depends on the household
        self.fields["spot"].queryset = AccidentSpot.objects.filter(household=self.household)
        self.fields["spot"].widget = PillRadioSelect(choices=self.fields["spot"].choices, addable="spot_new")
        self.fields["spot"].empty_label = None
        self.fields["context"] = forms.MultipleChoiceField(
            label=_("What was going on"), choices=context_choices(self.household), required=False,
            widget=PillCheckboxSelect(addable="context_new"))
        if self.instance.pk:
            self.initial["context"] = self.instance.context
        # Only the locations the pets use (a dog never uses a litter box); the page script narrows it further to
        # the pets picked.
        pets = [self.instance.pet] if self.instance.pk else list(self.fields["pets"].queryset)
        self.location_map = {pet.pk: pet.allowed_potty_locations() for pet in pets}
        self.allowed_locations = {loc for locs in self.location_map.values() for loc in locs} | {self.instance.location}
        # The model keeps any code (the household's own locations); the form only takes this household's.
        self.fields["location"] = forms.ChoiceField(
            label=_("Location"),
            choices=[(c, l) for c, l in location_choices(self.household) if c in self.allowed_locations],
            widget=PillRadioSelect(addable="location_new"))  # the page asks: accident or right place?
        for name in ("found_later", "spot", "context"):
            self.show_when[name] = ("location", accident_locations(self.household))
        if not self.instance.pk and pets:
            self.initial.setdefault("location", self.location_map[pets[0].pk][0])

    def add_new_choices(self, data):
        """A "+ New" Where or What was going on: the household gets it once the rest of the entry is fine."""
        if not self.errors and location_is_accident(data.get("location")):
            if data.get("spot_new"):
                data["spot"] = new_spot(self.household, data["spot_new"])
            if data.get("context_new"):
                data["context"] = list(dict.fromkeys(data.get("context", []) + [
                    new_context(self.household, data["context_new"])]))


class PottyBreakForm(WhereForm):
    unusual = forms.BooleanField(label=_("Something unusual?"), required=False, help_text=_(
        "Amount, colour or consistency that isn't normal for this pet. Leave off when all looks fine."))
    show_when = {
        "container": ("location", [PottyBreak.LITTER_BOX]),
        "pee_amount": ("unusual", [True]), "pee_colour": ("unusual", [True]),
        "poop_amount": ("unusual", [True]), "poop_consistency": ("unusual", [True]),
        "poop_colour": ("unusual", [True]),
    }
    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["pee", "poop"], "layout": "choices", "layout_attrs": {"label": _("Contents")}},
        {"fields": ["location"], "layout": "required"},
        {"fields": ["found_later"], "layout": "choices", "layout_attrs": {"label": _("Timing")}},
        {"fields": ["spot", "context", "container"]},
        {"fields": ["unusual"], "layout": "choices", "layout_attrs": {"label": _("Health")}},
        {"fields": HEALTH_FIELDS + ["notes"]},
    ]

    class Meta:
        model = PottyBreak
        fields = ["pet", "time", "pee", "poop", "location", "found_later", "spot", "context", "container"] + \
            HEALTH_FIELDS + ["notes"]
        labels = {"found_later": _("Found it later")}
        widgets = {
            "pet": PetRadioSelect,
            "time": DateTimeInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"container": _("Litter box")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["container"].queryset = self.fields["container"].queryset.filter(
            kind__in=[Container.LITTER_BOX, Container.AUTO_LITTER_BOX]
        )
        if self.instance.pk:
            self.initial["unusual"] = any(getattr(self.instance, f) for f in HEALTH_FIELDS)
        for name in HEALTH_FIELDS:
            self.fields[name].widget = PillRadioSelect(choices=self.fields[name].choices)
        # Pee details only for pees, poop details only for poops.
        for kind, names in (("pee", ["pee_amount", "pee_colour"]),
                            ("poop", ["poop_amount", "poop_consistency", "poop_colour"])):
            for name in names:
                self.show_when[name] = [("unusual", [True]), (kind, [True])]
        self.health_kinds = {"pee": ["pee_amount", "pee_colour"],
                             "poop": ["poop_amount", "poop_consistency", "poop_colour"]}
        if PottyBreak.LITTER_BOX not in self.allowed_locations or not self.fields["container"].queryset.exists():
            self.fields.pop("container")

    def clean(self):
        data = super().clean()
        if not data.get("pee") and not data.get("poop") and not (self.instance.pk and self.instance.nothing):
            raise forms.ValidationError(_("Pick pee, poop or both."))
        # Details for what didn't happen aren't kept (a poop colour on a pee-only break).
        for kind, names in self.health_kinds.items():
            if not data.get(kind):
                for name in names:
                    data[name] = ""
        self.add_new_choices(data)
        return data

    def before_save(self, entry):
        link_walk(entry)

    def save(self, commit=True):
        entry = super().save(commit=False)
        if self.instance.pk:
            link_walk(entry)
        if commit:
            entry.save()
        return entry


class PukeForm(WhereForm):
    """Throwing up (PUP-182): the potty break's where and when, and what came up."""

    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["location"], "layout": "required"},
        {"fields": ["found_later"], "layout": "choices", "layout_attrs": {"label": _("Timing")}},
        {"fields": ["spot", "context"]},
        {"fields": ["contents", "notes"]},
    ]

    class Meta:
        model = Puke
        fields = ["pet", "time", "location", "found_later", "spot", "context", "contents", "notes"]
        labels = {"found_later": _("Found it later")}
        widgets = {"pet": PetRadioSelect, "time": DateTimeInput(), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contents"].widget = PillRadioSelect(choices=self.fields["contents"].choices)
        if not self.instance.pk and PottyBreak.INSIDE in self.allowed_locations:
            self.initial["location"] = PottyBreak.INSIDE  # mostly indoors

    def clean(self):
        data = super().clean()
        self.add_new_choices(data)
        return data


def context_choices(household):
    """What was going on: the built-in choices, then the household's own (stored by name)."""
    return PottyBreak.CONTEXTS + [(c.name, c.name) for c in AccidentContext.objects.filter(household=household)]


def new_spot(household, name):
    """The household's spot with that name (any capitalisation), made if it's new."""
    name = name.strip()
    return (AccidentSpot.objects.filter(household=household, name__iexact=name).first()
            or AccidentSpot.objects.create(household=household, name=name))


def new_context(household, name):
    """The value to store for a typed What was going on: a built-in one's code if it's that, else the household's own
    by name (made if it's new)."""
    name = name.strip()
    for code, label in PottyBreak.CONTEXTS:
        if name.lower() in (code.lower(), str(label).lower()):
            return code
    existing = AccidentContext.objects.filter(household=household, name__iexact=name).first()
    return (existing or AccidentContext.objects.create(household=household, name=name)).name


def _minute(dt):
    return dt.replace(second=0, microsecond=0)


def link_walk(entry):
    """An outside potty break that happened during a walk belongs to it. By the minute (PUP-188): a walk's form
    keeps whole minutes, so one ended at 6:17 still has the pee logged at 6:17:32."""
    if entry.location != PottyBreak.OUTSIDE:
        entry.walk = None
        return
    minute = _minute(entry.time)
    entry.walk = (
        Walk.objects.filter(pets=entry.pet, start__lt=minute + datetime.timedelta(minutes=1))
        .filter(Q(end__isnull=True) | Q(end__gte=minute))
        .order_by("-start")
        .first()
    )


def relink_walk(walk):
    """A walk logged or changed afterwards (back-dated, longer, other pets) takes in the potty breaks that now fall
    within it, and lets go of ones that no longer do (PUP-129). Same rule as link_walk."""
    end = _minute(walk.end or timezone.now()) + datetime.timedelta(minutes=1)
    nearby = PottyBreak.everything.filter(
        Q(walk=walk) | Q(pet__in=walk.pets.all(), time__gte=_minute(walk.start), time__lt=end)).distinct()
    for entry in nearby:
        before = entry.walk_id
        link_walk(entry)
        if entry.walk_id != before:
            PottyBreak.everything.filter(pk=entry.pk).update(walk=entry.walk)


OTHER_WALKER = "other"


def walk_company(pets, anchor):
    """Who can come along on a walk with `anchor`: the pets that walk together, unless one of them walks alone."""
    ids = [p.pk for p in anchor]
    if all(p.walks_together for p in anchor):
        return pets.filter(Q(walks_together=True) | Q(pk__in=ids))
    return pets.filter(pk__in=ids)


class WalkForm(PetsForm):
    walker_choice = forms.ChoiceField(label=_("Walker"), widget=PillRadioSelect(), required=False)
    show_when = {"walker_name": ("walker_choice", [OTHER_WALKER]), "distance_unit": ("distance", "*")}
    distance_unit = forms.ChoiceField(label=_("Distance unit"), choices=units.choices(units.DISTANCE))

    fieldsets = [
        {"fields": ["pets", "start", "end"], "layout": "required"},
        {"fields": ["walker_choice", "walker_name"]},
        {"fields": ["distance", "distance_unit", "notes"]},
    ]

    class Meta:
        model = Walk
        fields = ["pets", "start", "end", "walker_name", "distance", "notes"]
        widgets = {
            "pets": PetCheckboxSelect,
            "start": DateTimeInput(),
            "end": DateTimeInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        members = list(self.household.members.order_by("first_name", "username"))
        self.fields["walker_choice"].choices = [(str(u.pk), u.first_name or u.username) for u in members] + [
            (OTHER_WALKER, _("Other"))
        ]
        self.fields["walker_name"].label = _("Other walker's name")
        self.fields["walker_name"].help_text = ""
        if self.instance.pk:
            self.initial["walker_choice"] = str(self.instance.walker_id) if self.instance.walker_id else (
                OTHER_WALKER if self.instance.walker_name else ""
            )
        else:
            self.initial["walker_choice"] = str(self.user.pk)
        # Walk company (PUP-204): logging a pet's walk lists the pets that walk together, or that pet alone.
        anchor = list(self.instance.pets.all()) if self.instance.pk else (
            [self.initial["pet"]] if self.initial.get("pet") else [])
        if anchor:
            self.fields["pets"].queryset = walk_company(self.fields["pets"].queryset, anchor)
        elif not self.is_bound:
            self.initial["pets"] = [p for p in self.initial.get("pets", []) if p.walks_together]
        self.fields["distance"].label = _("Distance")
        unit = self.default_unit("distance")
        self.initial["distance_unit"] = unit
        if self.instance.distance is not None:
            self.initial["distance"] = round(units.from_stored(self.instance.distance, unit, units.DISTANCE), 2)

    def clean_pets(self):
        pets = self.cleaned_data.get("pets")
        alone = [p.name for p in pets if not p.walks_together] if pets and len(pets) > 1 else []
        if alone and "pets" in self.changed_data:
            raise forms.ValidationError(_("%(names)s walks alone: log that walk on its own.") % {"names": ", ".join(alone)})
        return pets

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError(_("The walk can't end before it starts."))
        choice = data.get("walker_choice")
        if choice == OTHER_WALKER:
            if not data.get("walker_name"):
                self.add_error("walker_name", _("Who walked?"))
            data["walker"] = None
        else:
            data["walker"] = self.household.members.filter(pk=choice).first() if choice else None
            data["walker_name"] = ""
        if data.get("distance") is not None:
            data["distance"] = units.to_stored(data["distance"], data.get("distance_unit") or "m", units.DISTANCE)
        return data

    def save(self, commit=True):
        walk = super().save(commit=False)
        walk.household = self.household
        walk.walker, walk.walker_name = self.cleaned_data["walker"], self.cleaned_data["walker_name"]
        if not walk.pk:
            walk.logged_by = self.user
        if commit:
            walk.save()
            self.save_m2m()
        return walk


class SleepForm(PerPetForm):
    fieldsets = [
        {"fields": ["pets", "pet", "start", "end", "kind"], "layout": "required"},
        {"fields": ["notes"]},
    ]

    class Meta:
        model = Sleep
        fields = ["pet", "start", "end", "kind", "notes"]
        widgets = {
            "pet": PetRadioSelect,
            "start": DateTimeInput(),
            "end": DateTimeInput(),
            "kind": PillRadioSelect(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError(_("Sleep can't end before it starts."))
        return data


BOWLS = "bowls"


class FillForm(PetsForm):
    unit = forms.ChoiceField(
        label=_("Unit"), choices=units.choices(units.MASS) + units.choices(units.VOLUME) + [(BOWLS, _("bowls"))]
    )
    eaten_now = forms.BooleanField(
        label=_("Eaten right away"), required=False,
        help_text=_(
            "Also logs a meal for the pets using this bowl, at the same time. Tick it when you put a portion down "
            "and they eat it (strict meal times). Leave it off when topping up a bowl they graze from."
        ),
    )

    fieldsets = [
        {"fields": ["container", "time"], "layout": "required"},
        {"fields": ["amount", "unit"]},
        {"fields": ["eaten_now"], "layout": "choices", "layout_attrs": {"label": _("Meal")}},
        {"fields": ["supply_item", "notes"]},
    ]

    class Meta:
        model = Fill
        fields = ["container", "time", "amount", "supply_item", "notes"]
        widgets = {
            "time": DateTimeInput(),
            "container": PillRadioSelect(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"container": _("Into"), "supply_item": _("From")}
        help_texts = {"amount": _("Decimals are fine, e.g. 0.5 bowls.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["container"].label_from_instance = container_label
        self.fields["amount"].required = True
        containers = list(self.fields["container"].queryset)
        litter = (Container.LITTER_BOX, Container.AUTO_LITTER_BOX)
        self.show_when["eaten_now"] = ("container", [c for c in containers if c.kind not in litter])
        self.show_when["supply_item"] = ("container", [c for c in containers if not c.holds_water])
        self.fields["unit"].initial = self.default_unit("mass")
        fill = self.instance
        if fill.pk and fill.bowls is not None:
            self.initial["unit"], self.initial["amount"] = BOWLS, fill.bowls
        elif fill.pk and fill.amount is not None:
            table, kind = (units.VOLUME, "volume") if fill.contents == Fill.WATER else (units.MASS, "mass")
            unit = self.default_unit(kind)
            self.initial["unit"] = unit
            self.initial["amount"] = round(units.from_stored(fill.amount, unit, table), 2)
        if fill.pk:
            self.initial["eaten_now"] = fill.meals.exists()
        else:
            containers = self.fields["container"].queryset
            pet = self.initial.get("pet")
            feeding = containers.filter(pets=pet, kind__in=[Container.FOOD_BOWL, Container.FEEDER]) if pet else None
            if containers.count() == 1:
                self.initial.setdefault("container", containers.first())
            elif feeding is not None and feeding.count() == 1:
                self.initial.setdefault("container", feeding.first())  # opened from a pet's page: its food bowl
        # Repeat what was done last time with each bowl: same amount, unit and meal choice (filled in by the page
        # script when a bowl is picked).
        self.container_info = {}
        for container in self.fields["container"].queryset:
            litter = container.kind in (Container.LITTER_BOX, Container.AUTO_LITTER_BOX)
            info = self.container_info[container.pk] = {"litter": litter, "add": not fill.pk,
                                                         "bag": container.default_supply_id}
            last = container.fills.order_by("-time").first()
            if last and (last.bowls is not None or last.amount is not None):
                if last.bowls is not None:
                    amount, unit = last.bowls, BOWLS
                else:
                    kind, table = ("volume", units.VOLUME) if container.holds_water else ("mass", units.MASS)
                    unit = self.default_unit(kind)
                    amount = round(units.from_stored(last.amount, unit, table), 2)
                info["last"] = {"amount": amount, "unit": unit, "eaten": last.meals.exists()}

    def clean(self):
        data = super().clean()
        container, amount, unit = data.get("container"), data.get("amount"), data.get("unit")
        if container is None or amount is None:
            return data
        data["bowls"] = None
        if unit == BOWLS:
            data["bowls"] = amount
            data["amount"] = amount * container.amount_per_bowl if container.amount_per_bowl else None
            return data
        table = units.VOLUME if container.holds_water else units.MASS
        if unit not in table:
            raise forms.ValidationError(_("Use %(units)s for this container.") % {"units": ", ".join(table) + ", bowls"})
        data["amount"] = units.to_stored(amount, unit, table)
        return data

    def save(self, commit=True):
        fill = super().save(commit=False)
        fill.amount, fill.bowls = self.cleaned_data["amount"], self.cleaned_data["bowls"]
        litter = fill.container.kind in (Container.LITTER_BOX, Container.AUTO_LITTER_BOX)
        fill.contents = Fill.WATER if fill.container.holds_water else Fill.LITTER if litter else Fill.FOOD
        if not fill.pk:
            fill.logged_by = self.user
            if fill.supply_item is None and not fill.container.holds_water:
                fill.supply_item = fill.container.default_supply
        if commit:
            fill.save()
            self.sync_meals(fill)
        return fill

    def sync_meals(self, fill):
        """Keep the meals logged with this fill in step: same time, removed if unticked."""
        self.meals_logged = []
        if not self.cleaned_data.get("eaten_now") or fill.contents == Fill.LITTER:
            fill.meals.all().delete()
            return
        if fill.meals.exists():
            fill.meals.update(time=fill.time)
            return
        pets = list(fill.container.pets.filter(active=True))
        water = fill.contents == Fill.WATER
        product = fill.supply_item.product if fill.supply_item else None
        for pet in pets:
            # With one pet, what went in the bowl is what it ate; shared bowls don't say who ate how much.
            amount = fill.amount if len(pets) == 1 else None
            Meal.objects.create(
                pet=pet, time=fill.time, logged_by=self.user, food=not water, water=water,
                kind=Meal.WATER if water else Meal.FOOD, fill=None if water else fill, water_fill=fill if water else None,
                amount=None if water else amount, water_amount=amount if water else None,
                product=None if water else product,
            )
        self.meals_logged = pets

    @property
    def has_uncalibrated(self):
        bowls = [c for c in self.fields["container"].queryset if c.kind in (Container.FOOD_BOWL, Container.WATER_BOWL)]
        return any(not c.amount_per_bowl for c in bowls)

    @property
    def uncalibrated_bowls(self):
        data = getattr(self, "cleaned_data", {})
        return data.get("bowls") is not None and data.get("amount") is None


def container_label(container):
    if container.kind in (Container.LITTER_BOX, Container.AUTO_LITTER_BOX):
        return container.name
    if container.amount_per_bowl:
        amount = units.show(container.amount_per_bowl, "volume" if container.holds_water else "mass", container.household)
        return f"{container.name} (1 bowl = {amount})"
    return f"{container.name} (bowl not calibrated)"


def pet_bowl(pet, water=False):
    """The pet's food (or water) bowl: its first active one."""
    kinds = [Container.WATER_BOWL, Container.FOUNTAIN] if water else [Container.FOOD_BOWL, Container.FEEDER]
    return pet.containers.filter(kind__in=kinds, active=True).order_by("pk").first()


def full_bowl(pet, water=False, at=None):
    """What was in this pet's full food (or water) bowl: the bowl's latest fill in the day before `at`, else how
    much it holds when full. Returns (amount or None, bowl or None)."""
    bowl = pet_bowl(pet, water)
    if bowl is None:
        return None, None
    at = at or timezone.now()
    fill = bowl.fills.filter(contents=Fill.WATER if water else Fill.FOOD, amount__isnull=False, time__lte=at,
                             time__gte=at - datetime.timedelta(days=1)).order_by("-time").first()
    return (fill.amount if fill else bowl.amount_per_bowl), bowl


def bowl_warnings(pets):
    """For the meal form: pets whose food or water bowl is missing or has no full-bowl size, with a link to fix it.
    Without it, "¼ of the bowl" can't become grams (and bag estimates stay rough)."""
    from django.urls import reverse

    out = []
    for pet in pets:
        for water, label in ((False, _("food")), (True, _("water"))):
            amount, bowl = full_bowl(pet, water)
            if bowl is None:
                out.append((_("%(pet)s has no %(what)s bowl set up, so portions can't be turned into amounts.") % {
                    "pet": pet.name, "what": label}, reverse("pets:add", args=["container"]), _("Add one")))
            elif not bowl.amount_per_bowl:
                out.append((_("%(bowl)s: how much a full bowl holds isn't set.") % {"bowl": bowl.name},
                            reverse("pets:edit", args=["container", bowl.pk]), _("Set it")))
    return out


FINISHED, OPENED = "finished", "opened"


class CanFields:
    """Finished this can / Opened a new can (PUP-210) for one food or litter on a form: the two buttons (always
    there, tap again to clear; the page script picks them when it's fairly sure), "How many did you buy?" for when
    the pantry is empty, and what the page script needs to know about each food."""

    def add_can_fields(self, s=""):
        self.fields["can" + s] = forms.MultipleChoiceField(
            label="", required=False, widget=PillCheckboxSelect(),
            choices=[(FINISHED, _("Finished this can")), (OPENED, _("Opened a new can"))])
        self.fields["guessed" + s] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.fields["bought" + s] = forms.IntegerField(label=_("How many did you buy?"), required=False, min_value=1,
                                                       max_value=200)

    def check_cans(self, data, product, s="", fill=None):
        """A food from the pantry needs an open can, or Opened a new can; opening one needs one in the pantry."""
        from .supplies import noun, open_item, pantry

        cans = data.get("can" + s) or []
        mine = fill is not None and fill.supply_item_id and fill.supply_item.product_id == product.pk
        if OPENED not in cans and not mine and open_item(product) is None:
            self.add_error("can" + s, _("No %(what)s is open: tick Opened a new %(what)s.") % {"what": noun(product)})
        elif OPENED in cans and not (fill is not None and fill.opened_new) and not data.get("bought" + s) \
                and not pantry(product).exists():
            self.add_error("bought" + s, _("None left in the pantry: how many did you buy?"))

    def use_cans(self, fill, product, s=""):
        """Point the fill at its bag or can, restocking first with what was bought."""
        from .supplies import open_item, release, restock, take

        data = self.cleaned_data
        if data.get("bought" + s) and ("bought" + s) not in self.__dict__.setdefault("_restocked", set()):
            restock(product, data["bought" + s])
            self._restocked.add("bought" + s)
        if fill.pk:
            release(fill)
        cans = data.get("can" + s) or []
        current = open_item(product)
        if data.get("guessed" + s) and FINISHED not in cans and current:  # "it isn't empty yet": stop guessing
            current.no_guess = True
            current.save(update_fields=["no_guess"])
        take(fill, product, finished=FINISHED in cans, opened=OPENED in cans)

    def initial_cans(self, fill, s=""):
        if fill and fill.pk:
            self.initial["can" + s] = [c for c, on in ((FINISHED, fill.finished_item_id), (OPENED, fill.opened_new)) if on]

    def can_info(self, products, slots, bowl=None):
        """For the page script: each food's open can and pantry, and each slot's field names."""
        from .supplies import picker_info

        return {"foods": {str(p.pk): picker_info(p) for p in products}, "mass": units.MASS,
                "bowl": bowl.amount_per_bowl if bowl else None, "editing": bool(self.instance.pk),
                "slots": [{"food": food, "amount": amount, "unit": unit, "can": "can" + s, "guessed": "guessed" + s,
                           "bought": "bought" + s} for food, amount, unit, s in slots]}


class MealForm(CanFields, PerPetForm):
    """Log meal (PUP-207, PUP-216): what went into the food and water bowls and how much was eaten or drunk. One
    form and one Save; each food and the water become their own entry, which edits on its own. Adding makes the bowl
    fills (the pantry counts those); eating or drinking is what Next Walk goes by."""

    # A second food (+ Another food), for two foods in one meal. Not "_2": the first food's own options are
    # id_portion_0, _1, _2…, and a field called portion_2 would share an id with one of them.
    SLOTS = ["", "_second"]
    water_added = forms.FloatField(label=_("Added to bowl"), required=False, min_value=0)
    water_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.VOLUME) + [(BOWLS, _("bowls"))],
                                   required=False)
    another = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Meal
        fields = ["pet", "time", "portion", "water_portion", "notes"]
        widgets = {"pet": PetRadioSelect, "time": DateTimeInput(), "notes": forms.Textarea(attrs={"rows": 3}),
                   "portion": PillRadioSelect(), "water_portion": PillRadioSelect()}
        labels = {"portion": _("Eaten"), "water_portion": _("Drank")}

    @staticmethod
    def slot_names(s):
        return ["food" + s, *(n + s for n in NEW_FOOD), "food_added" + s, "food_unit" + s, "can" + s, "bought" + s,
                "portion" + s]

    def __init__(self, *args, importing=False, **kwargs):
        # Imported meals are history (PUP-216): no food has to be picked, and the pantry isn't touched.
        self.importing = importing
        super().__init__(*args, **kwargs)
        meal = self.instance
        self.editing = meal.kind if meal.pk else None
        self.slots = [] if self.editing == Meal.WATER else [""] if self.editing else self.SLOTS
        if self.editing == Meal.FOOD:
            for name in ("water_added", "water_unit", "water_portion"):
                self.fields.pop(name)
        if self.editing == Meal.WATER:
            self.fields.pop("portion")
        if self.editing:
            self.fields.pop("another")
        for s in self.slots:
            self.add_food_fields(s)
        for name in ("portion", "water_portion"):
            if name in self.fields:
                self.fields[name].help_text = ""
                self.fields[name].widget.choices = [c for c in self.fields[name].choices if c[0] != ""]
        if "water_unit" in self.fields:
            self.fields["water_unit"].initial = self.default_unit("volume")
        food_section = [name for s in self.slots for name in self.slot_names(s)]
        self.fieldsets = [{"fields": ["pet" if meal.pk else "pets", "time"], "layout": "required"}]
        if food_section:
            self.fieldsets.append({"fields": food_section, "layout": "section", "layout_attrs": {"label": _("Food")}})
        if self.editing != Meal.FOOD:
            self.fieldsets.append({"fields": ["water_added", "water_unit", "water_portion"], "layout": "section",
                                   "layout_attrs": {"label": _("Water")}})
        self.fieldsets.append({"fields": ["notes"]})
        pets = [meal.pet] if meal.pk else (
            [p for p in [self.initial.get("pet")] if p] or list(self.initial.get("pets") or []))
        self.warnings = bowl_warnings(pets)
        if meal.pk:
            fill = meal.fill if self.editing == Meal.FOOD else meal.water_fill
            names = ("food_added", "food_unit") if self.editing == Meal.FOOD else ("water_added", "water_unit")
            if fill:
                self.initial[names[0]], self.initial[names[1]] = self.shown_amount(fill)
            if self.editing == Meal.FOOD:
                item = fill.supply_item if fill else None
                self.initial["food"] = str(item.product_id) if item else ""
                self.initial_cans(fill)
        elif pets:
            # The food and unit used last time for this pet's bowl; the amount only as a hint (nothing added unless typed).
            for water, amount, unit in ((False, "food_added", "food_unit"), (True, "water_added", "water_unit")):
                bowl = pet_bowl(pets[0], water)
                last = bowl and bowl.fills.order_by("-time").first()
                if not water and bowl:  # the food given last time, while there's some left
                    bag = (last.supply_item if last and last.supply_item else None) or bowl.default_supply
                    if bag and str(bag.product_id) in self.food_products:
                        self.initial["food"] = str(bag.product_id)
                if last and (last.amount is not None or last.bowls is not None):
                    shown, self.fields[unit].initial = self.shown_amount(last)
                    self.fields[amount].widget.attrs["placeholder"] = _("Last time: %(n)s") % {"n": f"{shown:g}"}
        bowl = pet_bowl(pets[0]) if pets else None
        self.food_info_json = self.can_info(
            self.food_products.values(), [("food" + s, "food_added" + s, "food_unit" + s, s) for s in self.slots],
            bowl) if self.slots else None

    def add_food_fields(self, s):
        """One food's fields: which food (one in stock, or "new" to add it to the pantry right here), how much went
        in, the can buttons, and how much was eaten."""
        f = self.fields
        f["food" + s] = forms.ChoiceField(label=_("Second food") if s else _("Food"), required=False)
        f["new_food_name" + s] = forms.CharField(label=_("Name"), max_length=255, required=False)
        f["new_food_category" + s] = forms.ChoiceField(
            label=_("Kind"), required=False, widget=PillRadioSelect(),
            choices=[(Product.DRY_FOOD, _("Dry food")), (Product.WET_FOOD, _("Wet food"))])
        f["new_food_size" + s] = forms.FloatField(label=_("Size of each"), required=False, min_value=0)
        f["new_food_size_unit" + s] = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS),
                                                        required=False, initial=units.unit_for(self.household, "bag"))
        f["new_food_count" + s] = forms.IntegerField(label=_("How many"), required=False, min_value=1, max_value=200,
                                                     initial=1)
        f["food_added" + s] = forms.FloatField(label=_("Added to bowl"), required=False, min_value=0)
        f["food_unit" + s] = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS) + [(BOWLS, _("bowls"))],
                                               required=False, initial=self.default_unit("mass"))
        self.add_can_fields(s)
        if s:
            f["portion" + s] = forms.TypedChoiceField(label=_("Eaten"), required=False, coerce=float, empty_value=None,
                                                      choices=Meal.PORTIONS, widget=PillRadioSelect())
        self.food_choices(s)
        shown = [("another", ["1"])] if s else []
        for name in self.slot_names(s):
            rule = shown + ([("food" + s, ["new"])] if name[:-len(s) or None] in NEW_FOOD else [])
            if rule:
                self.show_when[name] = rule

    def food_choices(self, s):
        """The foods in stock, each with what's left of its open bag or can, then "+ Add food to the pantry". When
        editing, the entry's own food is there even once it has run out."""
        from .supplies import in_stock, product_status

        if "food_products" not in self.__dict__:
            foods = self.household.products.filter(category__in=Product.FOODS)
            stocked = in_stock(foods)
            fill = self.instance.fill if self.instance.pk else None
            keep = fill.supply_item.product if fill and fill.supply_item else None
            if keep and keep not in stocked:
                stocked.append(keep)
            self.food_details = {}
            for product in stocked:
                st = product_status(product)
                if st["empty"]:
                    parts = [str(_("should be empty"))]
                elif st["left"]:
                    parts = [f"{st['left']} {_('left')} · {st['pct_left']}%"]
                else:
                    parts = [str(_("unopened"))]
                if st["pantry"]:
                    parts.append(str(_("+%(n)s in pantry") % {"n": st["pantry"]}))
                self.food_details[str(product.pk)] = " · ".join(parts)
            self.food_products = {str(p.pk): p for p in stocked}
        field = self.fields["food" + s]
        field.choices = [("", _("Pick a food"))] + [(pk, str(p)) for pk, p in self.food_products.items()] + [
            ("new", "+ " + str(_("Add food to the pantry")))]
        field.widget = FoodPicker(choices=field.choices, details=self.food_details)

    def shown_amount(self, fill):
        """A fill as it was entered: in bowls, or in the household's unit."""
        if fill.bowls is not None:
            return fill.bowls, BOWLS
        kind, table = ("volume", units.VOLUME) if fill.contents == Fill.WATER else ("mass", units.MASS)
        unit = self.default_unit(kind)
        return round(units.from_stored(fill.amount, unit, table), 2), unit

    def clean(self):
        data = super().clean()
        meal = self.instance
        legacy = meal.pk and not (meal.fill_id or meal.water_fill_id)  # older entries: only what was eaten
        added = [s for s in self.slots if data.get("food_added" + s)]
        eaten = [data.get("portion" + s) for s in self.slots] + [data.get("water_portion")]
        if not self.editing and not added and not data.get("water_added") and not any(eaten):
            raise forms.ValidationError(_("Fill in what went into a bowl."))
        if not legacy:
            for s in self.slots:
                if data.get("portion" + s) and not data.get("food_added" + s):
                    self.add_error("food_added" + s, _("Enter what went into the bowl."))
            if data.get("water_portion") and not data.get("water_added"):
                self.add_error("water_added", _("Enter what went into the bowl."))
            if self.editing and not added and not data.get("water_added"):
                self.add_error("food_added" if self.editing == Meal.FOOD else "water_added",
                               _("Enter what went into the bowl."))
        for s in added:
            food = data.get("food" + s)
            if food == "new":
                for name in ("new_food_name" + s, "new_food_category" + s):
                    if not data.get(name):
                        self.add_error(name, _("This field is required."))
                known = self.known_food(data.get("new_food_name" + s))
                if not data.get("new_food_size" + s) and not (known and known.package_size):
                    self.add_error("new_food_size" + s, _("Enter the size of each bag or can."))
            elif not food:
                if not self.importing:
                    self.add_error("food" + s, _("Pick the food, or add it to the pantry."))
            elif not self.importing:
                self.check_cans(data, self.food_products[food], s, meal.fill if self.editing else None)
        pets = [meal.pet] if meal.pk else list(data.get("pets") or [])
        for names, water, what in ((["food_added" + s for s in added], False, _("food")),
                                   (["water_added"] if data.get("water_added") else [], True, _("water"))):
            missing = [p.name for p in pets if pet_bowl(p, water) is None] if names else []
            if missing:
                text = (_("%(names)s has no %(what)s bowl set up.") if len(missing) == 1 else
                        _("%(names)s have no %(what)s bowl set up."))
                self.add_error(names[0], text % {"names": ", ".join(missing), "what": what})
        return data

    def known_food(self, name):
        """The household's food by this name (or brand and name), if it has one."""
        name = (name or "").strip().lower()
        foods = self.household.products.filter(category__in=Product.FOODS)
        return next((p for p in foods if name and name in (p.name.lower(), str(p).lower())), None)

    def product_for(self, s):
        """The food picked in a slot; a new one is added to the pantry first (or restocked, if it's one we know)."""
        from .supplies import restock

        made = self.__dict__.setdefault("_made", {})
        food = self.cleaned_data.get("food" + s) or ""
        if food != "new":
            return self.food_products.get(food)
        if s not in made:
            data = self.cleaned_data
            unit = data.get("new_food_size_unit" + s) or units.unit_for(self.household, "bag")
            size = data.get("new_food_size" + s)
            size = units.to_stored(size, unit, units.MASS) if size else None
            product = self.known_food(data["new_food_name" + s])  # one that's run out: restocked, not added twice
            if product is None:
                product = Product.objects.create(household=self.household, name=data["new_food_name" + s],
                                                 category=data["new_food_category" + s], package_size=size, unit=unit)
            elif size and not product.package_size:
                product.package_size = size
                product.save(update_fields=["package_size"])
            restock(product, data.get("new_food_count" + s) or 1,
                    pets=list(Pet.objects.filter(household=self.household, active=True)))
            made[s] = product
        return made[s]

    def bowl_fill(self, meal, water, s, fill=None):
        """The fill for one food (or the water) in this pet's bowl: pets sharing a bowl share one."""
        shared = self.__dict__.setdefault("_fills", {})
        data = self.cleaned_data
        amount = data.get("water_added" if water else "food_added" + s)
        bowl = pet_bowl(meal.pet, water)
        if fill is None or fill.container_id != bowl.pk:
            if (bowl.pk, s, water) in shared:
                return shared[(bowl.pk, s, water)]
            fill = Fill(container=bowl, contents=Fill.WATER if water else Fill.FOOD, logged_by=self.user)
        fill.time = meal.time
        chosen = data.get("water_unit" if water else "food_unit" + s) or self.default_unit("volume" if water else "mass")
        if chosen == BOWLS:
            fill.bowls = amount
            fill.amount = amount * bowl.amount_per_bowl if bowl.amount_per_bowl else None
        else:
            fill.bowls = None
            fill.amount = units.to_stored(amount, chosen, units.VOLUME if water else units.MASS)
        if not water:
            product = self.product_for(s)
            if product and not self.importing:
                self.use_cans(fill, product, s)
            meal.product = product
        fill.save()
        shared[(bowl.pk, s, water)] = fill
        return fill

    @staticmethod
    def eaten(meal):
        """How much was eaten or drunk, from the bowl's portion."""
        water = meal.kind == Meal.WATER
        share = meal.water_portion if water else meal.portion
        field = "water_amount" if water else "amount"
        if share:
            full, _bowl = full_bowl(meal.pet, water, meal.time)
            setattr(meal, field, round(full * share, 1) if full else None)
        elif meal.fill_id or meal.water_fill_id or not meal.pk:
            setattr(meal, field, None)
        # Ate / drank at this time: only when marked. A bowl nobody marked was eaten some time before the next
        # fill, so it counts towards the day's amounts (PUP-218) but doesn't move Next walk (PUP-207).
        meal.food = not water and bool(meal.portion or meal.amount)
        meal.water = water and bool(meal.water_portion or meal.water_amount)

    def save_entries(self):
        if self.instance.pk:
            return [self.save()]
        data = self.cleaned_data
        entries = []
        for pet in data["pets"]:
            made = []
            for s in self.slots:
                if data.get("food_added" + s) and (not s or data.get("another") == "1"):
                    meal = Meal(pet=pet, time=data["time"], kind=Meal.FOOD, logged_by=self.user,
                                portion=data.get("portion" + s))
                    meal.fill = self.bowl_fill(meal, False, s)
                    made.append(meal)
            if data.get("water_added"):
                meal = Meal(pet=pet, time=data["time"], kind=Meal.WATER, logged_by=self.user,
                            water_portion=data.get("water_portion"))
                meal.water_fill = self.bowl_fill(meal, True, "")
                made.append(meal)
            for i, meal in enumerate(made):
                meal.notes = data.get("notes", "") if i == 0 else ""  # the note goes with the first entry
                self.eaten(meal)
                meal.save()
            entries += made
        return entries

    def save(self, commit=True):
        meal = super().save(commit=False)
        water = meal.kind == Meal.WATER
        attr = "water_fill" if water else "fill"
        before = getattr(meal, attr + "_id")
        if self.cleaned_data.get("water_added" if water else "food_added"):
            setattr(meal, attr, self.bowl_fill(meal, water, "", getattr(meal, attr)))
        else:
            setattr(meal, attr, None)
        self.eaten(meal)
        if commit:
            meal.save()
            if before and before != getattr(meal, attr + "_id"):
                Fill.objects.filter(pk=before, meals=None, water_meals=None).delete()
        return meal


NEW_FOOD = ["new_food_name", "new_food_category", "new_food_size", "new_food_size_unit", "new_food_count"]


class LitterForm(CanFields, PetsForm):
    """Litter put into a litter box (PUP-207): its own entry, not a bowl fill. Which litter, with the same Finished /
    Opened buttons as food (PUP-210)."""

    LITTER_BOXES = (Container.LITTER_BOX, Container.AUTO_LITTER_BOX)
    unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS))
    litter = forms.ChoiceField(label=_("Litter"), required=False)

    fieldsets = [
        {"fields": ["container", "time"], "layout": "required"},
        {"fields": ["amount", "unit", "litter", "can", "bought", "notes"]},
    ]

    class Meta:
        model = Fill
        fields = ["container", "time", "amount", "notes"]
        widgets = {"time": DateTimeInput(), "container": PillRadioSelect(), "notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"container": _("Litter box")}
        help_texts = {"amount": ""}

    def __init__(self, *args, **kwargs):
        from .supplies import in_stock, product_status

        super().__init__(*args, **kwargs)
        boxes = self.fields["container"].queryset.filter(kind__in=self.LITTER_BOXES)
        self.fields["container"].queryset = boxes
        fill = self.instance
        unit = self.default_unit("mass")
        self.fields["unit"].initial = unit
        if fill.pk and fill.amount is not None:
            self.initial["amount"] = round(units.from_stored(fill.amount, unit, units.MASS), 2)
        elif boxes.count() == 1:
            self.initial.setdefault("container", boxes.first())
        stocked = in_stock(self.household.products.filter(category=Product.LITTER))
        keep = fill.supply_item.product if fill.pk and fill.supply_item else None
        if keep and keep not in stocked:
            stocked.append(keep)
        self.litters = {str(p.pk): p for p in stocked}
        details = {}
        for product in stocked:
            st = product_status(product)
            details[str(product.pk)] = (str(_("should be empty")) if st["empty"] else
                                        f"{st['left']} {_('left')} · {st['pct_left']}%" if st["left"] else str(_("unopened")))
        field = self.fields["litter"]
        field.choices = [("", _("Pick the litter"))] + [(pk, str(p)) for pk, p in self.litters.items()]
        field.widget = FoodPicker(choices=field.choices, details=details)
        if keep:
            self.initial["litter"] = str(keep.pk)
        elif len(self.litters) == 1:
            self.initial["litter"] = next(iter(self.litters))
        self.add_can_fields()
        self.initial_cans(fill)
        self.food_info_json = self.can_info(stocked, [("litter", "amount", "unit", "")]) if stocked else None
        if not stocked:  # no litter in the pantry: nothing to pick or open
            for name in ("litter", "can", "guessed", "bought"):
                del self.fields[name]

    def clean(self):
        data = super().clean()
        product = self.litters.get(data.get("litter") or "")
        if data.get("amount") and product:
            self.check_cans(data, product, "", self.instance if self.instance.pk else None)
        elif data.get("amount") and self.litters:
            self.add_error("litter", _("Pick the litter."))
        return data

    def save(self, commit=True):
        from .supplies import release

        fill = super().save(commit=False)
        fill.contents = Fill.LITTER
        if fill.amount is not None:
            fill.amount = units.to_stored(self.cleaned_data["amount"], self.cleaned_data["unit"], units.MASS)
        if not fill.pk:
            fill.logged_by = self.user
        product = self.litters.get(self.cleaned_data.get("litter") or "")
        if product:
            self.use_cans(fill, product)
        elif fill.pk:
            release(fill)
            fill.supply_item = None
        if commit:
            fill.save()
        return fill


class MeasurementForm(PerPetForm):
    weight_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.WEIGHT))
    length_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.LENGTH))
    show_when = {
        "weight_unit": ("kind", [Measurement.WEIGHT]),
        "length_unit": ("kind", [Measurement.HEIGHT, Measurement.HEIGHT_HEAD, Measurement.LENGTH]),
    }

    fieldsets = [
        {"fields": ["pets", "pet", "date", "kind", "value", "weight_unit", "length_unit"], "layout": "required"},
        {"fields": ["notes"]},
    ]

    class Meta:
        model = Measurement
        fields = ["pet", "date", "kind", "value", "notes"]
        widgets = {
            "pet": PetRadioSelect,
            "date": DateInput(),
            "kind": PillRadioSelect(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weight_unit"].initial = self.default_unit("weight")
        self.fields["length_unit"].initial = self.default_unit("length")
        if not self.instance.pk:
            self.initial.setdefault("kind", Measurement.WEIGHT)
        if self.instance.pk:
            weight = self.instance.kind == Measurement.WEIGHT
            unit = self.default_unit("weight" if weight else "length")
            self.initial["value"] = round(
                units.from_stored(self.instance.value, unit, units.WEIGHT if weight else units.LENGTH), 2
            )

    def clean(self):
        data = super().clean()
        kind, value = data.get("kind"), data.get("value")
        if kind and value is not None:
            weight = kind == Measurement.WEIGHT
            unit = data.get("weight_unit" if weight else "length_unit") or self.default_unit("weight" if weight else "length")
            data["value"] = units.to_stored(value, unit, units.WEIGHT if weight else units.LENGTH)
        return data

    def save(self, commit=True):
        measurement = super().save(commit=False)
        measurement.value = self.cleaned_data["value"]
        if commit:
            measurement.save()
        return measurement


class NoteForm(PetsForm):
    fieldsets = [{"fields": ["pet", "time", "text"], "layout": "required"}]

    class Meta:
        model = Note
        fields = ["pet", "time", "text"]
        widgets = {"time": DateTimeInput(), "text": forms.Textarea(attrs={"rows": 4})}
        help_texts = {"pet": _("Leave empty for a household note.")}

    def save(self, commit=True):
        note = super().save(commit=False)
        note.household = self.household
        if not note.pk:
            note.logged_by = self.user
        if commit:
            note.save()
        return note


# --- Training (PUP-150) ----------------------------------------------------------------------------
class CommandForm(PetsForm):
    fieldsets = [{"fields": ["pet", "name", "status"], "layout": "required"}, {"fields": ["how_to", "when_to_use"]}]

    class Meta:
        model = Command
        fields = ["pet", "name", "status", "how_to", "when_to_use"]
        widgets = {"pet": PetRadioSelect, "status": PillRadioSelect(),
                   "how_to": forms.Textarea(attrs={"rows": 3}), "when_to_use": forms.Textarea(attrs={"rows": 3})}


class TrainingLogForm(PetsForm):
    fieldsets = [{"fields": ["command", "time", "score"], "layout": "required"}, {"fields": ["notes"]}]

    class Meta:
        model = TrainingLog
        fields = ["command", "time", "score", "notes"]
        widgets = {"time": DateTimeInput(), "score": PillRadioSelect(), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["command"].queryset = Command.objects.filter(pet__household=self.household).select_related("pet")
        self.fields["score"].help_text = " · ".join(
            f"{n} {label}: {TrainingLog.SCORE_HELP[n]}" for n, label in TrainingLog.SCORES)

    def save(self, commit=True):
        log = super().save(commit=False)
        if not log.pk:
            log.logged_by = self.user
        if commit:
            log.save()
        return log


# --- Setup: pets, containers, products and bags ------------------------------------------------
class HouseholdOwnedForm(PetsForm):
    """Forms for things that belong directly to the household."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.fieldsets:
            self.fieldsets = [{"fields": list(self.fields), "layout": "default"}]

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.household = self.household
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class PetForm(HouseholdOwnedForm):
    potty_locations = forms.MultipleChoiceField(
        label=_("Potty locations"), choices=PottyBreak.LOCATIONS, widget=PillCheckboxSelect, required=False,
        help_text=_("Only these show when logging a potty break. Leave all off for the usual ones for the type."),
    )

    class Meta:
        model = Pet
        fields = ["name", "type", "breed", "sex", "birth_date", "birth_date_estimated", "potty_locations",
                  "pee_every", "poop_every", "after_meal", "walks_together", "picture", "active", "notes"]
        widgets = {"type": PillRadioSelect(), "sex": PillRadioSelect(), "birth_date": DateInput(),
                   "pee_every": DurationSelect(), "poop_every": DurationSelect(), "picture": PictureInput(),
                   "notes": forms.Textarea(attrs={"rows": 3})}

    picture_crop = forms.CharField(required=False, widget=forms.HiddenInput)
    # The framed square as a JPEG data: URL, drawn by the page (PUP-169).
    picture_square = forms.CharField(required=False, widget=forms.HiddenInput)

    PICTURE_MAX = 10 * 1024 * 1024
    SQUARE_MAX = 2 * 1024 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_when = {"birth_date_estimated": ("birth_date", self.ANY)}
        self.fields["picture"].help_text = _("Drag and resize the box to frame your pet.")
        self.fields["picture"].widget.attrs["accept"] = "image/*"
        if not self.instance.pk:
            del self.fields["walks_together"]  # a new pet walks as its type does (PUP-204); changed afterwards
        if self.instance.pk:
            self.initial["potty_locations"] = self.instance.allowed_potty_locations()
            if self.instance.picture_crop:
                import json

                self.initial["picture_crop"] = json.dumps(self.instance.picture_crop)

    def clean_picture(self):
        """A new photo must be what the page makes of it: a JPEG, stored under a .jpg name whatever it was called."""
        picture = self.cleaned_data.get("picture")
        if picture and "picture" in self.changed_data and hasattr(picture, "content_type"):
            head = picture.read(3)
            picture.seek(0)
            if head != JPEG_START or picture.size > self.PICTURE_MAX:
                raise forms.ValidationError(_("That photo didn't come through. Pick it again."))
            picture.name = "photo.jpg"
        return picture

    def clean_picture_square(self):
        """The framed square from the page, as JPEG bytes; anything else is ignored."""
        import base64
        import binascii

        raw = self.cleaned_data.get("picture_square") or ""
        prefix = "data:image/jpeg;base64,"
        if not raw.startswith(prefix):
            return None
        try:
            data = base64.b64decode(raw[len(prefix):], validate=True)
        except (binascii.Error, ValueError):
            return None
        return data if data.startswith(JPEG_START) and len(data) <= self.SQUARE_MAX else None

    def clean_picture_crop(self):
        """The crop box from the page: fractions of the picture, {x, y, w, h}; anything odd is ignored."""
        import json

        raw = self.cleaned_data.get("picture_crop")
        try:
            crop = {k: float(v) for k, v in json.loads(raw).items() if k in ("x", "y", "w", "h")} if raw else None
        except (TypeError, ValueError, AttributeError):
            return None
        if not crop or len(crop) != 4 or not all(0 <= v <= 1 for v in crop.values()) or crop["w"] <= 0 or crop["h"] <= 0:
            return None
        return crop

    def save(self, commit=True):
        if self.instance.pk and {"pee_every", "poop_every"} & set(self.changed_data):
            from django.utils import timezone

            self.instance.schedule_changed_at = timezone.now()  # the coach starts counting again
        self.instance.schedule_checked = True  # seen and saved: the setup checklist's schedule step is done
        if not self.instance.pk:
            self.instance.walks_together = self.instance.type == Pet.DOG
        pet = super().save(commit=commit)
        if commit:
            crop, square = self.cleaned_data.get("picture_crop"), self.cleaned_data.get("picture_square")
            if not pet.picture:
                crop, square = None, None
            # A new picture without a square shows its middle (object-fit on the page) until it's framed.
            if ("picture" in self.changed_data or not pet.picture
                    or (square and (crop != pet.picture_crop or not pet.picture_cropped))):
                pet.picture_crop = crop if square else None
                pet.set_cropped_picture(square)
                pet.save(update_fields=["picture_crop", "picture_cropped"])
        return pet


JPEG_START = b"\xff\xd8\xff"


class ContainerForm(HouseholdOwnedForm):
    bowl_unit = forms.ChoiceField(label=_("Unit"), required=False,
                                  choices=units.choices(units.MASS) + units.choices(units.VOLUME))

    class Meta:
        model = Container
        fields = ["name", "kind", "pets", "amount_per_bowl", "default_supply", "active"]
        widgets = {"kind": PillRadioSelect(), "pets": PetCheckboxSelect}
        labels = {"amount_per_bowl": _("One full bowl holds")}
        help_texts = {"amount_per_bowl": _(
            "To calibrate: put the empty bowl on a kitchen scale and tare it, fill it the way you usually do, "
            "and enter the weight. Leave empty for automatic feeders and litter boxes."
        )}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order = ["name", "kind", "pets", "amount_per_bowl", "bowl_unit", "default_supply", "active"]
        self.fieldsets = [{"fields": order, "layout": "default"}]
        bowls = [Container.FOOD_BOWL, Container.WATER_BOWL]
        self.show_when = {
            "amount_per_bowl": ("kind", bowls), "bowl_unit": ("kind", bowls),
            "default_supply": ("kind", [Container.FOOD_BOWL, Container.FEEDER, Container.LITTER_BOX,
                                        Container.AUTO_LITTER_BOX]),
        }
        self.fields["bowl_unit"].initial = self.default_unit("mass")
        if self.instance.pk and self.instance.amount_per_bowl:
            water = self.instance.kind in (Container.WATER_BOWL, Container.FOUNTAIN)
            unit = self.default_unit("volume" if water else "mass")
            self.initial["bowl_unit"] = unit
            self.initial["amount_per_bowl"] = round(
                units.from_stored(self.instance.amount_per_bowl, unit, units.VOLUME if water else units.MASS), 2)

    def clean(self):
        data = super().clean()
        amount, unit, kind = data.get("amount_per_bowl"), data.get("bowl_unit"), data.get("kind")
        if amount is not None and kind:
            table = units.VOLUME if kind in (Container.WATER_BOWL, Container.FOUNTAIN) else units.MASS
            if unit not in table:
                raise forms.ValidationError(_("Use %(units)s for this container.") % {"units": ", ".join(table)})
            data["amount_per_bowl"] = units.to_stored(amount, unit, table)
        return data


class AccidentSpotForm(HouseholdOwnedForm):
    class Meta:
        model = AccidentSpot
        fields = ["name"]
        help_texts = {"name": _("A place accidents happen, e.g. dining room rug, by the back door.")}


class PottyLocationForm(HouseholdOwnedForm):
    class Meta:
        model = PottyLocation
        fields = ["name", "accident"]
        help_texts = {"name": _("Somewhere a pet goes besides Outside, Inside, Pad and Litter box, e.g. balcony.")}


class AccidentContextForm(HouseholdOwnedForm):
    class Meta:
        model = AccidentContext
        fields = ["name"]
        help_texts = {"name": _("Something that was going on before an accident, e.g. doorbell, new toy.")}

    def save(self, commit=True):
        """Potty breaks keep the name, so a rename carries over to the ones already logged."""
        old = AccidentContext.objects.get(pk=self.instance.pk).name if self.instance.pk else None
        obj = super().save(commit=commit)
        if commit and old and old != obj.name:
            for b in PottyBreak.everything.filter(pet__household=obj.household).exclude(context=[]):
                if old in b.context:
                    b.context = [obj.name if c == old else c for c in b.context]
                    b.save(update_fields=["context"])
        return obj


class ProductForm(HouseholdOwnedForm):
    """A food or litter in the Pantry (PUP-210): what it is, the size of each bag or can (in the unit it's sold in,
    which is how it's shown), when to warn, and, when adding it, how many there are."""

    size_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS))
    low_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS), required=False)
    count = forms.IntegerField(label=_("How many"), min_value=0, max_value=500, initial=1, required=False)
    show_when = {"low_unit": ("low_by", [Product.AMOUNT])}

    class Meta:
        model = Product
        fields = ["name", "brand", "category", "package_size", "low_by", "low_at"]
        widgets = {"category": PillRadioSelect(), "low_by": PillRadioSelect()}
        labels = {"category": _("Kind"), "package_size": _("Size of each"), "low_by": _("Running low"),
                  "low_at": _("Warn at")}
        help_texts = {"low_at": _("Bags or cans left (open and in the pantry), or the amount left in them.")}

    def __init__(self, *args, litter=False, **kwargs):
        super().__init__(*args, **kwargs)
        product = self.instance
        names = ["name", "brand", "category", "package_size", "size_unit", "count", "low_by", "low_at", "low_unit"]
        if product.pk:
            names.remove("count")  # the count is set in the Pantry
            del self.fields["count"]
        self.fieldsets = [{"fields": names, "layout": "default"}]
        self.fields["package_size"].required = True
        self.fields["low_by"].widget.choices = [c for c in self.fields["low_by"].choices if c[0] != ""]
        # Litter isn't a food: a food is dry or wet, and litter has no kind to pick.
        self.litter = litter = product.category == Product.LITTER if product.pk else litter
        if litter:
            names.remove("category")
            del self.fields["category"]
            self.instance.category = Product.LITTER
        else:
            self.fields["category"].choices = [c for c in self.fields["category"].choices if c[0] in Product.FOODS]
            self.initial.setdefault("category", product.category if product.pk else Product.DRY_FOOD)
        unit = product.unit or units.unit_for(self.household, "bag")
        self.fields["size_unit"].initial = self.fields["low_unit"].initial = unit
        if product.pk and product.package_size:
            self.initial["package_size"] = f"{units.from_stored(product.package_size, unit, units.MASS):.6g}"
        if product.pk and product.low_by == Product.AMOUNT:
            self.initial["low_at"] = f"{units.from_stored(product.low_at, unit, units.MASS):.6g}"
        elif product.pk:
            self.initial["low_at"] = f"{product.low_at:g}"  # 2 cans, not 2.0

    def clean(self):
        data = super().clean()
        if data.get("package_size") is not None:
            data["package_size"] = units.to_stored(data["package_size"], data.get("size_unit") or "g", units.MASS)
        if data.get("low_by") == Product.AMOUNT and data.get("low_at") is not None:
            data["low_at"] = units.to_stored(data["low_at"], data.get("low_unit") or "g", units.MASS)
        return data

    def save(self, commit=True):
        from .supplies import restock

        product = super().save(commit=False)
        product.unit = self.cleaned_data.get("size_unit") or product.unit
        new = not product.pk
        if commit:
            product.save()
            if new and self.cleaned_data.get("count"):
                restock(product, self.cleaned_data["count"])
            elif not new:  # a new usual size applies to the ones still unopened
                product.items.filter(opened=None, finished=None, fills=None).update(size=product.package_size)
        return product


class SupplyItemForm(PetsForm):
    """Bags, boxes or cans of one product (PUP-213): as many as were bought, into the pantry."""

    size_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS))
    count = forms.IntegerField(label=_("How many"), min_value=1, max_value=200, initial=1)

    class Meta:
        model = SupplyItem
        fields = ["product", "size", "opened", "finished", "price", "pets"]
        widgets = {"opened": DateInput(), "finished": DateInput(), "pets": PetCheckboxSelect}
        labels = {"size": _("Size of each")}
        help_texts = {"size": _("Leave empty to use the product's usual package size."),
                      "opened": _("Leave empty for the pantry: it's opened when a meal first takes from it.")}

    def __init__(self, *args, litter=False, **kwargs):
        super().__init__(*args, **kwargs)
        # A food source or a litter source (PUP-210): only that kind of product to pick from.
        if self.instance.pk:
            litter = self.instance.product.category == Product.LITTER
        self.litter = litter
        products = self.fields["product"].queryset
        self.fields["product"].queryset = products.filter(category=Product.LITTER) if litter else products.exclude(
            category=Product.LITTER)
        self.fields["size"].required = False
        names = ["product", "count", "size", "size_unit", "opened", "finished", "price", "pets"]
        if self.instance.pk:
            del self.fields["count"]  # one bag or can at a time once it's in
        self.fieldsets = [{"fields": names, "layout": "default"}]
        unit = units.unit_for(self.household, "bag")
        self.fields["size_unit"].initial = unit
        if not self.instance.pk:  # into the pantry: it's opened when a meal first takes from it
            self.initial.setdefault("pets", list(Pet.objects.filter(household=self.household, active=True)))
        elif self.instance.size:
            self.initial["size"] = round(units.from_stored(self.instance.size, unit, units.MASS), 2)

    def clean(self):
        data = super().clean()
        if data.get("size") is not None:
            data["size"] = units.to_stored(data["size"], data.get("size_unit") or "g", units.MASS)
        elif data.get("product") and data["product"].package_size:
            data["size"] = data["product"].package_size
        else:
            self.add_error("size", _("Enter the size (the product has no usual size yet)."))
        return data

    def save_entries(self):
        """The first as entered; the rest of the count into the pantry, unopened."""
        first = self.save()
        entries = [first]
        for _n in range(1, self.cleaned_data.get("count") or 1):
            item = SupplyItem.objects.create(product=first.product, size=first.size, price=first.price)
            item.pets.set(first.pets.all())
            entries.append(item)
        return entries


class HouseholdForm(forms.ModelForm):
    PER_KIND = [f"unit_{kind}" for kind in units.KINDS]

    class Meta:
        model = Household
        fields = ["name", "same_units", "units", *[f"unit_{kind}" for kind in units.KINDS], "time_zone",
                  "minutes_to_outside", "bedtime", "wake_time", "water_cutoff_hours"]
        widgets = {"units": PillRadioSelect(), "bedtime": TimeInput(), "wake_time": TimeInput()}
        labels = {"units": _("Units")}
        help_texts = {"units": _("Metric shows g, kg, ml, cm, km; imperial shows oz, lb, fl oz, in, mi.")}

    def __init__(self, *args, **kwargs):
        from housetrained import time_zones

        super().__init__(*args, **kwargs)
        for name in ("bedtime", "wake_time"):  # left blank: keep what's set
            self.fields[name].required = False
        # Units (PUP-140): one system, or one unit per kind. Each kind starts at its last saved pick, else the system's.
        self.fields["units"].required = False
        for kind, name in zip(units.KINDS, self.PER_KIND):
            saved = getattr(self.instance, name)
            self.fields[name] = forms.ChoiceField(
                label=self.fields[name].label[:1].upper() + self.fields[name].label[1:],
                choices=units.choices(units.PER_KIND[kind]), required=False, widget=PillRadioSelect())
            self.initial[name] = saved or units.DEFAULTS[self.instance.units][kind]
        # Time zone (PUP-141): a dropdown by UTC offset; with nothing set yet, the device's zone (else the server's).
        zone = self.instance.time_zone
        self.fields["time_zone"] = forms.ChoiceField(
            label=_("Default time zone"), choices=time_zones.choices(zone), required=False,
            widget=time_zones.TimeZoneSelect(guess=not zone),
            help_text=_("For reminders, and where new members start."))
        self.initial["time_zone"] = zone or time_zones.default()

    @property
    def show_when_json(self):
        return {"units": [["same_units", ["true"]]], **{name: [["same_units", ["false"]]] for name in self.PER_KIND}}

    @property
    def unit_defaults_json(self):
        """For the page: kinds never picked follow the system choice until saved."""
        return {"defaults": units.DEFAULTS,
                "unsaved": [name for name in self.PER_KIND if not getattr(self.instance, name)]}

    def clean(self):
        data = super().clean()
        for name in ("bedtime", "wake_time"):
            if data.get(name) is None:
                data[name] = getattr(self.instance, name)
        # The hidden side isn't sent: keep what was saved there.
        hidden = self.PER_KIND if data.get("same_units") else ["units"]
        for name in hidden:
            data[name] = getattr(self.instance, name)
        if not data.get("units"):
            data["units"] = self.instance.units
        return data
