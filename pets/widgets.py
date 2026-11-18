from django.forms import CheckboxSelectMultiple, ClearableFileInput, MultiWidget, RadioSelect, Select


class _ButtonInputs:
    """Inputs drawn as buttons: the real radio or checkbox is hidden behind its label. With `addable` (the name of the
    form's text field for it), a "+ New" pill lets you type a choice that isn't there yet (PUP-180)."""

    def __init__(self, attrs=None, choices=(), addable=None):
        super().__init__(attrs, choices)
        self.addable = addable

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["addable"] = self.addable
        return context

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = attrs.get("class", "") + " btn-check d-none"
        return attrs


class _PetOptions(_ButtonInputs):
    template_name = "pets/widgets/button_group.html"
    option_template_name = "pets/widgets/pet_option.html"

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value != "":
            option["picture"] = value.instance.card_picture
            option["pet_type"] = value.instance.type
        return option


class PetRadioSelect(_PetOptions, RadioSelect):
    """One pet, picked by photo button."""


class PetCheckboxSelect(_PetOptions, CheckboxSelectMultiple):
    """Several pets, picked by photo buttons."""


class PillRadioSelect(_ButtonInputs, RadioSelect):
    """One choice, as a row of pills."""

    template_name = "pets/widgets/pill_group.html"
    option_template_name = "pets/widgets/pill_option.html"


class PillCheckboxSelect(_ButtonInputs, CheckboxSelectMultiple):
    """Several choices, as toggle pills."""

    template_name = "pets/widgets/pill_group.html"
    option_template_name = "pets/widgets/pill_option.html"


class DurationSelect(MultiWidget):
    """Minutes picked as hours and minutes (PUP-205), instead of one long list. Both blank: no value."""

    template_name = "pets/widgets/duration.html"

    def __init__(self, attrs=None):
        attrs = {"class": "form-select w-auto", **(attrs or {})}
        hours = [("", "–")] + [(h, h) for h in range(13)]
        minutes = [("", "–")] + [(m, f"{m:02d}") for m in range(0, 60, 15)]
        super().__init__([Select(attrs, hours), Select(attrs, minutes)])

    def decompress(self, value):
        if value in (None, ""):
            return ["", ""]
        return [int(value) // 60, int(value) % 60]

    def value_from_datadict(self, data, files, name):
        if name in data:  # one number of minutes, as the API and older pages send it
            return data.get(name)
        hours, minutes = super().value_from_datadict(data, files, name)
        if hours in ("", None) and minutes in ("", None):
            return ""
        return str(int(hours or 0) * 60 + int(minutes or 0))


class PictureInput(ClearableFileInput):
    """The photo field without the stored file's path: the photo itself shows in the framing box (PUP-205)."""

    template_name = "pets/widgets/picture.html"
