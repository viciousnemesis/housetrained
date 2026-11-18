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
 * Automatic reloads wait while someone is using the page (PUP-200): within 10
 * seconds of a touch or key press, or once a form is on its way, a reload
 * would swallow the tap.
 */
Housetrained.lastInput = 0;
Housetrained.sending = false;
["pointerdown", "keydown"].forEach(function (type) {
  document.addEventListener(
    type,
    function () {
      Housetrained.lastInput = Date.now();
    },
    true,
  );
});
window.addEventListener("pageshow", function (event) {
  if (event.persisted) Housetrained.sending = false; // back from the cache: the form went
});
Housetrained.canReload = function () {
  return !Housetrained.sending && Date.now() - Housetrained.lastInput > 10000;
};

/**
 * Show a loading spinner on the submit button when a form is submitted and
 * prevent double-submission.
 */
(function handleFormSubmit() {
  $("form").on("submit", function (event) {
    Housetrained.sending = true;
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
    // The spinner takes the label's place and the button keeps its size, so nothing moves (PUP-202).
    $(submitter)
      .css({
        width: submitter.offsetWidth + "px",
        height: submitter.offsetHeight + "px",
      })
      .prop("disabled", true)
      .html(
        '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>',
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

/* Page refresh: reloads the page every `refresh_rate` milliseconds while it's
 * showing and not in use. Not on focus: on a phone the first tap brings focus,
 * and the reload swallowed it (PUP-200). */
Housetrained.Dashboard = (function () {
  var Dashboard = {
    watch: function (element_id, refresh_rate) {
      if (refresh_rate) {
        setInterval(function () {
          if (!document.hidden && Housetrained.canReload()) Dashboard.update();
        }, refresh_rate);
      }
    },
    update: function () {
      location.reload();
    },
  };
  return Dashboard;
})();
