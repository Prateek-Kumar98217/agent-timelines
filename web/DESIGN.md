# Design notes

The screen is one black map of agent steps that falls from top to bottom. Runs go straight down and
every fork steps to the right. Everything else slides in over it when asked for.

- **Colour:** canvas `#000000`, blocks `#141414`, lines `#3A3A3A`, text `#F2EFEA`, orange `#FF7A00`.
  Orange only ever means "you changed this" or "act here".
- **Step types:** each has its own tag colour and name. Task off-white, You violet `#C4A7FF`, Model
  blue `#62C6FF`, Tool call green `#3DDC97`, Tool result yellow `#F2D544`. Answers invert to a light
  block, so outcomes stand out at any zoom.
- **Type:** Overpass, from highway signage. Overpass Mono is only for real tool arguments and raw
  results, never for labels.
- **Motion:** a new step pops up, then its arrow draws from the previous step down to it. Steps that
  arrive together play one after another. Sidebars slide. Lamps pulse only on running branches. All of
  it stops under reduced motion.
- **Words:** say "step" and "branch". The action is "Create branch" everywhere.
