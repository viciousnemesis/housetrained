# Styles and icons

The gold theme lives in the SCSS source, not in the compiled CSS:

- `babybuddy/static_src/scss/_variables.scss`: gold `$primary`, with Bootstrap's `$blue`, `$cyan`,
  `$info`, `$debug` and the two lightest grays warmed to match.
- `babybuddy/static_src/icons/_icons.scss`: the food, walk and potty icons (CSS masks), imported after
  the icon font so they replace its glyphs.
- `babybuddy/static_src/logo/`, `root/` and `core/static_src/img/`: paw logo, favicons and pet placeholder.

To change the theme, edit the source, then rebuild with `npx gulp styles` and collect the static files.
`static/` is committed (whitenoise serves it with hashed names from `static/staticfiles.json`), so the
rebuilt `app.css` has to be copied in with a fresh hashed copy and manifest entry. Don't run
`collectstatic` in the repo folder without checking the result: it rewrites the whole manifest.
