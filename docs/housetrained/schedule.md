# Next walk: the schedule and the coach

The goal is fewer accidents in the house: every one makes it harder to teach a puppy that outside is the place.
So the Now card follows a simple schedule that you set and can see, and the app only **suggests** changes.
Nothing about the timer is hidden or learned behind your back. It's a guide, not veterinary advice.

The code is `pets/schedule.py`; tests are in `pets/tests/test_schedule.py`.

## The schedule

Three numbers per pet (Setup > the pet), and the household's quiet hours and time to get outside (Setup >
Household settings):

| Setting | Example | Starts at |
|---|---|---|
| Pee every | 2h 30m | From the pet's age: about its age in months + 1 hour, 1 to 8 hours |
| Poop every | 5h | Twice pee every |
| Out after a meal | 15 min | 15 minutes |
| Quiet hours | 11 PM to 6:30 AM | 11 PM to 6:30 AM |
| Time to get outside | 10 min | 10 minutes |

From those and what's logged:

- **Next pee** = last pee + pee every. **Next poop** = last poop + poop every. With pee every half of poop every,
  every other walk expects a poop. If a poop was due and you were out without one, it's expected on the next walk
  (no extra walk for it).
- **Sooner** after eating or drinking (out after a meal; food brings a poop too, unless she pooped in the hour
  before eating), or right after waking from a nap. Never before you logged it, and never while she was asleep:
  a nap or meal logged afterwards is due **now**.
- **Back with nothing** (a walk without a pee, or **Nothing** on the Potty panel): try again 15 minutes later.
- **Quiet hours:** after the bedtime pee (in the hour before bedtime or later), the next one is at wake-up. A pee
  that would be due overnight goes on the bedtime walk.
- **Head out** = the earliest of those minus the time to get outside. Anything due within 30 minutes of it is
  expected on the same walk: "expect pee + poop". Tap *Why?* on the Now card to see each time and where it came
  from.
- **Disregard meal / wake-up**: went out after a meal or nap and nothing happened? Back to the schedule.

Always true (checked by the tests, including random days): logging a meal, drink or nap never makes head-out
later; time passing never makes it later; only logging a potty break or going out does.

## The coach

Puppy trainers stretch the time slowly after success and pull it back quickly after accidents. The coach does
the same, as a suggestion on the Now card with **Apply** and **Not now** (asks again in two days):

- **Two or more accidents** (pee, or poop) since the last change, within a week: "8 pee accidents this week came
  ~2h 31m after the last pee. Try every 2h 15m?" The suggestion is 15 minutes before they typically came, and
  always at least 15 minutes shorter than now.
- **A week without an accident** at the current setting (and at least 7 of that kind logged): "A week without a
  pee accident at every 2h 30m. Try 2h 45m?" Pee grows by 15 minutes, poop by 30, up to 8 and 12 hours.

After a change (applied or made on the pet's page) the coach waits for a fresh week.

## What you can see

The **Progress** page shows:

- **Time since the last pee**: for the past two weeks, how long after a pee the next one came, in half-hour steps,
  and how many were outside and how many were accidents. Where the accidents start is where pee every should be
  below.
- **By part of the day**: the usual gap between outdoor pees in the morning, afternoon and evening, and the
  accidents in each, with a headline when pee accidents cluster: "Most pee accidents are in the evening".

## Why not predict it?

We tried: usual gaps by time of day, learned meal delays, accident adjustments. Replayed against a real puppy's
two weeks, none of it did better than this schedule, and it was hard to trust or follow. A logged pee is when
someone took the pet out, not when it first needed to go, so a few weeks of logs can't pin down the moment it
needs to go; only accidents show that, and there are few. A schedule you set, plus a coach that reads the
accidents, is simpler and just as good.
