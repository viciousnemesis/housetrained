// Housetrained service worker: shows reminders sent by the server and opens the right page when tapped.
self.addEventListener("push", function (event) {
    var data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { data = {body: event.data && event.data.text()}; }
    event.waitUntil(self.registration.showNotification(data.title || "Housetrained", {
        body: data.body || "",
        tag: data.tag || undefined,
        icon: "/static/babybuddy/root/android-chrome-192x192.png",
        badge: "/static/babybuddy/root/android-chrome-192x192.png",
        data: {url: data.url || "/"}
    }));
});

self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) || "/";
    event.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(function (open) {
        for (var i = 0; i < open.length; i++) {
            if ("focus" in open[i]) { open[i].navigate(url); return open[i].focus(); }
        }
        return clients.openWindow(url);
    }));
});
