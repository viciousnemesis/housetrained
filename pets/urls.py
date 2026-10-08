from django.urls import path

from . import views

app_name = "pets"

urlpatterns = [
    path("pets/", views.HouseholdDashboard.as_view(), name="household"),
    path("pets/timeline/", views.Timeline.as_view(), name="timeline"),
    path("pets/setup/", views.Setup.as_view(), name="setup"),
    path("pets/setup/household/", views.HouseholdEdit.as_view(), name="household-edit"),
    path("pets/setup/members/", views.Members.as_view(), name="members"),
    path("pets/setup/import/", views.Import.as_view(), name="import"),
    path("pets/notifications/", views.Notifications.as_view(), name="notifications"),
    path("sw.js", views.service_worker, name="service-worker"),
    path("pets/<slug:slug>/", views.PetDashboard.as_view(), name="pet"),
    path("pets/<slug:slug>/progress/", views.Progress.as_view(), name="progress"),
    path("log/<str:kind>/", views.EntryList.as_view(), name="list"),
    path("log/<str:kind>/add/", views.EntryAdd.as_view(), name="add"),
    path("log/<str:kind>/<int:pk>/", views.EntryEdit.as_view(), name="edit"),
    path("log/<str:kind>/<int:pk>/delete/", views.EntryDelete.as_view(), name="delete"),
    path("log/<str:kind>/start/", views.ActivityStart.as_view(), name="start"),
    path("log/<str:kind>/<int:pk>/<str:action>/", views.ActivityStop.as_view(), name="stop"),
]
