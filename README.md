# Home Assistant - Albert Python Extension

View and control your [Home Assistant](https://www.home-assistant.io/) devices directly from Albert.

## Features

- **Entity Control**: Toggle lights, switches, and covers; control media players; trigger scripts and automations; press buttons; pick select options; set input numbers.
- **Ranking**: Results are matched and ranked by Albert (word prefixes, any word order, no diacritics; a plain substring still matches, lower), and what you use often moves up. The empty query shows the ten entities you use most.
- **Domain specific features**:
  - **media_player** play, pause, next, previous, mute, volume steps (+10% / -10%), exact volume via `ha vol <name> <pct>`; an extra 'now playing' item with cover and title
  - **light** toggle and brightness steps (+10% / -10%); exact values via `ha bright <name> <pct>`
  - **input_number** selection of steps
  - **cover** open/close
- **Smart URL Switching**: Automatically switches between internal and public URLs based on connectivity.
- **Hidden entities**: Whole domains a launcher cannot use are left out (`update, event, notify, ...` by default, e.g. add `device_tracker, number`), noisy ids by pattern (`*_rssi`, `media_player.*_ma`), and states like `unavailable`. Everything else stays searchable, sensors included.
- **MDI Icons**: Automatically fetches and caches Material Design Icons from jsDelivr to match your Home Assistant UI (opt out possible).
- **Colored Icons**: Active entities (like lights that are 'on') are displayed with colored icons

## Icons

Every icon is a [Material Design Icon](https://pictogrammers.com/library/mdi/), named the way Home Assistant names it: the entity's own `icon` attribute, or the default the Home Assistant frontend derives from domain, device class and state (a thermometer for a temperature sensor, an open door for a door sensor that is on, ...). The mapping lives in a few tables at the top of the plugin.

A name resolves to the cached download, else to the small bundled seed in `icons/mdi` (the defaults, so it works offline), else the icon is downloaded from jsDelivr in a background thread while the Home Assistant logo stands in; the next query shows the proper icon. Downloads can be switched off in the settings. Active entities (lights that are on, players that play) get an amber tinted copy, done by rewriting the SVG fill, since the Albert API has no tinting primitive. Queries never wait on the network.

## Configuration

1. **Internal URL**: The local address of your Home Assistant instance (e.g., `http://homeassistant.local:8123`).
2. **Public URL (Optional)**: Your external address for when you are away from home.
3. **Long-Lived Access Token**: Generate this in your Home Assistant profile settings see [Guide](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token).

## Usage

Trigger `ha ` (configurable), then a name or entity id. The empty query shows the entities you use most. `Alt` shows all actions of an item.

### Verbs

Start with a verb to act directly. Only entities the verb applies to are listed, and the verb's action comes first.

| Verb | Applies to | Does |
|---|---|---|
| `play` | media player | play |
| `pause` | media player | pause |
| `next` / `prev` | media player | next / previous track |
| `mute` / `unmute` | media player | mute / unmute |
| `vol <name> [pct]` (or `volume`) | media player | set the volume; without a number the volume steps come first |
| `bright <name> [pct]` (or `brightness`) | light | set the brightness; without a number the brightness steps come first |
| `on` | light, switch, fan, input boolean, scene, script, cover, media player | turn on, activate, run, open, play |
| `off` | light, switch, fan, input boolean, cover, media player | turn off, close, stop |
| `toggle` | light, switch, fan, input boolean, cover | toggle |

### Commands

| Command | Does |
|---|---|
| `fav [filter]` | Sonos favorites; Enter plays one on the group that is playing now, the other actions pick a group or player. Needs the Sonos integration. |
| `todo [list] [text]` | To-do lists and their open items: complete, reopen, remove; offers to add the text when no open item matches. Without a list name it searches all lists. |
| `shop milk` | Same as `todo shop milk` when the first word starts a list name. |
| `info` | Active URL (with a switch between internal and public) and entity counts. |

### Steps

Brightness and volume steps read the current value when they run; their size is a setting (10 % brightness, 5 % volume by default). Players without a current level, like media player groups, get Home Assistant's own `Volume +` / `Volume -`. Steps and track skipping keep the Albert window open so Enter repeats them, where the Python plugin supports it (`hide_on_activation`, not yet in released Albert); with released Albert the window closes after each step.

## install

Simple clone to Albert plugin directory and activate in Albert Python Modules Settings. no special python dependencies are required.
```
git clone https://github.com/Bierchermuesli/albert-home-assistant.git ~/.local/share/albert/python/plugins/albert-home-assistant
```

## Work-in-Progress

- this plugin has some work-in-progress features and may not cover all Home Assistant domains or entity types.
- feel free to open issues or contribute to the project directly
- Icon/Coloring is a workaround (see [Icons](#icons)). Respect legal and licensing requirements.