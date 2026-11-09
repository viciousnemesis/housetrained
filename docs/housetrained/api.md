# API

Housetrained's API lives at `/api/v1/`.

- Sign in with a session or the API key from user settings: `Authorization: Token <key>`.
- Everything is scoped to your household. Anyone in the household can read; owners and members can write;
  read-only members can't. Only owners can change the household itself.
- Amounts use the stored units, whatever the household displays: grams (food, litter), millilitres (water),
  kilograms (weight), centimetres (height, length), metres (walk distance).
- Walks and sleeps without an `end` are running timers. PATCH an `end` to stop one.
- A potty break posted without a `walk` is linked to a walk that was running at the time, like in the UI.
- A fill's `contents` comes from its container, and a food or litter fill without a `supply_item` uses the
  container's default bag.

| Resource | Filters |
|---|---|
| `households/` | |
| `pets/` | `type`, `active`, `slug` |
| `potty-breaks/` | `pet`, `location`, `walk`, `pee`, `poop` |
| `walks/` | `pets`, `walker` |
| `sleeps/` | `pet`, `kind` |
| `fills/` | `container`, `contents`, `supply_item` |
| `meals/` | `pet`, `product` |
| `measurements/` | `pet`, `kind` |
| `medications/` | `pet` |
| `notes/` | `pet` |
| `containers/` | `kind`, `active` |
| `products/` | `category` |
| `supplies/` | `product`, `finished` |

Lists are paginated with `limit` and `offset` (100 per page) and sort with `ordering`.

## Next walk (Home Assistant)

`GET /api/v1/pets/<id>/next-walk/` gives what the pet page shows: when to head out, or the walk that's running.

```json
{"pet": "Ruby", "state": "Head out ~4:15 PM", "head_out": "2026-11-08T16:15:00-08:00", "due": "in 25 min",
 "expect": "pee + poop", "on_walk_since": null}
```

`state` is ready to show ("Head out ~4:15 PM", "On a walk since 3:50 PM" or "Nothing logged yet"). Use an API
key from a read-only member: it can read this and nothing can be changed with it.

A Home Assistant REST sensor reads it, and the companion app shows it as a home-screen widget (Entity widget) or a
pinned notification (an automation on the sensor's changes sending `notify.mobile_app_<phone>` with `persistent`,
`sticky`, `tag`, `alert_once`, and `chronometer` + `when` for a live countdown).
