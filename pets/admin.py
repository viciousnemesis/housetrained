from django.contrib import admin

from . import models


class MembershipInline(admin.TabularInline):
    model = models.Membership
    extra = 0


@admin.register(models.Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ["name", "units"]
    inlines = [MembershipInline]


@admin.register(models.Pet)
class PetAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "household", "birth_date", "active"]
    list_filter = ["type", "active", "household"]


@admin.register(models.Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "household", "amount_per_bowl", "active"]
    list_filter = ["kind", "household"]


@admin.register(models.Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "category", "package_size", "household"]
    list_filter = ["category", "household"]


@admin.register(models.SupplyItem)
class SupplyItemAdmin(admin.ModelAdmin):
    list_display = ["product", "size", "opened", "finished", "price"]


for model, fields in (
    (models.Fill, ["time", "container", "contents", "amount", "bowls", "supply_item"]),
    (models.Walk, ["start", "end", "walker", "walker_name"]),
    (models.PottyBreak, ["time", "pet", "pee", "poop", "location", "walk"]),
    (models.Sleep, ["start", "end", "pet", "kind"]),
    (models.Meal, ["time", "pet", "product", "amount"]),
    (models.Measurement, ["date", "pet", "kind", "value"]),
    (models.Medication, ["time", "pet", "name", "dose"]),
    (models.Note, ["time", "pet", "text"]),
):
    admin.site.register(model, list_display=fields)
