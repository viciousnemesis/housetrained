# API

Housetrained's own API lives at `/api/v1/`. Baby Buddy's API at `/api/` only answers in baby mode.

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
