# Brand assets

Staged icons for the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. This directory mirrors the exact path/layout that repo expects, so the
files can be copied straight into a brands pull request:

```
custom_integrations/ha_diamond_linq/
├── icon.png      256×256
└── icon@2x.png   512×512
```

Home Assistant and HACS render integration icons from `brands.home-assistant.io`
(served by the brands repo), not from this integration repository — these copies are
kept here as the source of truth.
