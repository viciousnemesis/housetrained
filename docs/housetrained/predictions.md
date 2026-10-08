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

Every meal or drink since the last potty break is a trigger; the earliest one wins, so logging another meal
or drink can only bring the estimate forward, never push it later.

A trigger (meal, wake-up) that passed more than **30 minutes** ago without a potty break gives way to the next
estimate. The pet's usual gap (steps 1 and 2) never gives way: once it has passed, the pet stays overdue until
a potty break is logged.

### Guarantees

These are checked by `pets/tests/test_prediction_scenarios.py`, including randomised routines:

1. Logging a meal or a drink never makes the estimate later.
2. Logging a wake-up never makes it later.
3. With nothing new logged, time passing never makes it later, except a missed trigger giving way, and then
   only back to the usual gap.
4. Once the usual gap has passed, the pet stays overdue until a potty break is logged.
5. Learning from accidents only ever brings the estimate forward.
6. Logging a pee is what moves the next pee later.

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

## Head out

The card's main time is when to **head out**: the earliest expected need minus the household's *time to get
outside* (Setup > Household settings, default 10 minutes: leash, shoes, stairs or elevator). "Due" and
"overdue", and the walk reminder notification, count from that time; the individual needs below it keep their
own times.

## Learning from accidents

When an accident (inside) is logged, the app stores what Next Walk expected just before it, using only what was
logged up to that moment. The Accident Times card lists recent accidents as "35m earlier than expected" or
"20m later than expected" (compared with when the need was expected, not the head-out time), with a summary.

If at least two accidents in the past two weeks came before the prediction, predictions move earlier by the
median of how early they were, up to **90 minutes** (not into the night, and not before the last potty break).
The reason says so: "~14m earlier: recent accidents came before the prediction". It learns from the unadjusted
predictions, so the adjustment doesn't feed on itself. Switch it off with *Learn from accidents*.

## One walk for several needs

The earliest prediction sets the walk time. Any other need due before that walk would end (the pet's median
walk length, **15 minutes** until learned) plus the **needs grouping buffer** (30 minutes) is handled on the same walk: "expect pee + poop".
Otherwise it shows as "likely next walk".

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Longest hold | 8 hours | Cap on the age rule of thumb |
| History needed to learn | 3 days | Before this, the age rule is used |
| Age rule period | 7 days | After this, the 3-day average is the fallback |
| Longest real gap | 12 hours | Longer gaps are ignored |
| After-meal window | 3 hours | Potty breaks this soon after a meal count as caused by it |
| Pee / poop after a meal | 20 / 30 minutes | Until the pet's own delay is learned |
| Missed trigger timeout | 30 minutes | A passed meal or wake-up stops driving the prediction |
| Needs grouping buffer | 30 minutes | A need due this long after a walk would normally end is expected on that walk |
| Usual walk length | 15 minutes | Until walks are logged |
| Shortest night | 4 hours | Shortest stretch that counts as the night |
| Night fade | 3 days | An older night counts half as much every this many days |
| Bedtime potty window | 45 minutes | A potty this long before bedtime counts as the bedtime one |
| Starting bedtime / wake-up | 23:00 / 06:30 | Until two nights are logged |
| Learn from accidents | on | Move predictions earlier when accidents keep coming before them |
| Largest accident adjustment | 90 minutes | The most predictions are moved earlier |
