# ADR 0001: Housetrained is an independent pet platform

- Status: accepted
- Date: 2026-10-07
- Tracking: PUP-50

## Context

Housetrained started as an add-on that patched Baby Buddy at build time: pet wording came from a translation
override, and pet data was stored in Baby Buddy's baby columns (diaper change "color" held the potty location, tummy
time "milestone" held the walker). Every new feature ran into the same limit: without our own tables and code, it
had to be squeezed into Baby Buddy's.

We also want to:

- support pets of any type (dog, cat, other), developing dog-first;
- track things Baby Buddy has no notion of: food put in a bowl or feeder versus a pet eating, shared bowls,
  bags of food and litter, and reminders to buy more;
- keep the option to switch Baby Buddy's baby tracking on later and use both in one app.

## Decision

1. Housetrained is a fork that we own outright. We do not merge Baby Buddy updates; fixes we want are ported by hand.
2. Pet features live in a new Django app (`pets`) with their own tables. Baby Buddy's tables and code (`core` and
   friends) stay in place, untouched by pet data.
3. Baby tracking becomes an optional module, hidden unless a site setting turns it on.
4. Pets have a type. Behavior that differs by type (walks for dogs, litter for cats) is driven by that type.
5. Existing data (Ruby) is migrated from Baby Buddy's tables into the pet tables once the models exist.

## Consequences

- Upgrading Django and dependencies is now our job; there is no upstream release to follow.
- Code shared with Baby Buddy (users, settings, theme, timeline framework) can be changed freely, but changes must
  keep baby mode working if it is ever switched on.
- The API gets pet endpoints of its own; Baby Buddy's endpoints stay for baby mode.
- Pet wording is written directly in the pet templates, so the en_US translation override can be removed.

## Update (PUP-145)

Baby mode was dropped: Housetrained tracks pets only. Baby Buddy's baby tracking, its API, translations and Home
Assistant support were removed and their tables dropped, so the points above about keeping baby mode working no
longer apply.
