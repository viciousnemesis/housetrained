# Next Walk predictions

The Now card answers one question: **when should this pet next go out?** It only uses that pet's own logs, it
works the same for any dog or puppy, and it's an estimate, not veterinary advice.

The code is `pets/insights.py` (`next_walk_card`). Every number below can be changed on the **Site Settings**
page (Predictions); the defaults suit most dogs. Tests: `pets/tests/test_predictions.py` and
`pets/tests/test_prediction_scenarios.py`.

## How it's worked out

Pees set the time. Poops only come into it after food.

1. **Usual gap.** The average gap between pees **that worked**: one outdoor pee to the next, with no accident in
   between, over the past **7 days**. Gaps under **30 minutes** (a top-up on the same walk) and over **4 hours**
   (a nap or a missed entry) are left out, and so are gaps starting at bedtime or during the night. Mornings
   (before noon), afternoons (noon to 5 PM) and evenings are averaged separately, because pets often hold it for
   less in the evening; with fewer than 3 gaps in a part of the day, the whole day's average is used.
   Next pee = **last pee + usual gap**: "evening pees that worked were ~2h apart".
2. **Not enough history yet:** the age rule of thumb, a puppy can hold it about its age in months plus one hour,
   capped at **8 hours**. Without a birth date the pet counts as a year old.
3. **Learning from accidents.** If pee accidents in a part of the day (2 or more in the past two weeks) typically
   came sooner after a pee than the gap above, that part of the day plans **15 minutes before** they typically
   came: "3 evening accidents came ~2h 12m after a pee, so ~1h 57m". Using the typical (median) accident, not the
   quickest, keeps one odd accident from shortening everything. Switch it off with *Learn from accidents*.
4. **Brought earlier by**, since the last pee: eating or drinking (**20 minutes** later), or waking up from a nap
   (right away). Only logged meals count (Log Meal, or Fill Bowl with "Eaten right away").
5. **Poops:** after food, about **30 minutes** later, unless she pooped in the hour before eating, or has pooped
   or been out since.
6. **Back with nothing:** a walk without a pee, an outdoor potty break that was only a poop, or **Nothing** on
   the Potty panel means try again **15 minutes** after getting back.
7. **The night:** after the bedtime pee, the next one is at the usual wake-up. A pee that would fall during the
   night goes on the bedtime walk instead.

**Head out** is the earliest need minus the household's *time to get outside* (Setup > Household settings,
default 10 minutes). "Due", "overdue" and the walk reminder count from it. Any other need due before that walk
would end (plus a 30-minute buffer) is expected on the same walk: "expect pee + poop".

**Disregard.** When a meal or wake-up sets the time and you went out and nothing happened, tap *Disregard meal*
(or *Disregard wake-up*) on the Now card: it goes back to the usual gap. The usual gap has no such button: once
it has passed, the pet stays overdue until a pee is logged.

## See it yourself

The **Progress** page has a *By part of the day* table: the usual gap for mornings, afternoons and evenings,
the accidents in each (pee and poop, past two weeks), and the gap Next Walk plans on (yellow where accidents
shortened it). When pee accidents cluster in one part of the day it says so: "Most pee accidents are in the
evening: 5 of 10 in the past two weeks."

## Guarantees

Checked by `pets/tests/test_prediction_scenarios.py`, including randomised routines:

1. Logging a meal or a drink never makes the estimate later.
2. Logging a wake-up never makes it later.
3. With nothing new logged, time passing never makes it later. Only logging something does.
4. Once the usual gap has passed, the pet stays overdue until a pee is logged.
5. Learning from accidents only ever brings the estimate forward.
6. Logging a pee is what moves the next pee later. So does going out and coming back with nothing: then the next
   walk is due **15 minutes** after getting back, and its reminder comes like any other.
7. A trigger is never due before you could have headed out for it: not while the pet was still asleep, and not
   before it was logged. A nap or meal logged after the fact is due **now**, not "overdue" for back then.

## How well it does

Replays of a real puppy's two weeks (every 5 minutes and after every entry) are how changes are judged:
how close the prediction made right after a pee was to the next one, how many accidents came before head-out,
how often the card says "head out" right after a potty, and whether it ever moved later with nothing logged.
A logged pee is when someone took the pet out, not when it first needed to, so accidents are the real test.

## The night

Each day's longest potty-free stretch (at least **4 hours**) is that day's night: the last break before it is
bedtime, the first after it is wake-up. Bedtime and wake-up are weighted medians over the past two weeks, recent
nights counting more (a night's weight halves every **3 days**). Until two nights are logged, bedtime is
**23:00** and wake-up **06:30**. A pee up to **45 minutes** before the usual bedtime counts as the bedtime one.
Accidents (inside) don't count when learning the night.

## Accidents vs. the prediction

When an accident (inside) is logged, the app stores what Next Walk expected just before it, using only what was
logged up to then. The Accident Times card lists recent accidents as "35m earlier than expected" or "20m later
than expected", with a summary.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Days for the usual gap | 7 | The usual gap is the average over this many days |
| Shortest gap that counts | 30 minutes | Shorter gaps are top-ups and are left out |
| Longest gap that counts | 4 hours | Longer gaps are naps or missed entries and are left out |
| Learn from accidents | on | Plan a part of the day earlier when its pee accidents come sooner |
| Accident margin | 15 minutes | How long before accidents typically came to plan |
| Pee after eating or drinking | 20 minutes | |
| Poop after eating | 30 minutes | |
| Try again after nothing | 15 minutes | Out and nothing happened: the next walk is due this long after getting back |
| Longest hold | 8 hours | Cap on the age rule of thumb |
| Longest real gap | 12 hours | For nights and walk timing: longer gaps are missed entries |
| Needs grouping buffer | 30 minutes | A need due this long after a walk would normally end is expected on that walk |
| Usual walk length | 15 minutes | Until walks are logged |
| Shortest night | 4 hours | Shortest stretch that counts as the night |
| Night fade | 3 days | An older night counts half as much every this many days |
| Bedtime potty window | 45 minutes | A potty this long before bedtime counts as the bedtime one |
| Starting bedtime / wake-up | 23:00 / 06:30 | Until two nights are logged |
