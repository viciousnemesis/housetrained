from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from babybuddy.widgets import DateInput, DateTimeInput
from core.widgets import PillRadioSelect

from . import units
from .models import Container, Fill, Household, Meal, Measurement, Note, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk
from .widgets import PetCheckboxSelect, PetRadioSelect


class PetsForm(forms.ModelForm):
    """Scopes choices to the household and renders with Baby Buddy's fieldset layouts."""

    fieldsets = []

    def __init__(self, *args, household, user, **kwargs):
        self.household, self.user = household, user
        super().__init__(*args, **kwargs)
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
                "layout": fs.get("layout", "default"),
                "layout_attrs": fs.get("layout_attrs", {}),
                "fields": [bound[name] for name in fs["fields"] if name in bound],
            }
            for fs in self.fieldsets
        ]

    def default_unit(self, kind):
        return units.DEFAULTS[self.household.units][kind]


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


class PottyBreakForm(PerPetForm):
    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["pee", "poop"], "layout": "choices", "layout_attrs": {"label": _("Contents")}},
        {"fields": ["location"], "layout": "required"},
        {"fields": ["container", "notes"], "layout": "advanced"},
    ]

    class Meta:
        model = PottyBreak
        fields = ["pet", "time", "pee", "poop", "location", "container", "notes"]
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
        if not self.instance.pk:
            self.initial.setdefault("pee", True)

    def clean(self):
        data = super().clean()
        if not data.get("pee") and not data.get("poop"):
            raise forms.ValidationError(_("Pick pee, poop or both."))
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


class WalkForm(PetsForm):
    distance_unit = forms.ChoiceField(label=_("Distance unit"), choices=units.choices(units.DISTANCE))

    fieldsets = [
        {"fields": ["pets", "start", "end"], "layout": "required"},
        {"fields": ["walker", "walker_name"]},
        {"fields": ["distance", "distance_unit", "notes"], "layout": "advanced"},
    ]

    class Meta:
        model = Walk
        fields = ["pets", "start", "end", "walker", "walker_name", "distance", "notes"]
        widgets = {
            "pets": PetCheckboxSelect,
            "start": DateTimeInput(),
            "end": DateTimeInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["walker"].label_from_instance = lambda u: u.first_name or u.username
        self.fields["distance"].label = _("Distance")
        unit = self.default_unit("distance")
        self.initial["distance_unit"] = unit
        if self.instance.distance is not None:
            self.initial["distance"] = round(units.from_stored(self.instance.distance, unit, units.DISTANCE), 2)
        if not self.instance.pk:
            self.initial.setdefault("walker", self.user)

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError(_("The walk can't end before it starts."))
        if data.get("distance") is not None:
            data["distance"] = units.to_stored(data["distance"], data.get("distance_unit") or "m", units.DISTANCE)
        return data

    def save(self, commit=True):
        walk = super().save(commit=False)
        walk.household = self.household
        if not walk.pk:
            walk.logged_by = self.user
        if commit:
            walk.save()
            self.save_m2m()
        return walk


class SleepForm(PerPetForm):
    fieldsets = [
        {"fields": ["pets", "pet", "start", "end", "kind"], "layout": "required"},
        {"fields": ["notes"], "layout": "advanced"},
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


class FillForm(PetsForm):
    unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS) + units.choices(units.VOLUME))

    fieldsets = [
        {"fields": ["container", "time"], "layout": "required"},
        {"fields": ["amount", "unit", "bowls"]},
        {"fields": ["supply_item", "notes"], "layout": "advanced"},
    ]

    class Meta:
        model = Fill
        fields = ["container", "time", "amount", "bowls", "supply_item", "notes"]
        widgets = {
            "time": DateTimeInput(),
            "container": PillRadioSelect(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"container": _("Into"), "supply_item": _("From bag")}
        help_texts = {
            "amount": _("Enter an amount or a number of bowls."),
            "bowls": _("Decimals allowed, e.g. 0.5."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["container"].label_from_instance = container_label
        self.fields["unit"].initial = self.default_unit("mass")
        if self.instance.pk and self.instance.amount is not None:
            table, kind = (units.VOLUME, "volume") if self.instance.contents == Fill.WATER else (units.MASS, "mass")
            unit = self.default_unit(kind)
            self.initial["unit"] = unit
            self.initial["amount"] = round(units.from_stored(self.instance.amount, unit, table), 2)
            if self.instance.bowls is not None:
                self.initial["amount"] = None
        if not self.instance.pk:
            containers = self.fields["container"].queryset
            if containers.count() == 1:
                self.initial.setdefault("container", containers.first())

    def clean(self):
        data = super().clean()
        container, amount, bowls, unit = data.get("container"), data.get("amount"), data.get("bowls"), data.get("unit")
        if container is None:
            return data
        if (amount is None) == (bowls is None):
            raise forms.ValidationError(_("Enter either an amount or a number of bowls."))
        water = container.kind in (Container.WATER_BOWL, Container.FOUNTAIN)
        if amount is not None:
            table = units.VOLUME if water else units.MASS
            if unit not in table:
                raise forms.ValidationError(
                    _("Use %(units)s for this container.") % {"units": ", ".join(table)}
                )
            data["amount"] = units.to_stored(amount, unit, table)
        elif container.amount_per_bowl:
            data["amount"] = bowls * container.amount_per_bowl
        else:
            data["amount"] = None
        return data

    def save(self, commit=True):
        fill = super().save(commit=False)
        fill.amount = self.cleaned_data["amount"]
        water = fill.container.kind in (Container.WATER_BOWL, Container.FOUNTAIN)
        litter = fill.container.kind in (Container.LITTER_BOX, Container.AUTO_LITTER_BOX)
        fill.contents = Fill.WATER if water else Fill.LITTER if litter else Fill.FOOD
        if not fill.pk:
            fill.logged_by = self.user
            if fill.supply_item is None and not water:
                fill.supply_item = fill.container.default_supply
        if commit:
            fill.save()
        return fill

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


class MealForm(PerPetForm):
    unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.MASS), required=False)

    fieldsets = [
        {"fields": ["pets", "pet", "time"], "layout": "required"},
        {"fields": ["product", "amount", "unit"]},
        {"fields": ["notes"], "layout": "advanced"},
    ]

    class Meta:
        model = Meal
        fields = ["pet", "time", "product", "amount", "notes"]
        widgets = {"pet": PetRadioSelect, "time": DateTimeInput(), "notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"amount": _("Amount eaten")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unit = self.default_unit("mass")
        self.fields["unit"].initial = unit
        if self.instance.pk and self.instance.amount is not None:
            self.initial["amount"] = round(units.from_stored(self.instance.amount, unit, units.MASS), 2)

    def clean(self):
        data = super().clean()
        if data.get("amount") is not None:
            data["amount"] = units.to_stored(data["amount"], data.get("unit") or "g", units.MASS)
        return data

    def save(self, commit=True):
        meal = super().save(commit=False)
        meal.amount = self.cleaned_data.get("amount")
        if commit:
            meal.save()
        return meal


class MeasurementForm(PerPetForm):
    unit = forms.ChoiceField(label=_("Unit"), choices=units.choices(units.WEIGHT) + units.choices(units.LENGTH))

    fieldsets = [
        {"fields": ["pets", "pet", "date", "kind", "value", "unit"], "layout": "required"},
        {"fields": ["notes"], "layout": "advanced"},
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
        self.fields["unit"].initial = self.default_unit("weight")
        if self.instance.pk:
            weight = self.instance.kind == Measurement.WEIGHT
            unit = self.default_unit("weight" if weight else "length")
            self.initial["unit"] = unit
            self.initial["value"] = round(
                units.from_stored(self.instance.value, unit, units.WEIGHT if weight else units.LENGTH), 2
            )

    def clean(self):
        data = super().clean()
        kind, value, unit = data.get("kind"), data.get("value"), data.get("unit")
        if kind and value is not None:
            table = units.WEIGHT if kind == Measurement.WEIGHT else units.LENGTH
            if unit not in table:
                raise forms.ValidationError(_("Use %(units)s for this measurement.") % {"units": ", ".join(table)})
            data["value"] = units.to_stored(value, unit, table)
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
    class Meta:
        model = Pet
        fields = ["name", "type", "breed", "sex", "birth_date", "birth_date_estimated", "picture", "active", "notes"]
        widgets = {"type": PillRadioSelect(), "sex": PillRadioSelect(), "birth_date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 3})}


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
        unit = "lb" if self.household.units == "imperial" else "kg"
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
        unit = "lb" if self.household.units == "imperial" else "kg"
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
    class Meta:
        model = Household
        fields = ["name", "units", "time_zone"]
        widgets = {"units": PillRadioSelect()}
        help_texts = {"units": _("Metric shows g, kg, ml, cm; imperial shows oz, lb, fl oz, in.")}
