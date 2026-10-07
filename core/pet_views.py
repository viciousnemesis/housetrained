"""Housetrained additions: walk/sleep timers (per pet or for all pets) and logging for all pets at once."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic.base import RedirectView

from babybuddy.mixins import LoginRequiredMixin, PermissionRequiredMixin
from babybuddy.widgets import DateTimeInput
from core import forms as core_forms, models
from core.widgets import PillRadioSelect

ACTIVITIES = {"walk": "Walk", "sleep": "Sleep"}
ACTIVITY_MODELS = {"walk": models.TummyTime, "sleep": models.Sleep}
ALL_FORMS = {
    "potty": (core_forms.DiaperChangeForm, "core.add_diaperchange", "Log Potty", {"wet": True}),
    "food": (core_forms.FeedingForm, "core.add_feeding", "Log Bowl of Food", {}),
}


def home_url(children):
    """A single pet's dashboard, or the all-pets dashboard."""
    children = list(children)
    if len(children) == 1:
        return reverse("dashboard:dashboard-child", args=[children[0].slug])
    return reverse("dashboard:dashboard")


def timer_group(timer):
    """Timers started together (same activity and start) run and end as one."""
    return models.Timer.objects.filter(name=timer.name, start=timer.start).select_related("child", "user")


def _kind(kwargs, table):
    if kwargs["kind"] not in table:
        raise Http404
    return kwargs["kind"]


def _ids(request):
    try:
        return [int(i) for i in request.GET.get("ids", "").split(",") if i]
    except ValueError:
        raise Http404


class ActivityStart(PermissionRequiredMixin, RedirectView):
    """Start a walk or sleep timer for one pet (slug) or for every pet."""

    http_method_names = ["post"]
    permission_required = ("core.add_timer",)

    def post(self, request, *args, **kwargs):
        name = ACTIVITIES[_kind(kwargs, ACTIVITIES)]
        if "slug" in kwargs:
            children = [get_object_or_404(models.Child, slug=kwargs["slug"])]
        else:
            children = list(models.Child.objects.all())
        start = timezone.now().replace(microsecond=0)
        for child in children:
            if not models.Timer.objects.filter(child=child, name=name).exists():
                models.Timer.objects.create(child=child, name=name, user=request.user, start=start)
        self.url = home_url(children)
        return super().get(request, *args, **kwargs)


class ActivityEnd(PermissionRequiredMixin, RedirectView):
    """Save a walk/sleep entry for each pet in the timer's group, then open it for editing."""

    http_method_names = ["post"]
    permission_required = ("core.add_timer",)

    def post(self, request, *args, **kwargs):
        timer = get_object_or_404(models.Timer, pk=kwargs["pk"], name__in=ACTIVITIES.values())
        kind = "walk" if timer.name == "Walk" else "sleep"
        end = timezone.now()
        entries = []
        for t in timer_group(timer):
            times = {"child": t.child, "start": t.start, "end": end}
            if kind == "walk":
                entries.append(models.TummyTime.objects.create(
                    **times, milestone=t.user.first_name or t.user.username))
            else:
                entries.append(models.Sleep.objects.create(**times, nap=True))
            t.stop()
        if len(entries) == 1:
            view = "core:tummytime-update" if kind == "walk" else "core:sleep-update"
            self.url = reverse(view, args=[entries[0].id]) + "?ended=1"
        else:
            self.url = reverse("core:activity-group-edit", args=[kind]) + "?ids=" + ",".join(
                str(e.id) for e in entries)
        return super().get(request, *args, **kwargs)


class ActivityCancel(PermissionRequiredMixin, RedirectView):
    """Throw away a running walk/sleep (for every pet in its group) without saving anything."""

    http_method_names = ["post"]
    permission_required = ("core.add_timer",)

    def post(self, request, *args, **kwargs):
        timer = get_object_or_404(models.Timer, pk=kwargs["pk"], name__in=ACTIVITIES.values())
        group = list(timer_group(timer))
        self.url = home_url(t.child for t in group)
        for t in group:
            t.stop()
        return super().get(request, *args, **kwargs)


class ActivityDiscard(LoginRequiredMixin, RedirectView):
    """Cancel on the edit page right after End: delete the entries that were just created."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        model = ACTIVITY_MODELS[_kind(kwargs, ACTIVITY_MODELS)]
        if not request.user.has_perm(f"core.delete_{model._meta.model_name}"):
            raise PermissionDenied
        ids = [kwargs["pk"]] if "pk" in kwargs else _ids(request)
        entries = list(model.objects.filter(pk__in=ids).select_related("child"))
        self.url = home_url(e.child for e in entries)
        for entry in entries:
            entry.delete()
        return super().get(request, *args, **kwargs)


class GroupEditForm(forms.Form):
    start = forms.DateTimeField(label="Start time", widget=DateTimeInput())
    end = forms.DateTimeField(label="End time", widget=DateTimeInput())

    def __init__(self, kind, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if kind == "walk":
            self.fields["walker"] = forms.CharField(label="Walker", required=False)
        else:
            self.fields["nap"] = forms.TypedChoiceField(
                choices=[(True, "Nap"), (False, "Sleep")], coerce=lambda v: v in (True, "True"),
                label="Type", widget=PillRadioSelect())

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError("End time must be after the start time.")
        return data


class GroupEdit(LoginRequiredMixin, View):
    """One edit page for a walk/sleep that was ended for several pets at once."""

    def dispatch(self, request, *args, **kwargs):
        self.kind = _kind(kwargs, ACTIVITY_MODELS)
        self.model = ACTIVITY_MODELS[self.kind]
        if not request.user.has_perm(f"core.change_{self.model._meta.model_name}"):
            raise PermissionDenied
        self.entries = list(self.model.objects.filter(pk__in=_ids(request)).select_related("child"))
        if not self.entries:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def render(self, form):
        return render(self.request, "core/pet_group_edit.html", {
            "form": form,
            "title": "Walk" if self.kind == "walk" else "Sleep",
            "names": ", ".join(e.child.first_name for e in self.entries),
            "discard_url": reverse("core:activity-group-discard", args=[self.kind])
            + "?ids=" + self.request.GET.get("ids", ""),
        })

    def get(self, request, *args, **kwargs):
        first = self.entries[0]
        initial = {"start": timezone.localtime(first.start), "end": timezone.localtime(first.end)}
        if self.kind == "walk":
            initial["walker"] = first.milestone
        else:
            initial["nap"] = first.nap
        return self.render(GroupEditForm(self.kind, initial=initial))

    def post(self, request, *args, **kwargs):
        form = GroupEditForm(self.kind, request.POST)
        if not form.is_valid():
            return self.render(form)
        for entry in self.entries:
            entry.start, entry.end = form.cleaned_data["start"], form.cleaned_data["end"]
            if self.kind == "walk":
                entry.milestone = form.cleaned_data["walker"]
            else:
                entry.nap = form.cleaned_data["nap"]
            entry.save()
        messages.success(request, f"Saved for {len(self.entries)} pets.")
        return redirect(home_url(e.child for e in self.entries))


class AllAdd(LoginRequiredMixin, View):
    """Log potty or food once and save an identical entry for every pet."""

    def dispatch(self, request, *args, **kwargs):
        self.kind = _kind(kwargs, ALL_FORMS)
        self.form_class, perm, self.title, self.initial = ALL_FORMS[self.kind]
        if not request.user.has_perm(perm):
            raise PermissionDenied
        self.children = list(models.Child.objects.order_by("first_name"))
        if not self.children:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def make_form(self, data=None):
        form = self.form_class(data, user=self.request.user, initial=dict(self.initial))
        form.fields.pop("child")
        form.fieldsets = [{**fs, "fields": [f for f in fs["fields"] if f != "child"]} for fs in form.fieldsets]
        form.instance.child = self.children[0]  # lets per-pet model validation run
        return form

    def render(self, form):
        return render(self.request, "core/pet_all_form.html", {
            "form": form, "title": self.title,
            "names": ", ".join(c.first_name for c in self.children),
        })

    def get(self, request, *args, **kwargs):
        return self.render(self.make_form())

    def post(self, request, *args, **kwargs):
        form = self.make_form(request.POST)
        if not form.is_valid():
            return self.render(form)
        entry = form.save(commit=False)
        tags = form.cleaned_data.get("tags")
        for child in self.children:
            entry.pk = entry.id = None
            entry._state.adding = True
            entry.child = child
            entry.save()
            if tags:
                entry.tags.set(tags)
        messages.success(request, f"{self.title}: saved for {len(self.children)} pets.")
        return redirect(home_url(self.children))
