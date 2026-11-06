# Design notes

The product is tracks that split at junctions, so the interface borrows from a railway signal-box
panel. A run is a straight line, an edit is a junction, and a status is a signal lamp.

- **Colour:** enamel `#D5E0DA`, card stock `#F3F6F1`, ink `#102A2E`, track `#2C5A63`, edit cobalt
  `#1F45D1`. Lamps: green `#12804F`, amber `#B86E00`, red `#C4261D`, grey `#5F6E6C`. Cobalt only ever
  means "someone edited this".
- **Type:** Overpass, from highway signage. Overpass Mono is only for real tool arguments and raw
  results, never for labels.
- **Layout:** the map is the one bold element and everything around it stays quiet. Left aligned.
- **Motion:** the home track draws once, and lamps pulse only on running branches. Both stop under
  reduced motion.
- **Words:** say "step" and "branch". The action is "Create branch" everywhere.
- **Shape encodes step type**, so colour is never the only signal: circle for you and the model,
  square for tools, filled for what acts, hollow for what comes back, a bar for a final answer.
