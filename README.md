# Home Assistant - Albert Python Extension

View and control your [Home Assistant](https://www.home-assistant.io/) devices directly from Albert.

## Features

- **Entity Control**: Toggle lights, switches, and covers; control media players; trigger scripts and automations.
- **Favorites**: Define a list of favorite entities to be shown immediately when the trigger is used without a query.
- **Domain specific features**:
  - **media_player** Media Control. Extra item with 'now playing' with Cover and title 
  - **light** On/Off and Dimm
  - **input_number** selection of steps
  - **cover** open/close
- **Smart URL Switching**: Automatically switches between internal and public URLs based on connectivity.
- **Entity Filtering and Blacklisting**: Hide specific unwanted entities by regex (like `update\..*` `.*_rssi`) or by states (like `unavailable` or `unknown`) from search results.
- **MDI Icons**: Automatically fetches and caches Material Design Icons from jsDelivr to match your Home Assistant UI (opt out possible).
- **Colored Icons**: Active entities (like lights that are 'on') are displayed with colored icons

## Icons

Icon handling is admittedly a bit of a mess - but optimized for speed. The plugin keeps queries non-blocking by layering three sources:

1. **Cache hit** - cached MDI/entity-picture SVG is used immediately.
2. **Cache miss** - a small bundled fallback set (`icons/`, by domain / device_class / unit) is shown right away, and the missing MDI icon is downloaded from jsDelivr in a background thread. The next query for that entity uses the proper icon.
3. **Coloring** - "active" entities (e.g. lights that are `on`) are recolored by rewriting `fill` attributes in the SVG and caching the tinted result.

Result: queries never wait on the network, and your personal icon cache fills itself in as you use the plugin. Suggestions for a cleaner approach are welcome - the Albert 5.0 Python API has no built-in SVG tinting primitive, so the manual recolor stays for now.

## Configuration

1. **Internal URL**: The local address of your Home Assistant instance (e.g., `http://homeassistant.local:8123`).
2. **Public URL (Optional)**: Your external address for when you are away from home.
3. **Long-Lived Access Token**: Generate this in your Home Assistant profile settings see [Guide](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token).

## Usage

- Use the trigger `ha ` (configurable) followed by the entity name or ID.
- Type `ha info` to see connection status, option to Toggle internal/External URL and entity statistics.
- Use `ha ` with an empty query to see your favorite entities.
- Press `Alt` on an item to see additional domain-specific actions (e.g., setting light brightness).

## install

Simple clone to Albert plugin directory and activate in Albert Python Modules Settings. no special python dependencies are required.
```
git clone https://github.com/Bierchermuesli/albert-home-assistant.git ~/.local/share/albert/python/plugins/home-assistant
```

## Work-in-Progress

- this plugin has some work-in-progress features and may not cover all Home Assistant domains or entity types.
- feel free to open issues or contribute to the project directly
- Icon/Coloring is a workaround (see [Icons](#icons)). Respect legal and licensing requirements.