from django.urls import path, reverse_lazy
from django.views.generic import RedirectView

from . import views

app_name = "pets"

urlpatterns = [
    path("pets/", views.HouseholdDashboard.as_view(), name="household"),
    # Settings (PUP-217), once called Setup: the old addresses still lead there.
    path("pets/settings/", views.Settings.as_view(), name="settings"),
    path("pets/setup/", RedirectView.as_view(url=reverse_lazy("pets:settings"))),
    path("pets/setup/household/", RedirectView.as_view(url=reverse_lazy("pets:settings"))),
    path("pets/settings/members/", views.Members.as_view(), name="members"),
    path("pets/settings/import/", views.Import.as_view(), name="import"),
    path("pets/settings/export/", views.Export.as_view(), name="export"),
    path("pets/settings/skip/", views.SetupSkip.as_view(), name="setup-skip"),
    path("pets/notifications/", views.Notifications.as_view(), name="notifications"),
    path("pets/settings/health/", views.HealthChecks.as_view(), name="health"),
    path("pets/pantry/", views.Pantry.as_view(), name="pantry"),
    path("pets/pantry/<int:pk>/count/", views.PantryCount.as_view(), name="pantry-count"),
    path("pets/activities/", views.Activities.as_view(), name="activities"),
    path("pets/activities/page/", views.ActivityPage.as_view(), name="activity-all"),
    path("sw.js", views.service_worker, name="service-worker"),
    path("resume/", views.Resume.as_view(), name="resume"),
    path("pets/<slug:slug>/", views.PetDashboard.as_view(), name="pet"),
    path("pets/<slug:slug>/progress/", views.Progress.as_view(), name="progress"),
    path("pets/<slug:slug>/activity/", views.ActivityPage.as_view(), name="activity"),
    path("log/<str:kind>/", views.EntryList.as_view(), name="list"),
    path("log/<str:kind>/add/", views.EntryAdd.as_view(), name="add"),
    path("log/<str:kind>/<int:pk>/", views.EntryEdit.as_view(), name="edit"),
    path("log/<str:kind>/<int:pk>/delete/", views.EntryDelete.as_view(), name="delete"),
    path("log/potty/quick/", views.QuickPotty.as_view(), name="quick-potty"),
    path("log/potty/location/", views.NewPottyLocation.as_view(), name="new-location"),
    path("log/training/quick/", views.QuickTraining.as_view(), name="quick-training"),
    path("undo/", views.Undo.as_view(), name="undo"),
    path("pets/<slug:slug>/training/", views.Training.as_view(), name="training"),
    path("pets/<slug:slug>/disregard/", views.Disregard.as_view(), name="disregard"),
    path("pets/<slug:slug>/meal-timing/", views.MealTiming.as_view(), name="meal-timing"),
    path("pets/<slug:slug>/coach/", views.Coach.as_view(), name="coach"),
    path("log/<str:kind>/start/", views.ActivityStart.as_view(), name="start"),
    path("log/<str:kind>/<int:pk>/<str:action>/", views.ActivityStop.as_view(), name="stop"),
]
