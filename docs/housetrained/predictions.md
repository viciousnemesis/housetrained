# Next Walk predictions

The Next Walk card guesses when each pet next needs to pee and poop, and groups those needs into one walk.
It only uses that pet's own logs from the past two weeks. It's an estimate, not veterinary advice.

The code is `pets/insights.py` (`next_walk_card`). Every number below can be changed on the **Site Settings**
page (Predictions); the defaults suit most dogs. Tests: `pets/tests/test_predictions.py`.

## What the guess is made of

For each of pee and poop, the app collects candidate times and picks the earliest one that's still current.

1. **Usual gap at this time of day.** Gaps between potty breaks are grouped by part of the day (overnight,
   morning, afternoon, evening, counted from the pet's usual wake-up). With at least three gaps in the same part
   of the day, the median gap is added to the last potty break: "morning pees usually 3h apart".
   Gaps longer than **12 hours** are ignored (usually missed entries, not real holds).
2. **Fallback when there isn't enough history.**
   - For the first **7 days** of logs (or with no breaks in the past three days): the **age rule of thumb**, a
     puppy can hold it about its age in months plus one hour, capped at **8 hours**. Without a birth date the
     pet counts as a year old.
   - After that: the average gap over the past three days.
   - Learned gaps (step 1) are only used once there are **3 days** of history.
3. **Meals.** Only logged meals count (Log Meal, or Fill Bowl with "Eaten right away"); filling a bowl says
   nothing about when the pet ate. Food drives poops; food and water drive pees. If the pet ate after its last
   potty break, the app adds the usual delay from a meal to the next pee or poop. That delay is learned from
   potty breaks within **3 hours** of a meal once three meals were followed by one; until then it's
   **20 minutes** for pee and **30 minutes** for poop.
4. **Waking up.** A logged sleep that ended after the last potty break means "woke up at ...": now.

A trigger (meal, wake-up) that passed more than **30 minutes** ago without a potty break gives way to the next
estimate, so the card doesn't stay stuck on "overdue".

## The night

Each day's longest potty-free stretch (at least **4 hours**) is that day's night: the last break before it is
bedtime, the first after it is wake-up. Bedtime and wake-up are the weighted medians over the past two weeks,
with recent nights counting more (a night's weight halves every **3 days**), so a drifting routine pulls the
estimate along. Times wrap past midnight correctly. Until two nights are logged, bedtime is **23:00** and
wake-up **06:30**.

- After the usual bedtime potty (or up to **45 minutes** before it), the next need is predicted for the morning:
  usual wake-up plus the usual delay to the first pee or poop.
- A need that would fall during the night moves: a pee goes on the bedtime walk if that's still ahead,
  anything else waits until after waking up.
- Accidents (inside) don't count when learning the night.

Daylight saving: bedtime and wake-up are learned in local clock time, gaps in real time, so a 25-hour or
23-hour day doesn't shift either.

## One walk for several needs

The earliest prediction sets the walk time. Any other need due before that walk would end (the pet's median
walk length, **15 minutes** until learned) plus **30 minutes** is handled on the same walk: "expect pee + poop".
Otherwise it shows as "likely next walk".

## Settings

| Setting | Default |
|---|---|
| Longest a pet is expected to hold it | 8 hours |
| Days of history before learning from it | 3 |
| Days before the 3-day average replaces the age rule | 7 |
| Ignore gaps longer than | 12 hours |
| A potty counts as after a meal within | 3 hours |
| Pee / poop after a meal, until learned | 20 / 30 minutes |
| Drop a missed trigger after | 30 minutes |
| Group needs into one walk within | 30 minutes |
| Walk length, until learned | 15 minutes |
| Shortest night | 4 hours |
| How fast old nights fade | 3 days |
| Bedtime potty window | 45 minutes |
| Bedtime / wake-up, until learned | 23:00 / 06:30 |
