from django.forms import CheckboxSelectMultiple, RadioSelect


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
