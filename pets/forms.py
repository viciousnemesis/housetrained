import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from babybuddy.widgets import DateInput, DateTimeInput, TimeInput

from . import units
from .models import AccidentSpot, Container, Fill, Household, Meal, Measurement, Note, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk
from .widgets import PetCheckboxSelect, PetRadioSelect, PillCheckboxSelect, PillRadioSelect


class PetsForm(forms.ModelForm):
    """Scopes choices to the household and renders with Baby Buddy's fieldset layouts."""

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
    UNIT_OF = {"unit": "amount", "water_unit": "water_amount", "distance_unit": "distance", "weight_unit": "value",
               "length_unit": "value", "bowl_unit": "amount_per_bowl"}

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


class PottyBreakForm(PerPetForm):
    unusual = forms.BooleanField(label=_("Something unusual?"), required=False, help_text=_(
        "Amount, colour or consistency that isn't normal for this pet. Leave off when all looks fine."))
    show_when = {
        "container": ("location", [PottyBreak.LITTER_BOX]),
        "found_later": ("location", [PottyBreak.INSIDE]),
        "spot": ("location", [PottyBreak.INSIDE]),
        "context": ("location", [PottyBreak.INSIDE]),
        "pee_amount": ("unusual", [True]), "pee_colour": ("unusual", [True]),
        "poop_amount": ("unusual", [True]), "poop_consistency": ("unusual", [True]),
        "poop_colour": ("unusual", [True]),
    }
    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["pee", "poop"], "layout": "choices", "layout_attrs": {"label": _("Contents")}},
        {"fields": ["location"], "layout": "required"},
        {"fields": ["found_later"], "layout": "choices", "layout_attrs": {"label": _("Accident")}},
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
            "location": PillRadioSelect(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"container": _("Litter box")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["container"].queryset = self.fields["container"].queryset.filter(
            kind__in=[Container.LITTER_BOX, Container.AUTO_LITTER_BOX]
        )
        from .models import AccidentSpot

        self.fields["spot"].queryset = AccidentSpot.objects.filter(household=self.household)
        self.fields["spot"].widget = PillRadioSelect(choices=self.fields["spot"].choices)
        self.fields["spot"].empty_label = None
        if not self.fields["spot"].queryset.exists():
            self.fields.pop("spot")  # nothing named yet (Setup > Accident spots)
        self.fields["context"] = forms.MultipleChoiceField(
            label=_("What was going on"), choices=PottyBreak.CONTEXTS, required=False, widget=PillCheckboxSelect)
        if self.instance.pk:
            self.initial["context"] = self.instance.context
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
        # Only the locations the pets use (a dog never uses a litter box); the page script narrows it further to
        # the pets picked.
        pets = [self.instance.pet] if self.instance.pk else list(self.fields["pets"].queryset)
        self.location_map = {pet.pk: pet.allowed_potty_locations() for pet in pets}
        allowed = {loc for locs in self.location_map.values() for loc in locs} | {self.instance.location}
        self.fields["location"].choices = [(c, l) for c, l in PottyBreak.LOCATIONS if c in allowed]
        if not self.instance.pk and pets:
            self.initial.setdefault("location", self.location_map[pets[0].pk][0])
        if PottyBreak.LITTER_BOX not in allowed or not self.fields["container"].queryset.exists():
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


def link_walk(entry):
    """An outside potty break that happened during a walk belongs to it."""
    if entry.location != PottyBreak.OUTSIDE:
        entry.walk = None
        return
    entry.walk = (
        Walk.objects.filter(pets=entry.pet, start__lte=entry.time)
        .filter(Q(end__isnull=True) | Q(end__gte=entry.time))
        .order_by("-start")
        .first()
    )


def relink_walk(walk):
    """A walk logged or changed afterwards (back-dated, longer, other pets) takes in the potty breaks that now fall
    within it, and lets go of ones that no longer do (PUP-129). Same rule as link_walk."""
    end = walk.end or timezone.now()
    nearby = PottyBreak.everything.filter(
        Q(walk=walk) | Q(pet__in=walk.pets.all(), time__gte=walk.start, time__lte=end)).distinct()
    for entry in nearby:
        before = entry.walk_id
        link_walk(entry)
        if entry.walk_id != before:
            PottyBreak.everything.filter(pk=entry.pk).update(walk=entry.walk)


OTHER_WALKER = "other"


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
        self.fields["distance"].label = _("Distance")
        unit = self.default_unit("distance")
        self.initial["distance_unit"] = unit
        if self.instance.distance is not None:
            self.initial["distance"] = round(units.from_stored(self.instance.distance, unit, units.DISTANCE), 2)

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
        labels = {"container": _("Into"), "supply_item": _("From bag")}
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
                pet=pet, time=fill.time, fill=fill, logged_by=self.user, food=not water, water=water,
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


def full_bowl(pet, water=False, at=None):
    """What was in this pet's full food (or water) bowl: the bowl's latest fill in the day before `at`, else how
    much it holds when full. Returns (amount or None, bowl or None)."""
    kinds = [Container.WATER_BOWL, Container.FOUNTAIN] if water else [Container.FOOD_BOWL, Container.FEEDER]
    bowl = pet.containers.filter(kind__in=kinds, active=True).order_by("pk").first()
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


class MealForm(PerPetForm):
    unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS), required=False)
    water_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.VOLUME), required=False)
    show_when = {
        "portion": ("food", [True]), "amount": ("food", [True]), "unit": ("food", [True]),
        "product": ("food", [True]),
        "water_portion": ("water", [True]), "water_amount": ("water", [True]), "water_unit": ("water", [True]),
    }

    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["food", "water"], "layout": "choices", "layout_attrs": {"label": _("Had")}},
        {"fields": ["portion", "amount", "unit", "product", "water_portion", "water_amount", "water_unit", "notes"]},
    ]

    class Meta:
        model = Meal
        fields = ["pet", "time", "food", "water", "portion", "amount", "product", "water_portion", "water_amount",
                  "notes"]
        widgets = {"pet": PetRadioSelect, "time": DateTimeInput(), "notes": forms.Textarea(attrs={"rows": 3}),
                   "portion": PillRadioSelect(), "water_portion": PillRadioSelect()}
        labels = {"portion": _("Food: how much of the bowl"), "water_portion": _("Water: how much of the bowl"),
                  "amount": _("Or exact amount eaten"), "water_amount": _("Or exact amount drunk"),
                  "product": _("Food")}
        help_texts = {
            "portion": _("Of what was in the bowl (its last fill, or a full bowl)."),
            "amount": _("Optional. Wins over the portion."),
            "water_amount": _("Optional. Wins over the portion."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mass, volume = self.default_unit("mass"), self.default_unit("volume")
        self.fields["unit"].initial, self.fields["water_unit"].initial = mass, volume
        self.fields["product"].queryset = self.fields["product"].queryset.exclude(category=Product.LITTER)
        if self.instance.pk:
            if self.instance.amount is not None:
                self.initial["amount"] = round(units.from_stored(self.instance.amount, mass, units.MASS), 2)
            if self.instance.water_amount is not None:
                self.initial["water_amount"] = round(units.from_stored(self.instance.water_amount, volume, units.VOLUME), 1)
        else:
            self.initial["food"] = True
        for name in ("portion", "water_portion"):
            self.fields[name].widget.choices = [c for c in self.fields[name].choices if c[0] != ""]
        pets = [self.instance.pet] if self.instance.pk else (
            [p for p in [self.initial.get("pet")] if p] or list(self.initial.get("pets") or []))
        self.warnings = bowl_warnings(pets)

    def clean(self):
        data = super().clean()
        if not data.get("food") and not data.get("water"):
            raise forms.ValidationError(_("Pick food, water or both."))
        if data.get("amount") is not None:
            data["amount"] = units.to_stored(data["amount"], data.get("unit") or "g", units.MASS)
        if data.get("water_amount") is not None:
            data["water_amount"] = units.to_stored(data["water_amount"], data.get("water_unit") or "ml", units.VOLUME)
        if not data.get("food"):
            data["amount"], data["product"], data["portion"] = None, None, None
        if not data.get("water"):
            data["water_amount"], data["water_portion"] = None, None
        return data

    def before_save(self, meal):
        """A portion becomes an amount from this pet's bowl, unless an exact amount was given."""
        meal.amount, meal.water_amount = self.cleaned_data.get("amount"), self.cleaned_data.get("water_amount")
        for portion, field, water in (("portion", "amount", False), ("water_portion", "water_amount", True)):
            share = self.cleaned_data.get(portion)
            if share and getattr(meal, field) is None:
                full, _bowl = full_bowl(meal.pet, water, meal.time)
                if full:
                    setattr(meal, field, round(full * share, 1))

    def save(self, commit=True):
        meal = super().save(commit=False)
        if meal.pet_id:
            self.before_save(meal)
        if commit:
            meal.save()
        return meal


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
                  "pee_every", "poop_every", "after_meal", "picture", "active", "notes"]
        widgets = {"type": PillRadioSelect(), "sex": PillRadioSelect(), "birth_date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 3})}

    picture_crop = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_when = {"birth_date_estimated": ("birth_date", self.ANY)}
        self.fields["picture"].help_text = _("Drag and resize the box to frame your pet.")
        self.fields["after_meal"].required = False
        if self.instance.pk:
            self.initial["potty_locations"] = self.instance.allowed_potty_locations()
            if self.instance.picture_crop:
                import json

                self.initial["picture_crop"] = json.dumps(self.instance.picture_crop)

    def clean_after_meal(self):
        value = self.cleaned_data.get("after_meal")
        return 15 if value is None else value

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
        pet = super().save(commit=commit)
        if commit:
            crop = self.cleaned_data.get("picture_crop")
            if not pet.picture:
                crop = None
            elif crop is None and "picture" in self.changed_data:
                crop = _centered_square(pet)  # a new picture without a box: the middle square
            if crop != pet.picture_crop or "picture" in self.changed_data or (pet.picture_crop and not pet.picture_cropped):
                pet.picture_crop = crop if pet.picture else None
                pet.make_cropped_picture()
                pet.save(update_fields=["picture_crop", "picture_cropped"])
        return pet


def _centered_square(pet):
    from PIL import Image, ImageOps

    with pet.picture.open("rb") as handle:
        width, height = ImageOps.exif_transpose(Image.open(handle)).size
    side = min(width, height)
    return {"x": (width - side) / 2 / width, "y": (height - side) / 2 / height, "w": side / width, "h": side / height}


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


class ProductForm(HouseholdOwnedForm):
    size_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS))

    class Meta:
        model = Product
        fields = ["name", "brand", "category", "package_size", "reorder_days"]
        widgets = {"category": PillRadioSelect()}
        labels = {"package_size": _("Usual package size")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fieldsets = [{"fields": ["name", "brand", "category", "package_size", "size_unit", "reorder_days"],
                           "layout": "default"}]
        unit = units.unit_for(self.household, "bag")
        self.fields["size_unit"].initial = unit
        if self.instance.pk and self.instance.package_size:
            self.initial["package_size"] = round(units.from_stored(self.instance.package_size, unit, units.MASS), 2)

    def clean(self):
        data = super().clean()
        if data.get("package_size") is not None:
            data["package_size"] = units.to_stored(data["package_size"], data.get("size_unit") or "g", units.MASS)
        return data


class SupplyItemForm(PetsForm):
    size_unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS))

    class Meta:
        model = SupplyItem
        fields = ["product", "size", "opened", "finished", "price", "pets"]
        widgets = {"opened": DateInput(), "finished": DateInput(), "pets": PetCheckboxSelect}
        labels = {"size": _("Bag size")}
        help_texts = {"size": _("Leave empty to use the product's usual package size.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["size"].required = False
        self.fieldsets = [{"fields": ["product", "size", "size_unit", "opened", "finished", "price", "pets"],
                           "layout": "default"}]
        unit = units.unit_for(self.household, "bag")
        self.fields["size_unit"].initial = unit
        if not self.instance.pk:
            self.initial.setdefault("opened", timezone.localdate())
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
            self.add_error("size", _("Enter the bag size (the product has no usual size yet)."))
        return data


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
        from babybuddy import time_zones

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
