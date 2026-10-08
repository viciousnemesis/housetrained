# Changelog

## 0.1.0 (unreleased)

First version of Housetrained, forked from Baby Buddy 2.11.0.

- Pets (dog, cat, other) in households with owners, members and read-only people.
- Potty breaks with per-pet locations, linked to walks; walks for one or several pets with live timers; sleep.
- Bowls, feeders, water bowls and litter boxes; Fill Bowl vs Log Meal (with "Eaten right away"); products and
  bags with days-left estimates and buy-more reminders.
- Next Walk prediction from each pet's routine, meals and sleep; rules adjustable in Site Settings.
- Dashboards: next walk, statistics, walks, potty breaks per day with walks, accident times, meals, sleep,
  supplies; timeline and logs.
- Metric or imperial per household, 12/24-hour time, light/dark/automatic theme, help bubbles.
- CSV import, REST API at `/api/v1/`, demo data command.
- Baby Buddy's baby tracking kept behind "baby mode"; its own API only answers in baby mode.
- No default admin/admin: the first admin gets a random or chosen password.
