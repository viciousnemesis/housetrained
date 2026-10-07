# Pet data model

Tracking: PUP-51. Everything here lives in a new `pets` app; Baby Buddy's tables are not used for pet data.

## Overview

```
Household ──< Membership >── User
    │
    ├──< Pet ──< PottyBreak >── Walk (optional)
    │     │  └─< Sleep              └── Container (optional, e.g. the litter robot)
    │     │  └─< Meal              (a pet ate)
    │     │  └─< Measurement       (weight, height, length, ...)
    │     │  └─< Medication
    │     │  └─< Note
    │     │
    │     ├──<> Walk                (one or several pets per walk)
    │     └──<> Container           (bowls, feeders, litter boxes the pet uses)
    │
    ├──< Container ──< Fill >── SupplyItem (optional) >── Product
    │                 (food, water or litter put into a container)
    ├──< Product                    (what you buy)
    └──< SupplyItem                 (one bag or box; shared by pets)
```

`──<` one-to-many, `<>` many-to-many.

## Households

**Household**: name, display units (metric or imperial), time zone default.
**Membership**: user, household, role (owner, member, read-only).

Every pet, container, product and supply item belongs to a household, and every query is filtered by it. The UI
works with a single household for now (the user's only household, created automatically); switching between
households can come later without reshaping the data. Baby Buddy's own tables are not household-aware, which only
matters if baby mode is switched on.

## Pets

**Pet**: household, name, slug, type (dog, cat, other), breed (optional), sex (optional), birth date (+ "estimated"
flag), photo, active (false once rehomed or passed), notes.

Type drives defaults: dogs get walks and the Next Walk card; cats get litter boxes. Nothing is hard-wired to a type,
so a cat on a harness can still have walks.

## Activities

All activities record who logged them (`logged_by`, a user) and optional notes.

| Table | Fields | Notes |
|---|---|---|
| **Walk** | start, end (empty while in progress), pets (one or many), walker (user, optional), walker name (text, for non-users like a dog walker), distance (optional) | The same record serves a walk with one pet or with several. A walk with no end is the running timer, which replaces today's named "Walk" timers. |
| **PottyBreak** | pet, time, pee, poop, location (outside, inside, pad, litter box), walk (optional), container (optional) | Logged during a running walk, it links to that walk automatically; a walk is never required. Container identifies which litter box (e.g. the litter robot). |
| **Sleep** | pet, start, end (empty while in progress), kind (nap, night) | Same running-timer approach as walks. |
| **Meal** | pet, time, product (optional), amount eaten (optional) | "The pet ate." Separate from food going into a bowl. |
| **Measurement** | pet, date, kind (weight, height, length, ...), value, unit | One table for body measurements, so new kinds (e.g. neck size for a harness) need no new table. |
| **Medication** | pet, time, name, dose, next dose interval | Separate: has a dose and a schedule. |
| **Note** | pet (optional; empty = household note), time, text | Whether notes become a "type" alongside measurements is decided in PUP-53. |

## Food, water and supplies

The key split: **filling** a bowl, feeder or litter box is not the same as **a pet eating**. Filling is what draws
down a bag; eating is what a pet did.

**Container**: household, name, kind (food bowl, automatic feeder, water bowl, water fountain, litter box, automatic
litter box), pets (many: who eats, drinks from or uses it), default supply item (optional), amount per bowl
(calibration, optional).

**Fill**: something put into a container. Container, time, contents (food, water, litter), amount, how it was entered
(weight/volume, or bowls), supply item (optional; water has none). A fill into a shared container counts toward every
pet listed on that container.

**Product**: what you buy. Household, name, brand, category (dry food, wet food, treats, litter, other), usual package
size.

**SupplyItem**: one physical bag or box. Product, size (pre-filled from the product, e.g. 15 lb), opened, finished,
pets (many: who it is shared by), price (optional).

Product is a separate table so a product is entered once and each new bag just picks it: usage history and reorder
reminders carry over from bag to bag, and the bag size is pre-filled.

From fills:

- **Amount left** in a bag = size minus the fills drawn from it.
- **Usage rate** = recent fills per day, across every pet sharing the bag or container.
- **Days left** = amount left ÷ usage rate.
- **Reminder** when days left drops below a per-product threshold (default 7 days), on the dashboard first.
- **Water**: amount served per day per container (and per pet where only one pet uses it); no supply.

### Measuring in bowls

Households can log food and water by weight or volume (g, oz, lb, ml, fl oz) or in **bowls** (decimals allowed,
e.g. 0.5 bowl). Bowls only become amounts once a container is calibrated: tare the bowl, fill it as usual, and
enter the weight. Until a container is calibrated, logging in bowls shows a warning that food tracking and bag
estimates will be rough. The same applies to water bowls if the household chooses bowls for water.

## Units

Food and litter are stored in grams, water in millilitres. Entry accepts g, kg, oz, lb (mass) and ml, fl oz (volume),
or calibrated bowls. The household's display units (metric or imperial) decide what is shown.

## Migration of existing data (PUP-61)

| Baby Buddy | Housetrained |
|---|---|
| (none) | Household "Home", with every existing user as a member |
| Child (Ruby) | Pet, type dog |
| Diaper change (wet/solid, "color" = location) | PottyBreak (pee/poop, location) |
| Tummy time ("milestone" = walker) | Walk with one pet (walker matched to a user by first name, otherwise walker name) |
| Feeding with grams, e.g. "Filled bowl 60g" | Fill (food) into a default food bowl |
| Feeding without grams ("a couple of kibbles") | Meal |
| Notes mentioning water | Fill (water) where the note is clear, otherwise left as notes |
| Sleep (nap flag) | Sleep (kind) |
| Note | Note |
| Weight / Height | Measurement (head circumference and BMI dropped) |
| Medication | Medication |

A default food bowl and a "Dry food" product are created during migration; the bag size is unknown, so supply
tracking starts when the first bag is entered.

## Decided

1. Walks: one record serves single-pet and multi-pet walks.
2. Potty breaks link to the walk they happened on; a walk is never required.
3. Water is tracked like food (amount served per container), without a supply.
4. Potty locations: outside, inside, pad, litter box; a break can also name the specific box (e.g. the litter robot).
5. Supply items record a price (optional).
6. Data model is multi-household; the UI is single-household for now.
7. Notes vs. measurements: decided when implementing PUP-53.
8. Product stays its own table.

## Later

- Import litter robot visits (e.g. via Home Assistant) as cat potty breaks.
