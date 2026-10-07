from django.forms import CheckboxSelectMultiple

from core.widgets import ChildRadioSelect


class PetRadioSelect(ChildRadioSelect):
    """One pet, picked by photo button (same look as Baby Buddy's child picker)."""


class PetCheckboxSelect(CheckboxSelectMultiple):
    """Several pets, picked by photo buttons."""

    template_name = "core/child_radio.html"
    option_template_name = "core/child_radio_option.html"

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = attrs.get("class", "") + " btn-check d-none"
        return attrs

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value != "":
            option["picture"] = value.instance.picture
        return option


class PillCheckboxSelect(CheckboxSelectMultiple):
    """Several choices, as toggle pills (the multi-select twin of Baby Buddy's PillRadioSelect)."""

    template_name = "core/pill_radio.html"
    option_template_name = "core/pill_radio_option.html"

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = attrs.get("class", "") + " btn-check d-none"
        return attrs
