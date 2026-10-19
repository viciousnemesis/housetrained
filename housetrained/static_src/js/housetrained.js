if (typeof jQuery === "undefined") {
  throw new Error("Housetrained requires jQuery.");
}

/**
 * Housetrained namespace for the site-wide scripts.
 *
 * @type {{}}
 */
var Housetrained = (function () {
  return {};
})();

/**
 * Pull to refresh.
 *
 * @type {{init: Housetrained.PullToRefresh.init, onRefresh: Housetrained.PullToRefresh.onRefresh}}
 */
Housetrained.PullToRefresh = (function (ptr) {
  return {
    init: function () {
      ptr.init({
        mainElement: "body",
        onRefresh: this.onRefresh,
      });
    },

    onRefresh: function () {
      window.location.reload();
    },
  };
})(PullToRefresh);

/**
 * Show a loading spinner on the submit button when a form is submitted and
 * prevent double-submission.
 */
(function handleFormSubmit() {
  $("form").on("submit", function (event) {
    var submitter =
      (event.originalEvent && event.originalEvent.submitter) ||
      $(this).find('[type="submit"]')[0];
    if (!submitter || $(submitter).prop("disabled")) return;
    // A disabled button isn't sent with the form, so carry its name/value
    // (e.g. what=pee) in a hidden field.
    if (submitter.name) {
      $("<input>", {
        type: "hidden",
        name: submitter.name,
        value: submitter.value,
      }).appendTo(this);
    }
    $(submitter)
      .prop("disabled", true)
      .prepend(
        '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>',
      );
  });
})();

Housetrained.RememberAdvancedToggle = function (ptr) {
  localStorage.setItem("advancedForm", event.newState);
};

(function toggleAdvancedFields() {
  window.addEventListener("load", function () {
    if (localStorage.getItem("advancedForm") !== "open") {
      return;
    }

    document.querySelectorAll(".advanced-fields").forEach(function (node) {
      node.open = true;
    });
  });
})();

/* Page refresh: reloads the page every
 * `refresh_rate` milliseconds while it's showing, and when it gets focus. */
Housetrained.Dashboard = (function () {
  var Dashboard = {
    watch: function (element_id, refresh_rate) {
      window.addEventListener("focus", Dashboard.update, false);
      if (refresh_rate) {
        setInterval(function () {
          if (!document.hidden) Dashboard.update();
        }, refresh_rate);
      }
    },
    update: function () {
      location.reload();
    },
  };
  return Dashboard;
})();
