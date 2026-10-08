"""REST API for the pet data (PUP-63), at /api/v1/.

Everything is scoped to the caller's household, with the same roles as the UI: anyone in the household can read,
owners and members can write, read-only members can't. Amounts use the stored units, whatever the household
displays: grams (food, litter), millilitres (water), kilograms (weight), centimetres (height, length) and metres
(walk distance). Walks and sleeps without an `end` are running timers; PATCH an `end` to stop one.
"""
from django.contrib.auth import get_user_model
from django.urls import include, path
from rest_framework import permissions, routers, serializers, viewsets

from .forms import link_walk
from .households import can_edit, current_household, is_owner
from .models import (
    AccidentSpot, Container, Fill, Household, Meal, Measurement, Medication, Note, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk,
)


class HouseholdRole(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        view.household = current_household(request.user)
        if request.method in permissions.SAFE_METHODS:
            return True
        if view.owner_only:
            return is_owner(request.user, view.household)
        return can_edit(request.user, view.household)


class HouseholdSerializer(serializers.ModelSerializer):
    """Choices for related fields are limited to the caller's household."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        view = self.context.get("view")
        household = getattr(view, "household", None)
        if household is None:
            return
        scopes = {
            Pet: Pet.objects.filter(household=household),
            Container: Container.objects.filter(household=household),
            Product: Product.objects.filter(household=household),
            SupplyItem: SupplyItem.objects.filter(product__household=household),
            Walk: Walk.objects.filter(household=household),
            Fill: Fill.objects.filter(container__household=household),
            AccidentSpot: AccidentSpot.objects.filter(household=household),
            get_user_model(): household.members.all(),
        }
        for field in self.fields.values():
            field = getattr(field, "child_relation", field)
            queryset = getattr(field, "queryset", None)
            if queryset is not None and queryset.model in scopes:
                field.queryset = scopes[queryset.model]
            elif queryset is not None and queryset.model._meta.app_label == "pets":
                field.queryset = queryset.none()  # fail closed: a new link must be scoped above


def serializer(model_class, exclude=("household",), read_only=("logged_by", "created")):
    present = {f.name for f in model_class._meta.get_fields()}
    meta = type("Meta", (), {
        "model": model_class,
        "exclude": [f for f in exclude if f in present],
        "read_only_fields": [f for f in read_only if f in present],
    })
    return type(f"{model_class.__name__}Serializer", (HouseholdSerializer,), {"Meta": meta})


class EntryViewSet(viewsets.ModelViewSet):
    permission_classes = [HouseholdRole]
    household_path = "household"
    owner_only = False
    filterset_fields = ()
    ordering = "-pk"

    def get_queryset(self):
        return self.queryset.filter(**{self.household_path: self.household})

    def perform_create(self, serializer):
        extra = {}
        model = serializer.Meta.model
        names = {f.name for f in model._meta.get_fields()}
        if "household" in names:
            extra["household"] = self.household
        if "logged_by" in names:
            extra["logged_by"] = self.request.user
        data = serializer.validated_data
        if model is Fill:
            extra["contents"] = contents_for(data["container"])
            if data.get("supply_item") is None and extra["contents"] != Fill.WATER:
                extra["supply_item"] = data["container"].default_supply
        entry = serializer.save(**extra)
        if model is PottyBreak and "walk" not in data:
            link_walk(entry)  # like the form: an outside break during a walk belongs to it
            entry.save()

    def perform_update(self, serializer):
        if serializer.Meta.model is Fill and "container" in serializer.validated_data:
            serializer.save(contents=contents_for(serializer.validated_data["container"]))
        else:
            serializer.save()


def contents_for(container):
    if container.holds_water:
        return Fill.WATER
    if container.kind in (Container.LITTER_BOX, Container.AUTO_LITTER_BOX):
        return Fill.LITTER
    return Fill.FOOD


def viewset(model, household_path, ordering, filters, serializer_class=None, **attrs):
    return type(f"{model.__name__}ViewSet", (EntryViewSet,), {
        "queryset": model.objects.all(),
        "serializer_class": serializer_class or serializer(model),
        "household_path": household_path,
        "ordering": ordering,
        "ordering_fields": [ordering.lstrip("-")],
        "filterset_fields": filters,
        **attrs,
    })


class HouseholdViewSet(EntryViewSet):
    """The caller's household. Only owners may change it."""

    queryset = Household.objects.all()
    serializer_class = serializer(Household, exclude=("members",), read_only=("created",))
    household_path = "pk"
    owner_only = True
    ordering = "name"
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return self.queryset.filter(pk=self.household.pk)


router = routers.DefaultRouter()
router.register("households", HouseholdViewSet)
router.register("pets", viewset(Pet, "household", "name", ("type", "active", "slug")))
router.register("potty-breaks", viewset(PottyBreak, "pet__household", "-time", ("pet", "location", "walk", "pee", "poop")))
router.register("walks", viewset(Walk, "household", "-start", ("pets", "walker")))
router.register("sleeps", viewset(Sleep, "pet__household", "-start", ("pet", "kind")))
router.register("fills", viewset(Fill, "container__household", "-time", ("container", "contents", "supply_item"),
                                 serializer_class=serializer(Fill, read_only=("logged_by", "created", "contents"))))
router.register("meals", viewset(Meal, "pet__household", "-time", ("pet", "product")))
router.register("measurements", viewset(Measurement, "pet__household", "-date", ("pet", "kind")))
router.register("medications", viewset(Medication, "pet__household", "-time", ("pet",)))
router.register("notes", viewset(Note, "household", "-time", ("pet",)))
router.register("containers", viewset(Container, "household", "name", ("kind", "active")))
router.register("products", viewset(Product, "household", "name", ("category",)))
router.register("supplies", viewset(SupplyItem, "product__household", "-pk", ("product", "finished")))

urlpatterns = [path("api/v1/", include((router.urls, "pets-api"), namespace="pets-api"))]
