// Housetrained service worker: shows reminders sent by the server and opens the right page when tapped.
self.addEventListener("push", function (event) {
    var data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { data = {body: event.data && event.data.text()}; }
    if (data.close) {
        // No longer needed (e.g. the walk it was for has started): take it away, show nothing.
        event.waitUntil(self.registration.getNotifications({tag: data.close}).then(function (shown) {
            shown.forEach(function (n) { n.close(); });
        }));
        return;
    }
    event.waitUntil(self.registration.showNotification(data.title || "Housetrained", {
        body: data.body || "",
        tag: data.tag || undefined,
        icon: "/static/housetrained/root/android-chrome-192x192.png",
        badge: "/static/housetrained/root/android-chrome-192x192.png",
        data: {url: data.url || "/"}
    }));
});

self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) || "/";
    event.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(function (open) {
        // Reuse an open window on the same site; a link to another address (HOUSETRAINED_URL) opens a new one.
        var target = new URL(url, self.location.origin);
        for (var i = 0; i < open.length; i++) {
            if ("focus" in open[i] && new URL(open[i].url).origin === target.origin) {
                open[i].navigate(target.href);
                return open[i].focus();
            }
        }
        return clients.openWindow(url);
    }));
});
