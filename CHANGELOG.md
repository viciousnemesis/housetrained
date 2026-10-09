# Changelog

Housetrained's releases. Baby Buddy's own history is in its repository and in this repository's git log.

## 0.1.0 (unreleased)

First version of Housetrained, forked from Baby Buddy 2.11.0.

- Pets (dog, cat, other); one household per install, with owners, members and read-only people.
- Potty breaks with per-pet locations, linked to walks (listed inside the walk); walks for one or several pets
  with live timers; sleep; Nothing (went out, nothing happened).
- Next walk: a schedule set per pet (pee every, poop every, out after a meal) and quiet hours per household;
  sooner after meals and naps; back with nothing means try again in 15 minutes. Starts from the pet's age.
- Coach: suggests a shorter interval after accidents that came before the walk was due, a longer one after a
  clean week. Nothing changes until someone taps Apply.
- Accidents show how long before (or after) the walk was due.
- Pet page: Status (head out, accident-free streak, most recent), Today, potty breaks per day, accident times,
  recent activity with filters; first-run checklist. Progress page: week by week, time since the last pee,
  by part of the day, where and what was going on.
- Bowls, feeders, water bowls and litter boxes; Fill Bowl vs Log Meal ("how much of the bowl"); products and
  bags with days-left estimates.
- Reminders as browser/phone push notifications (off until an admin allows them): walk due (minutes before),
  still not out, buy more, water bowl, weekly progress. Dismissed when the walk starts.
- Health details on potty breaks (amount, colour, consistency) with gentle alerts.
- CSV import and export, REST API at `/api/v1/`, demo data command.
- Metric or imperial, 12/24-hour time, light/dark/automatic theme, help bubbles. English only.
- Baby mode: babies alongside pets in the same household, with Baby Buddy's baby tracking.
- Private by design: nothing loaded from or sent to the internet (push notifications aside, opt-in).
- New accounts aren't site admins unless "Site admin" is ticked; no default admin/admin.
