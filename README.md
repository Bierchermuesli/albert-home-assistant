# Home Assistant - Albert Python Extension

View and control your [Home Assistant](https://www.home-assistant.io/) devices directly from Albert.

## Features

- **Entity Control**: Toggle lights, switches, and covers; control media players; trigger scripts and automations.
- **Favorites**: Define a list of favorite entities to be shown immediately when the trigger is used without a query.
- **Domain specific features**:
  - **media_player** Media Control. Extra item with 'now playing' with Cover and title 
  - **light** On/Off Dimm
  - **input_number** selection of steps
  - **cover** open/close
- **Smart URL Switching**: Automatically switches between internal and public URLs based on connectivity.
- **Blacklisting**: Hide specific entities or states (like `unavailable` or `unknown`) from search results.
- **MDI Icons**: Automatically fetches and caches Material Design Icons from jsDelivr to match your Home Assistant UI (optional).
- **Colored Icons**: Active entities (like lights that are 'on') are displayed with colored icons for quick visual feedback.

## Configuration

1. **Internal URL**: The local address of your Home Assistant instance (e.g., `http://homeassistant.local:8123`).
2. **Public URL (Optional)**: Your external address for when you are away from home.
3. **Long-Lived Access Token**: Generate this in your Home Assistant profile settings see [Guide](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token).

## Usage

- Use the trigger `ha ` (configurable) followed by the entity name or ID.
- Type `ha info` to see connection status, option to Toggle internal/External URL and entity statistics.
- Use `ha ` with an empty query to see your favorite entities.
- Press `Alt` on an item to see additional domain-specific actions (e.g., setting light brightness).


## Work-in-Progress

- this plugin has some work-in-progress features and may not cover all Home Assistant domains or entity types.
- feel free to open issues or contribute to the project directly
- Icon/Coloring is a bit hacky - feedback and PRs welcome