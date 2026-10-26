import datetime
from dataclasses import dataclass

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.core.paginator import Paginator
from django.views.generic import TemplateView

from housetrained.mixins import LoginRequiredMixin

from . import forms
from .households import add_member, can_edit, current_household, is_owner
from .models import (
    AccidentContext, AccidentSpot, Command, Container, Fill, Meal, Measurement, Membership, Note, Pet, PottyBreak,
    PottyLocation, Product, Sleep, SupplyItem, TrainingLog, Walk,
)
from . import undo
from .entries import KINDS, LIST_TITLES, activity, for_pet, row


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
    "training": Entry(TrainingLog, forms.TrainingLogForm, _("Training"), "command__pet__household"),
    "command": Entry(Command, forms.CommandForm, _("Command"), "pet__household"),
    # Setup
    "pet": Entry(Pet, forms.PetForm, _("Pet"), "household"),
    "container": Entry(Container, forms.ContainerForm, _("Bowl, feeder or litter box"), "household"),
    "product": Entry(Product, forms.ProductForm, _("Product"), "household"),
    "supply": Entry(SupplyItem, forms.SupplyItemForm, _("Bag"), "product__household"),
    "spot": Entry(AccidentSpot, forms.AccidentSpotForm, _("Accident spot"), "household"),
    "context": Entry(AccidentContext, forms.AccidentContextForm, _("What was going on"), "household"),
    "location": Entry(PottyLocation, forms.PottyLocationForm, _("Potty location"), "household"),
}
SETUP_KINDS = ("pet", "container", "product", "supply", "spot", "context", "location")


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


def entry_household(entry):
    """The household an entry belongs to, by its kind's path (e.g. pet__household)."""
    for kind in ENTRIES.values():
        if isinstance(entry, kind.model):
            for part in kind.household_path.split("__"):
                entry = getattr(entry, part)
            return entry
    return None


# Kinds whose "saved" banner offers Undo (PUP-151): the things logged as they happen.
UNDOABLE = {"potty", "walk", "sleep", "fill", "meal", "measurement", "note", "training"}


def entry_pets(entry):
    if isinstance(entry, Pet):
        return [entry]
    if isinstance(entry, TrainingLog):
        return [entry.command.pet]
    if isinstance(entry, Command):
        return [entry.pet]
    if isinstance(entry, (Product, AccidentSpot, AccidentContext, PottyLocation)):
        return []
    if hasattr(entry, "pet"):
        return [entry.pet] if entry.pet_id else []
    if hasattr(entry, "pets"):
        return list(entry.pets.all())
    return list(entry.container.pets.all())


class HouseholdDashboard(HouseholdMixin, TemplateView):
    template_name = "pets/household.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(pets=self.household.pets.filter(active=True), household=self.household,
                                        **kwargs)


class HouseholdEdit(HouseholdMixin, View):
    def check(self):
        if not is_owner(self.request.user, self.household):
            raise PermissionDenied

    def render_form(self, form):
        return render(self.request, "pets/form.html", {"form": form, "title": _("Household"), "kind": "household",
                                                        "cancel_url": reverse("pets:setup")})

    def get(self, request):
        self.check()
        form = forms.HouseholdForm(instance=self.household)
        form.hydrated_fielsets = [{"layout": "default", "layout_attrs": {}, "fields": list(form)}]
        return self.render_form(form)

    def post(self, request):
        self.check()
        form = forms.HouseholdForm(request.POST, instance=self.household)
        if form.is_valid():
            household = form.save(commit=False)
            household.setup_done = sorted(set(household.setup_done or []) | {"quiet"})  # quiet hours seen
            household.save()
            messages.success(request, _("Household saved."))
            target = request.POST.get("next") or request.GET.get("next")
            if target and url_has_allowed_host_and_scheme(target, {request.get_host()}):
                return HttpResponseRedirect(target)
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


class SetupSkip(HouseholdMixin, View):
    """Skip a first-run step, or hide the checklist ("all")."""

    http_method_names = ["post"]

    def post(self, request):
        step = request.POST.get("step")
        if step in ("quiet", "bowls", "notifications", "all"):
            household = self.household
            household.setup_done = sorted(set(household.setup_done or []) | {step})
            household.save(update_fields=["setup_done"])
        elif step == "schedule":
            pet = get_object_or_404(self.household.pets, slug=request.POST.get("pet"))
            pet.schedule_checked = True
            pet.save(update_fields=["schedule_checked"])
        return HttpResponseRedirect(self.back_url())


class Export(HouseholdMixin, View):
    """Everything as CSV in a zip, in the importer's columns (PUP-117). Anyone in the household can take it."""

    def get(self, request):
        from .exporter import export_zip

        response = HttpResponse(export_zip(self.household), content_type="application/zip")
        stamp = timezone.localtime().strftime("%Y-%m-%d")
        response["Content-Disposition"] = f'attachment; filename="housetrained-export-{stamp}.zip"'
        return response


WEEKDAYS = [(0, _("Monday")), (1, _("Tuesday")), (2, _("Wednesday")), (3, _("Thursday")), (4, _("Friday")),
            (5, _("Saturday")), (6, _("Sunday"))]


class Notifications(HouseholdMixin, View):
    """Notifications (PUP-58): turn them on per device, choose reminders, send a test."""

    def get(self, request):
        from . import push
        from .models import NotificationPreference

        prefs, _created = NotificationPreference.objects.get_or_create(user=request.user)
        if not push.allowed():
            return render(request, "pets/notifications.html", {"disabled": True, "prefs": prefs})
        return render(request, "pets/notifications.html", {
            "prefs": prefs, "public_key": push.public_key(), "household": self.household,
            "is_owner": is_owner(request.user, self.household), "weekdays": WEEKDAYS,
            "devices": request.user.push_subscriptions.order_by("-created"),
        })

    def post(self, request):
        import json

        from django.http import JsonResponse

        from . import push
        from .models import NotificationPreference, PushSubscription

        action = request.POST.get("action") or request.GET.get("action")
        if not push.allowed() and action in ("subscribe", "test"):
            if action == "subscribe":
                return JsonResponse({"ok": False, "reason": "disabled"}, status=403)
            messages.warning(request, _("Notifications are turned off for this site."))
            return HttpResponseRedirect(reverse("pets:notifications"))
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
            prefs.walk_overdue = bool(request.POST.get("walk_overdue"))
            # Each number: kept when missing (its switch was off) or not a number; held to a sensible range.
            for name, low, high in (("walk_lead_minutes", 0, 120), ("overdue_minutes", 1, 120), ("late_limit_hours", 1, 12),
                                    ("weekly_day", 0, 6), ("weekly_hour", 0, 23)):
                try:
                    setattr(prefs, name, min(max(int(request.POST[name]), low), high))
                except (KeyError, ValueError):
                    pass
            if is_owner(request.user, self.household):
                for name, low, high in (("water_cutoff_hours", 0, 12), ("water_back_minutes", 0, 240)):
                    try:
                        setattr(self.household, name, min(max(int(request.POST[name]), low), high))
                    except (KeyError, ValueError):
                        pass
                self.household.save(update_fields=["water_cutoff_hours", "water_back_minutes"])
            prefs.low_supply = bool(request.POST.get("low_supply"))
            prefs.weekly_progress = bool(request.POST.get("weekly_progress"))
            prefs.water_cutoff = bool(request.POST.get("water_cutoff"))
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


class Progress(HouseholdMixin, TemplateView):
    """Is house-training getting better? (PUP-73)"""

    template_name = "pets/progress.html"

    def get_context_data(self, **kwargs):
        from . import progress

        pet = get_object_or_404(Pet, household=self.household, slug=self.kwargs["slug"])
        return super().get_context_data(pet=pet, **progress.page(pet), **kwargs)


RETRY_AFTER = datetime.timedelta(minutes=15)  # the page's "try again" banner (Next Walk: schedule.RETRY)


class Disregard(HouseholdMixin, View):
    """The meal or wake-up driving Next Walk didn't lead to a potty break: stop counting it (and any before it)."""

    http_method_names = ["post"]

    def post(self, request, slug):
        self.require("add", PottyBreak)
        pet = get_object_or_404(self.household.pets, slug=slug)
        pet.triggers_cleared_at = timezone.now()
        pet.save(update_fields=["triggers_cleared_at"])
        messages.info(request, _("Disregarded. %(name)s is back on the usual gap.") % {"name": pet.name})
        return HttpResponseRedirect(self.back_url([pet]))


class Coach(HouseholdMixin, View):
    """Apply the coach's suggestion (a new pee or poop interval), or not now (ask again in two days)."""

    http_method_names = ["post"]

    def post(self, request, slug):
        from .schedule import COACH_SNOOZE, coach, fmt_minutes

        self.require("change", Pet)
        pet = get_object_or_404(self.household.pets, slug=slug)
        tip = coach(pet)
        if request.POST.get("action") == "apply" and tip and request.POST.get("kind") == tip["kind"]:
            setattr(pet, f"{tip['kind']}_every", tip["suggested"])
            pet.schedule_changed_at = timezone.now()
            pet.coach_snoozed_until = None
            pet.save(update_fields=[f"{tip['kind']}_every", "schedule_changed_at", "coach_snoozed_until"])
            messages.success(request, _("%(name)s: %(kind)s every %(every)s.") % {
                "name": pet.name, "kind": _("pee") if tip["kind"] == "pee" else _("poop"),
                "every": fmt_minutes(tip["suggested"])})
        elif request.POST.get("action") == "snooze":
            pet.coach_snoozed_until = timezone.now() + COACH_SNOOZE
            pet.save(update_fields=["coach_snoozed_until"])
        return HttpResponseRedirect(self.back_url([pet]))


class QuickPotty(HouseholdMixin, View):
    """One-tap potty logging from the top of the page (PUP-74): pee, poop, both or nothing, now."""

    http_method_names = ["post"]

    def post(self, request):
        from .forms import link_walk

        self.require("add", PottyBreak)
        slugs = request.POST.getlist("pet")
        pets = list(self.household.pets.filter(active=True, slug__in=slugs)) if slugs else []
        what = request.POST.get("what")
        if not pets:
            messages.error(request, _("Pick a pet first."))
            return HttpResponseRedirect(self.back_url())
        if what not in ("pee", "poop", "both", "nothing", "dismiss"):
            messages.error(request, _("Pick pee, poop, both or nothing."))
            return HttpResponseRedirect(self.back_url(pets))
        now = timezone.now().replace(microsecond=0)
        names = ", ".join(p.name for p in pets)
        if what == "dismiss":
            Pet.objects.filter(pk__in=[p.pk for p in pets]).update(retry_at=None)
            return HttpResponseRedirect(self.back_url(pets))
        location = request.POST.get("location")
        notes = request.POST.get("notes", "").strip()
        entry, logged = None, []
        for pet in pets:
            spot = location if location in pet.allowed_potty_locations() else pet.allowed_potty_locations()[0]
            entry = PottyBreak(pet=pet, time=now, pee=what in ("pee", "both"), poop=what in ("poop", "both"),
                               location=spot, logged_by=request.user, notes=notes)
            link_walk(entry)
            entry.save()
            logged.append(entry)
        back = self.back_url(pets)
        if what == "nothing":
            Pet.objects.filter(pk__in=[p.pk for p in pets]).update(retry_at=now + RETRY_AFTER)
            undo.offer(request, _("Logged nothing. Try %(names)s again in 15 minutes.") % {"names": names}, logged,
                       back, messages.INFO)
            return HttpResponseRedirect(back)
        Pet.objects.filter(pk__in=[p.pk for p in pets]).update(retry_at=None)
        labels = {"pee": _("pee"), "poop": _("poop"), "both": _("pee + poop")}
        undo.offer(request, _("Logged %(what)s for %(names)s.") % {"what": labels[what], "names": names}, logged, back)
        if len(pets) == 1 and entry.accident:
            # An accident: offer its details (where, what was going on, found later); all optional.
            back = self.back_url(pets)
            return HttpResponseRedirect(reverse("pets:edit", args=["potty", entry.pk]) + f"?next={back}")
        return HttpResponseRedirect(self.back_url(pets))


class Setup(HouseholdMixin, TemplateView):
    template_name = "pets/setup.html"

    def get_context_data(self, **kwargs):
        from .supplies import display_unit, status

        unit = display_unit(self.household)
        bags = SupplyItem.objects.filter(product__household=self.household).select_related("product__household").prefetch_related("pets")
        return super().get_context_data(
            household=self.household,
            pets=self.household.pets.all(),
            containers=self.household.containers.prefetch_related("pets"),
            products=self.household.products.all(),
            spots=self.household.accident_spots.all(),
            contexts=self.household.accident_contexts.all(),
            places=self.household.potty_places.all(),
            bags=[status(b, unit) for b in bags],
            **kwargs,
        )


class PetDashboard(HouseholdMixin, TemplateView):
    template_name = "pets/pet.html"

    def get_context_data(self, **kwargs):
        pet = get_object_or_404(Pet, household=self.household, slug=self.kwargs["slug"])
        more = [(kind, ENTRIES[kind].title) for kind in ("walk", "sleep", "meal", "measurement", "note")]
        filters = [("all", _("All")), ("walk", _("Walks")), ("pee", _("Pees")), ("poop", _("Poops")),
                   ("accident", _("Accidents")), ("meal", _("Meals")), ("sleep", _("Sleep")),
                   ("measurement", _("Measurements"))]
        from .schedule import setup_steps

        steps = setup_steps(pet, self.request.user) if can_edit(self.request.user, self.household) else []
        recent, more_activity = activity(pet)
        return super().get_context_data(pet=pet, recent=recent, more_activity=more_activity, more_entries=more,
                                        activity_filters=filters, setup_steps=steps, **kwargs)


class ActivityPage(HouseholdMixin, TemplateView):
    """A page of All activity for the pet page: a filter, or more as you scroll (PUP-143)."""

    template_name = "pets/_activity_items.html"

    def get_context_data(self, **kwargs):
        pet = get_object_or_404(Pet, household=self.household, slug=self.kwargs["slug"])
        try:
            page = max(int(self.request.GET.get("page", 0)), 0)
        except ValueError:
            page = 0
        recent, more = activity(pet, self.request.GET.get("filter", "all"), page)
        return super().get_context_data(recent=recent, more_activity=more, page=page,
                                        next_url=reverse("pets:pet", args=[pet.slug]), **kwargs)


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


class EntryView(HouseholdMixin, View):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        if kwargs["kind"] not in ENTRIES:
            raise Http404
        self.kind = kwargs["kind"]
        self.entry = ENTRIES[self.kind]

    def get_instance(self):
        model = self.entry.model
        return get_object_or_404(
            getattr(model, "everything", model._default_manager), pk=self.kwargs["pk"],
            **{self.entry.household_path: self.household}
        )

    def render_form(self, form, instance=None):
        return render(self.request, "pets/form.html", {
            "form": form, "kind": self.kind, "title": self.entry.title, "object": instance,
            "ended": bool(self.request.GET.get("ended")), "next": self.request.GET.get("next", ""),
            "cancel_url": self.back_url(
                self.household.pets.filter(slug=self.request.GET.get("pet")) if not instance else
                [p for p in entry_pets(instance)]),
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
        pets = {pet for entry in saved for pet in entry_pets(entry)}
        back = self.back_url(pets)
        if getattr(form, "uncalibrated_bowls", False):
            messages.warning(request, _(
                "Saved, but this bowl isn't calibrated, so the amount is unknown and food tracking will be rough. "
                "Calibrate it: tare the bowl, fill it as usual, and enter the weight."
            ))
        elif getattr(form, "meals_logged", None):
            names = ", ".join(pet.name for pet in form.meals_logged)
            text = _("Bowl filled, and a meal logged for %(names)s.") % {"names": names}
            undo.offer(request, text, saved, back)  # the meals go with their fill
        elif self.kind in UNDOABLE:
            undo.offer(request, _("%(title)s saved.") % {"title": self.entry.title}, saved, back)
        else:
            messages.success(request, _("%(title)s saved.") % {"title": self.entry.title})
        return HttpResponseRedirect(back)


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
        back = self.back_url(pets)
        names = ", ".join(p.name for p in pets)
        if kind == "walk":
            self.require("add", Walk)
            walk = Walk.objects.create(household=self.household, start=start, walker=request.user, logged_by=request.user)
            walk.pets.set(pets)
            from .reminders import close_walk_reminders

            close_walk_reminders(pets)  # the reminder was to get going: done
            undo.offer(request, _("Walk started for %(names)s.") % {"names": names}, [walk], back)
        elif kind == "sleep":
            self.require("add", Sleep)
            started = [Sleep.objects.create(pet=p, start=start, logged_by=request.user)
                       for p in pets if not p.sleeps.filter(end=None).exists()]
            if started:
                undo.offer(request, _("Sleep started for %(names)s.") % {"names": names}, started, back)
        else:
            raise Http404
        return HttpResponseRedirect(back)


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


class Undo(HouseholdMixin, View):
    """The Undo button on a "logged" banner (PUP-151)."""

    http_method_names = ["post"]

    def post(self, request):
        self.require("delete")
        back = undo.take(request, request.POST.get("token", ""), self.household)
        in_place = request.headers.get("X-Requested-With") == "fetch"
        if back is None:
            text = _("Too late to undo. Open the entry to change or delete it.")
            if in_place:
                return JsonResponse({"ok": False, "text": text})
            messages.warning(request, text)
            return HttpResponseRedirect(self.back_url())
        if in_place:
            return JsonResponse({"ok": True, "text": _("Undone.")})
        messages.info(request, _("Undone."))
        return HttpResponseRedirect(back)


class NewPottyLocation(HouseholdMixin, View):
    """"+ New" location on the potty form (PUP-181): made straight away, so the form can pick it and show the accident
    rows for it. A name that's already there (any capitals, or a built-in one) is that one."""

    http_method_names = ["post"]

    def post(self, request):
        self.require("add", PottyLocation)
        name = request.POST.get("name", "").strip()[:100]
        if not name:
            return JsonResponse({"ok": False, "text": _("Type a name.")}, status=400)
        for code, label in PottyBreak.LOCATIONS:
            if name.lower() in (code, str(label).lower()):
                return JsonResponse({"ok": True, "code": code, "name": str(label), "accident": code == PottyBreak.INSIDE})
        place = self.household.potty_places.filter(name__iexact=name).first() or PottyLocation.objects.create(
            household=self.household, name=name, accident=request.POST.get("accident") == "true")
        return JsonResponse({"ok": True, "code": place.code, "name": place.name, "accident": place.accident})


class QuickTraining(HouseholdMixin, View):
    """One tap on a command's 1-5 button (PUP-150)."""

    http_method_names = ["post"]

    def post(self, request):
        self.require("add", TrainingLog)
        command = get_object_or_404(Command, pk=request.POST.get("command"), pet__household=self.household)
        try:
            score = int(request.POST.get("score", ""))
        except ValueError:
            score = 0
        back = self.back_url([command.pet])
        in_place = request.headers.get("X-Requested-With") == "fetch"  # tapped on the card: stay where you are
        if score not in dict(TrainingLog.SCORES):
            if in_place:
                return JsonResponse({"ok": False, "text": _("Pick 1 to 5.")}, status=400)
            messages.error(request, _("Pick 1 to 5."))
            return HttpResponseRedirect(back)
        log = TrainingLog.objects.create(command=command, score=score, logged_by=request.user,
                                         time=timezone.now().replace(microsecond=0))
        text = _("Training logged: %(command)s, %(score)s %(label)s.") % {
            "command": command.name, "score": score, "label": log.get_score_display()}
        token = undo.offer(request, text, [log], back, banner=not in_place)
        if in_place:
            return JsonResponse({"ok": True, "text": text, "token": token, "score": score})
        return HttpResponseRedirect(back)


class Training(HouseholdMixin, TemplateView):
    """Every command a pet is learning, with how it's going over time (PUP-150)."""

    template_name = "pets/training.html"

    def get_context_data(self, **kwargs):
        from .insights import training_chart

        from .insights import sorted_commands

        pet = get_object_or_404(Pet, household=self.household, slug=self.kwargs["slug"])
        order = {Command.WORKING: 0, Command.KNOWN: 1, Command.RETIRED: 2}
        rows = sorted(sorted_commands(pet), key=lambda r: order[r[0].status])  # stable: keeps the chosen order
        commands = [{"command": c, "avg": f"{avg:.1f}" if avg else None, **training_chart(c)} for c, avg in rows]
        scale = "\n".join(f"{n} {label}: {TrainingLog.SCORE_HELP[n]}" for n, label in TrainingLog.SCORES)
        return super().get_context_data(pet=pet, commands=commands, scores=TrainingLog.SCORES, scale_text=scale,
                                        sorts=Pet.TRAINING_SORTS, **kwargs)

    def post(self, request, slug):
        """Change the order: sort=manual|alpha|progress, or move=<command> with direction=up|down (my order)."""
        self.require("change")
        pet = get_object_or_404(Pet, household=self.household, slug=slug)
        if request.POST.get("sort") in dict(Pet.TRAINING_SORTS):
            pet.training_sort = request.POST["sort"]
            pet.save(update_fields=["training_sort"])
        move = pet.commands.filter(pk=request.POST.get("move")).first()
        if move:
            from .insights import sorted_commands

            groups = {Command.WORKING: 0, Command.KNOWN: 1, Command.RETIRED: 2}
            order = sorted((c for c, _avg in sorted_commands(pet)), key=lambda c: groups[c.status])  # as shown
            i = order.index(move)
            j = i - 1 if request.POST.get("direction") == "up" else i + 1
            if 0 <= j < len(order) and order[j].status == move.status:  # within its group (working, known, retired)
                order[i], order[j] = order[j], order[i]
            for position, command in enumerate(order):
                Command.objects.filter(pk=command.pk).update(position=position)
            pet.training_sort = "manual"  # moving one means you're ordering them yourself
            pet.save(update_fields=["training_sort"])
        return HttpResponseRedirect(reverse("pets:training", args=[pet.slug]))


class HealthChecks(HouseholdMixin, TemplateView):
    """Setup > Health checks (PUP-159): every check, its numbers, and when it last went off for each pet."""

    template_name = "pets/health.html"
    NUMBERS = {"soft_count": (int, 1, 10), "dark_count": (int, 1, 10), "pee_factor": (float, 1.1, 5),
               "pee_extra": (int, 0, 20), "usual_days": (int, 3, 28)}

    def get_context_data(self, **kwargs):
        from . import health

        cfg = health.settings_for(self.household)
        pets = list(self.household.pets.filter(active=True))
        past = {pet: health.history(pet) for pet in pets}
        checks = []
        for key, label, level, what in health.CHECKS:
            trips = [{"pet": pet, "last": past[pet][key][0] if past[pet][key] else None, "count": len(past[pet][key])}
                     for pet in pets]
            checks.append({"key": key, "label": label, "level": level, "what": what, "on": key not in cfg["off"],
                           "trips": trips})
        return super().get_context_data(checks=checks, cfg=cfg, is_owner=is_owner(self.request.user, self.household),
                                        **kwargs)

    def post(self, request):
        from . import health

        if not is_owner(request.user, self.household):
            raise PermissionDenied
        cfg = health.settings_for(self.household)
        saved = {"off": [key for key, *_rest in health.CHECKS if not request.POST.get(key)]}
        for name, (kind, low, high) in self.NUMBERS.items():
            try:
                saved[name] = min(max(kind(request.POST[name]), low), high)
            except (KeyError, ValueError):
                saved[name] = cfg[name]
        self.household.health_checks = saved
        self.household.save(update_fields=["health_checks"])
        messages.success(request, _("Health checks saved."))
        return HttpResponseRedirect(reverse("pets:health"))
