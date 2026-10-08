import datetime
from dataclasses import dataclass

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.core.paginator import Paginator
from django.views.generic import TemplateView

from babybuddy.mixins import LoginRequiredMixin

from . import forms
from .households import add_member, can_edit, current_household, is_owner
from .models import Container, Fill, Meal, Measurement, Membership, Note, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk
from .entries import KINDS, LIST_TITLES, day_timeline, for_pet, recent_activity, row


@dataclass
class Entry:
    model: type
    form: type
    title: str
    household_path: str  # lookup from the entry to its household


ENTRIES = {
    "potty": Entry(PottyBreak, forms.PottyBreakForm, _("Potty break"), "pet__household"),
    "walk": Entry(Walk, forms.WalkForm, _("Walk"), "household"),
    "sleep": Entry(Sleep, forms.SleepForm, _("Sleep"), "pet__household"),
    "fill": Entry(Fill, forms.FillForm, _("Fill a bowl"), "container__household"),
    "meal": Entry(Meal, forms.MealForm, _("Meal"), "pet__household"),
    "measurement": Entry(Measurement, forms.MeasurementForm, _("Measurement"), "pet__household"),
    "note": Entry(Note, forms.NoteForm, _("Note"), "household"),
    # Setup
    "pet": Entry(Pet, forms.PetForm, _("Pet"), "household"),
    "container": Entry(Container, forms.ContainerForm, _("Bowl, feeder or litter box"), "household"),
    "product": Entry(Product, forms.ProductForm, _("Product"), "household"),
    "supply": Entry(SupplyItem, forms.SupplyItemForm, _("Bag"), "product__household"),
}
SETUP_KINDS = ("pet", "container", "product", "supply")


class HouseholdMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.household = current_household(request.user)
        return super().dispatch(request, *args, **kwargs)

    def require(self, action, model=None):
        """Anyone in the household may view; adding, changing and deleting needs an owner or member."""
        if action != "view" and not can_edit(self.request.user, self.household):
            raise PermissionDenied

    def back_url(self, pets=()):
        target = self.request.POST.get("next") or self.request.GET.get("next")
        if target and url_has_allowed_host_and_scheme(target, {self.request.get_host()}):
            return target
        if self.kwargs.get("kind") in SETUP_KINDS:
            return reverse("pets:setup")
        pets = list(pets)
        if len(pets) == 1:
            return reverse("pets:pet", args=[pets[0].slug])
        return reverse("pets:household")


def entry_pets(entry):
    if isinstance(entry, Pet):
        return [entry]
    if isinstance(entry, Product):
        return []
    if hasattr(entry, "pet"):
        return [entry.pet] if entry.pet_id else []
    if hasattr(entry, "pets"):
        return list(entry.pets.all())
    return list(entry.container.pets.all())


class HouseholdDashboard(HouseholdMixin, TemplateView):
    template_name = "pets/household.html"

    def get_context_data(self, **kwargs):
        from core.models import Child

        from .context_processors import baby_mode_on

        return super().get_context_data(
            pets=self.household.pets.filter(active=True), household=self.household,
            babies=Child.objects.order_by("birth_date") if baby_mode_on() else [], **kwargs
        )


class HouseholdEdit(HouseholdMixin, View):
    def check(self):
        if not is_owner(self.request.user, self.household):
            raise PermissionDenied

    def render_form(self, form):
        return render(self.request, "pets/form.html", {"form": form, "title": _("Household"), "kind": "household"})

    def get(self, request):
        self.check()
        form = forms.HouseholdForm(instance=self.household)
        form.hydrated_fielsets = [{"layout": "default", "layout_attrs": {}, "fields": list(form)}]
        return self.render_form(form)

    def post(self, request):
        self.check()
        form = forms.HouseholdForm(request.POST, instance=self.household)
        if form.is_valid():
            form.save()
            messages.success(request, _("Household saved."))
            return HttpResponseRedirect(reverse("pets:setup"))
        form.hydrated_fielsets = [{"layout": "default", "layout_attrs": {}, "fields": list(form)}]
        return self.render_form(form)


class Members(HouseholdMixin, TemplateView):
    """Owners add people to the household and set their role."""

    template_name = "pets/members.html"

    def get(self, request, *args, **kwargs):
        if not is_owner(request.user, self.household):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model

        members = self.household.memberships.select_related("user").order_by("user__username")
        # Only site admins see other accounts and can add them: owners can't pull strangers into a household.
        others = get_user_model().objects.none()
        if self.request.user.is_superuser:
            others = get_user_model().objects.filter(is_active=True).exclude(memberships__household=self.household)
        return super().get_context_data(
            household=self.household, members=members, others=others.order_by("username"),
            roles=Membership._meta.get_field("role").choices, **kwargs
        )

    def post(self, request):
        from django.contrib.auth import get_user_model

        if not is_owner(request.user, self.household):
            raise PermissionDenied
        roles = dict(Membership._meta.get_field("role").choices)
        user = get_object_or_404(get_user_model(), pk=request.POST.get("user"))
        new_role = request.POST.get("role")
        if not request.user.is_superuser and not self.household.memberships.filter(user=user).exists():
            raise PermissionDenied  # adding people is for site admins
        owners = self.household.memberships.filter(role=Membership.OWNER).exclude(user=user)
        if new_role != Membership.OWNER and not owners.exists():
            messages.error(request, _("A household needs at least one owner."))
        elif new_role == "remove":
            self.household.memberships.filter(user=user).delete()
            messages.success(request, _("%(name)s removed.") % {"name": user.get_username()})
        elif new_role in roles:
            add_member(self.household, user, new_role)
            messages.success(request, _("%(name)s is now %(role)s.") % {"name": user.get_username(), "role": roles[new_role]})
        return HttpResponseRedirect(reverse("pets:members"))


class Import(HouseholdMixin, View):
    """Setup > Import: CSV upload, preview, then import (PUP-35)."""

    def get(self, request):
        from . import importer

        self.require("add")
        kind = request.GET.get("template")
        if kind in importer.KINDS:
            response = HttpResponse(importer.template_csv(kind), content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="housetrained-{kind}.csv"'
            return response
        return self.page({"kind": request.GET.get("kind", "potty")})

    def page(self, context):
        from . import importer

        return render(self.request, "pets/import.html", {"kinds": importer.KINDS, "household": self.household,
                                                          **context})

    def post(self, request):
        from . import importer

        self.require("add")
        kind = request.POST.get("kind")
        if kind not in importer.KINDS:
            raise Http404
        upload = request.FILES.get("file")
        if upload:
            if upload.size > importer.MAX_BYTES:
                return self.page({"kind": kind, "problems": [_("The file is too big (2 MB at most).")]})
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                return self.page({"kind": kind, "problems": [_("Save the file as CSV (UTF-8) and try again.")]})
        else:
            text = request.POST.get("csv_text", "")
        job = importer.Importer(self.household, request.user, kind)
        rows, problems = job.read(text)
        errors = [r for r in rows if r.errors]
        new = [r for r in rows if not r.errors and not r.duplicate]
        if request.POST.get("confirm") and rows and not errors and not problems:
            saved = job.save(rows)
            messages.success(request, _("Imported %(saved)d entries; %(skipped)d already logged.")
                             % {"saved": saved, "skipped": len(rows) - len(new)})
            return HttpResponseRedirect(reverse("pets:list", args=[kind]))
        return self.page({"kind": kind, "rows": rows, "problems": problems, "errors": errors, "new": new,
                          "csv_text": text, "columns": importer.KINDS[kind].columns})


class Notifications(HouseholdMixin, View):
    """Notifications (PUP-58): turn them on per device, choose reminders, send a test."""

    def get(self, request):
        from . import push
        from .models import NotificationPreference

        prefs, _created = NotificationPreference.objects.get_or_create(user=request.user)
        return render(request, "pets/notifications.html", {
            "prefs": prefs, "public_key": push.public_key(),
            "devices": request.user.push_subscriptions.order_by("-created"),
        })

    def post(self, request):
        import json

        from django.http import JsonResponse

        from . import push
        from .models import NotificationPreference, PushSubscription

        action = request.POST.get("action") or request.GET.get("action")
        if action == "subscribe":
            data = json.loads(request.body or "{}")
            keys = data.get("keys") or {}
            endpoint = data.get("endpoint", "")
            if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
                return JsonResponse({"ok": False}, status=400)
            PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={
                "user": request.user, "p256dh": keys["p256dh"], "auth": keys["auth"],
                "device": (data.get("device") or request.headers.get("User-Agent", ""))[:255],
            })
            return JsonResponse({"ok": True})
        if action == "unsubscribe":
            data = json.loads(request.body or "{}")
            request.user.push_subscriptions.filter(endpoint=data.get("endpoint", "")).delete()
            return JsonResponse({"ok": True})
        if action == "remove":
            request.user.push_subscriptions.filter(pk=request.POST.get("device")).delete()
            messages.success(request, _("Device removed."))
        elif action == "test":
            count = push.send(request.user, _("Housetrained"), _("Notifications work on this device."), url="/pets/")
            if count:
                messages.success(request, _("Test sent to %(count)d device(s).") % {"count": count})
            else:
                messages.warning(request, _("No device received it. Turn notifications on first."))
        elif action == "prefs":
            prefs, _created = NotificationPreference.objects.get_or_create(user=request.user)
            prefs.walk_due = bool(request.POST.get("walk_due"))
            prefs.low_supply = bool(request.POST.get("low_supply"))
            prefs.save()
            messages.success(request, _("Saved."))
        return HttpResponseRedirect(reverse("pets:notifications"))


def service_worker(request):
    """The push service worker, served from the site root so it covers every page."""
    from django.template.loader import render_to_string

    response = HttpResponse(render_to_string("pets/sw.js"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


class Setup(HouseholdMixin, TemplateView):
    template_name = "pets/setup.html"

    @staticmethod
    def babies():
        from core.models import Child

        from .context_processors import baby_mode_on

        return Child.objects.order_by("birth_date") if baby_mode_on() else None

    def get_context_data(self, **kwargs):
        from .supplies import display_unit, status

        unit = display_unit(self.household)
        bags = SupplyItem.objects.filter(product__household=self.household).select_related("product").prefetch_related("pets")
        return super().get_context_data(
            household=self.household,
            pets=self.household.pets.all(),
            babies=self.babies(),
            containers=self.household.containers.prefetch_related("pets"),
            products=self.household.products.all(),
            bags=[status(b, unit) for b in bags],
            **kwargs,
        )


class PetDashboard(HouseholdMixin, TemplateView):
    template_name = "pets/pet.html"

    def get_context_data(self, **kwargs):
        pet = get_object_or_404(Pet, household=self.household, slug=self.kwargs["slug"])
        more = [(kind, ENTRIES[kind].title) for kind in ("walk", "sleep", "meal", "measurement", "note")]
        return super().get_context_data(pet=pet, recent=recent_activity(pet), more_entries=more, **kwargs)


class EntryList(HouseholdMixin, TemplateView):
    template_name = "pets/list.html"

    def get_context_data(self, **kwargs):
        kind = self.kwargs["kind"]
        if kind not in KINDS:
            raise Http404
        self.require("view", ENTRIES[kind].model)
        field, _summary, queryset = KINDS[kind]
        entries = queryset(self.household)
        pet = self.household.pets.filter(slug=self.request.GET.get("pet")).first()
        if pet:
            entries = for_pet(kind, entries, pet)
        page = Paginator(entries.order_by(f"-{field}"), 50).get_page(self.request.GET.get("page"))
        return super().get_context_data(
            title=LIST_TITLES[kind], page=page, rows=[row(kind, e) for e in page], pet=pet,
            pets=self.household.pets.filter(active=True), kinds=LIST_TITLES.items(), **kwargs,
        )


class Timeline(HouseholdMixin, TemplateView):
    template_name = "pets/timeline.html"

    def get_context_data(self, **kwargs):
        try:
            date = datetime.date.fromisoformat(self.request.GET.get("date", ""))
        except ValueError:
            date = timezone.localdate()
        today = timezone.localdate()
        return super().get_context_data(
            date=date, rows=day_timeline(self.household, date),
            previous=date - datetime.timedelta(days=1),
            next=date + datetime.timedelta(days=1) if date < today else None, **kwargs,
        )


class EntryView(HouseholdMixin, View):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        if kwargs["kind"] not in ENTRIES:
            raise Http404
        self.kind = kwargs["kind"]
        self.entry = ENTRIES[self.kind]

    def get_instance(self):
        return get_object_or_404(
            self.entry.model, pk=self.kwargs["pk"], **{self.entry.household_path: self.household}
        )

    def render_form(self, form, instance=None):
        return render(self.request, "pets/form.html", {
            "form": form, "kind": self.kind, "title": self.entry.title, "object": instance,
            "ended": bool(self.request.GET.get("ended")), "next": self.request.GET.get("next", ""),
            "uncalibrated_warning": self.kind == "fill",
        })


class EntryAdd(EntryView):
    def initial(self):
        initial = {}
        pet = self.household.pets.filter(slug=self.request.GET.get("pet"), active=True).first()
        chosen = [pet] if pet else list(self.household.pets.filter(active=True))
        initial["pets"] = chosen
        if pet:
            initial["pet"] = pet
        return initial

    def get(self, request, *args, **kwargs):
        self.require("add", self.entry.model)
        form = self.entry.form(household=self.household, user=request.user, initial=self.initial())
        return self.render_form(form)

    def post(self, request, *args, **kwargs):
        self.require("add", self.entry.model)
        form = self.entry.form(request.POST, request.FILES, household=self.household, user=request.user)
        if not form.is_valid():
            return self.render_form(form)
        saved = form.save_entries() if hasattr(form, "save_entries") else [form.save()]
        if getattr(form, "uncalibrated_bowls", False):
            messages.warning(request, _(
                "Saved, but this bowl isn't calibrated, so the amount is unknown and food tracking will be rough. "
                "Calibrate it: tare the bowl, fill it as usual, and enter the weight."
            ))
        elif getattr(form, "meals_logged", None):
            names = ", ".join(pet.name for pet in form.meals_logged)
            messages.success(request, _("Bowl filled, and a meal logged for %(names)s.") % {"names": names})
        else:
            messages.success(request, _("%(title)s saved.") % {"title": self.entry.title})
        pets = {pet for entry in saved for pet in entry_pets(entry)}
        return HttpResponseRedirect(self.back_url(pets))


class EntryEdit(EntryView):
    def get(self, request, *args, **kwargs):
        self.require("change", self.entry.model)
        instance = self.get_instance()
        return self.render_form(self.entry.form(instance=instance, household=self.household, user=request.user), instance)

    def post(self, request, *args, **kwargs):
        self.require("change", self.entry.model)
        instance = self.get_instance()
        form = self.entry.form(request.POST, request.FILES, instance=instance, household=self.household, user=request.user)
        if not form.is_valid():
            return self.render_form(form, instance)
        entry = form.save()
        messages.success(request, _("%(title)s saved.") % {"title": self.entry.title})
        return HttpResponseRedirect(self.back_url(entry_pets(entry)))


class EntryDelete(EntryView):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        self.require("delete", self.entry.model)
        instance = self.get_instance()
        pets = entry_pets(instance)
        instance.delete()
        messages.success(request, _("%(title)s deleted.") % {"title": self.entry.title})
        return HttpResponseRedirect(self.back_url(pets))


# --- Walks and sleeps as running timers -------------------------------------------------------
def running_sleeps(household, sleep):
    """Sleeps started together for several pets run and end as one."""
    return Sleep.objects.filter(pet__household=household, start=sleep.start, end=None)


class ActivityStart(HouseholdMixin, View):
    http_method_names = ["post"]

    def post(self, request, kind):
        pet = self.household.pets.filter(slug=request.POST.get("pet"), active=True).first()
        pets = [pet] if pet else list(self.household.pets.filter(active=True))
        start = timezone.now().replace(microsecond=0)
        if kind == "walk":
            self.require("add", Walk)
            walk = Walk.objects.create(household=self.household, start=start, walker=request.user, logged_by=request.user)
            walk.pets.set(pets)
        elif kind == "sleep":
            self.require("add", Sleep)
            for p in pets:
                if not p.sleeps.filter(end=None).exists():
                    Sleep.objects.create(pet=p, start=start, logged_by=request.user)
        else:
            raise Http404
        return HttpResponseRedirect(self.back_url(pets))


class ActivityStop(HouseholdMixin, View):
    """End (save) or cancel (discard) a running walk or sleep."""

    http_method_names = ["post"]

    def post(self, request, kind, pk, action):
        if kind == "walk":
            walk = get_object_or_404(Walk, pk=pk, household=self.household, end=None)
            pets = list(walk.pets.all())
            if action == "cancel":
                self.require("delete", Walk)
                walk.delete()
                return HttpResponseRedirect(self.back_url(pets))
            self.require("change", Walk)
            walk.end = timezone.now()
            walk.save()
            return HttpResponseRedirect(reverse("pets:edit", args=["walk", walk.pk]) + "?ended=1")
        if kind == "sleep":
            sleep = get_object_or_404(Sleep, pk=pk, pet__household=self.household, end=None)
            group = list(running_sleeps(self.household, sleep).select_related("pet"))
            pets = [s.pet for s in group]
            if action == "cancel":
                self.require("delete", Sleep)
                for s in group:
                    s.delete()
                return HttpResponseRedirect(self.back_url(pets))
            self.require("change", Sleep)
            end = timezone.now()
            for s in group:
                s.end = end
                s.save()
            if len(group) == 1:
                return HttpResponseRedirect(reverse("pets:edit", args=["sleep", sleep.pk]) + "?ended=1")
            messages.success(request, _("Sleep saved for %(names)s.") % {"names": ", ".join(p.name for p in pets)})
            return HttpResponseRedirect(self.back_url(pets))
        raise Http404
