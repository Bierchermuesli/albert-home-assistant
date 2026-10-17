# -*- coding: utf-8 -*-
""" View and control devices of your HomeAssistant.io instance.

Synopsis: <trigger> <entity filter>"""

from albert import *
import requests
import threading
from pathlib import Path
import re
import time
from fnmatch import fnmatchcase
from functools import partial
import xml.etree.ElementTree as ET
import hashlib

md_iid = "5.0"
md_version = "3.6"

# Verb-first command mode: the first word of the query is a verb that restricts the candidate
# domains, so "play office" never lists office lamps. (verb, domain) -> (service, label).
# {pct} is filled in for the verbs that take a number.
VERB_SERVICES = {
    ("play",       "media_player"):  ("media_player.media_play",           "Play {name}"),
    ("pause",      "media_player"):  ("media_player.media_pause",          "Pause {name}"),
    ("next",       "media_player"):  ("media_player.media_next_track",     "Next track on {name}"),
    ("prev",       "media_player"):  ("media_player.media_previous_track", "Previous track on {name}"),
    ("vol",        "media_player"):  ("media_player.volume_set",           "Set {name} volume to {pct}%"),
    ("volume",     "media_player"):  ("media_player.volume_set",           "Set {name} volume to {pct}%"),
    ("mute",       "media_player"):  ("media_player.volume_mute",          "Mute {name}"),
    ("unmute",     "media_player"):  ("media_player.volume_mute",          "Unmute {name}"),
    ("bright",     "light"):         ("light.turn_on",                     "Set {name} brightness to {pct}%"),
    ("brightness", "light"):         ("light.turn_on",                     "Set {name} brightness to {pct}%"),
    ("on",         "light"):         ("light.turn_on",                     "Turn on {name}"),
    ("on",         "switch"):        ("switch.turn_on",                    "Turn on {name}"),
    ("on",         "fan"):           ("fan.turn_on",                       "Turn on {name}"),
    ("on",         "input_boolean"): ("input_boolean.turn_on",             "Turn on {name}"),
    ("on",         "scene"):         ("scene.turn_on",                     "Activate {name}"),
    ("on",         "script"):        ("script.turn_on",                    "Run {name}"),
    ("on",         "cover"):         ("cover.open_cover",                  "Open {name}"),
    ("on",         "media_player"):  ("media_player.media_play",           "Play {name}"),
    ("off",        "light"):         ("light.turn_off",                    "Turn off {name}"),
    ("off",        "switch"):        ("switch.turn_off",                   "Turn off {name}"),
    ("off",        "fan"):           ("fan.turn_off",                      "Turn off {name}"),
    ("off",        "input_boolean"): ("input_boolean.turn_off",            "Turn off {name}"),
    ("off",        "cover"):         ("cover.close_cover",                 "Close {name}"),
    ("off",        "media_player"):  ("media_player.media_stop",           "Stop {name}"),
    ("toggle",     "light"):         ("light.toggle",                      "Toggle {name}"),
    ("toggle",     "switch"):        ("switch.toggle",                     "Toggle {name}"),
    ("toggle",     "fan"):           ("fan.toggle",                        "Toggle {name}"),
    ("toggle",     "input_boolean"): ("input_boolean.toggle",              "Toggle {name}"),
    ("toggle",     "cover"):         ("cover.toggle",                      "Toggle {name}"),
}

VERB_DOMAINS = {}
for _verb, _domain in VERB_SERVICES:
    VERB_DOMAINS.setdefault(_verb, []).append(_domain)

VALUE_REQUIRED_VERBS = {"vol", "volume", "bright", "brightness"}

# Fixed actions per domain: (action id, label, service, extra service data).
DOMAIN_ACTIONS = {
    "light": [("toggle", "Toggle", "light.toggle", {})]
             + [(f"set_{p}", f"Set to {p}%", "light.turn_on", {"brightness_pct": p}) for p in (20, 40, 60, 80)],
    "switch": [("toggle", "Toggle", "switch.toggle", {})],
    "fan": [("toggle", "Toggle", "fan.toggle", {})],
    "input_boolean": [("toggle", "Toggle", "input_boolean.toggle", {})],
    "scene": [("turn_on", "Turn On", "scene.turn_on", {})],
    "script": [("turn_on", "Turn On", "script.turn_on", {})],
    "automation": [("trigger", "Trigger", "automation.trigger", {})],
    "cover": [("toggle", "Toggle", "cover.toggle", {}),
              ("open", "Open", "cover.open_cover", {}),
              ("close", "Close", "cover.close_cover", {})],
}

# media_player supported_features bits, see homeassistant.components.media_player.const
MP_PAUSE, MP_VOLUME_SET, MP_VOLUME_MUTE = 1, 4, 8
MP_PREVIOUS_TRACK, MP_NEXT_TRACK, MP_VOLUME_STEP, MP_PLAY = 16, 32, 1024, 16384

md_name = "Home Assistant"
md_description = "View and control Home Assistant devices"
md_license = "MIT"
md_url = "https://github.com/Bierchermuesli/albert-home-assistant"
md_authors = ["@Bierchermuesli"]
md_lib_dependencies = ["requests"]

class Plugin(PluginInstance, GeneratorQueryHandler):
    # --- private attributes
    _internal_url = ""
    _public_url = ""
    _hass_key = ""
    _blacklist = "update.*, button.*, *.test_*, *_rssi"
    _state_blacklist = "unknown,unavailable"
    _use_mdi_icons = True
    _cache_seconds = 30
    _favorites = ""

    # --- plugin state
    headers = {}
    entities_cache = None
    last_entities_fetch_time = 0
    active_url = ""

    def __init__(self):
        GeneratorQueryHandler.__init__(self)
        PluginInstance.__init__(self)
        self.icons_path = Path(__file__).parent / "icons"
        self.icon_path = self.icons_path / "home-assistant.svg"
        self.icon_cache_path = Path(self.cacheLocation()) / "icons"
        self.icon_cache_path.mkdir(parents=True, exist_ok=True)

        self._download_lock = threading.Lock()
        self._pending_downloads = set()

        self._init_configuration()
        self._update_headers()
        self.active_url = self._internal_url if self._internal_url else self._public_url

    # --- properties for settings
    @property
    def internal_url(self):
        return self._internal_url

    @internal_url.setter
    def internal_url(self, value):
        self._internal_url = value.strip().rstrip("/")
        self.writeConfig("internal_url", self._internal_url)
        self.active_url = self._internal_url or self._public_url

    @property
    def public_url(self):
        return self._public_url

    @public_url.setter
    def public_url(self, value):
        self._public_url = value.strip().rstrip("/")
        self.writeConfig("public_url", self._public_url)
        if not self.active_url:
            self.active_url = self._public_url
        
    @property
    def hass_key(self):
        return self._hass_key

    @hass_key.setter
    def hass_key(self, value):
        self._hass_key = value
        self.writeConfig("hass_key", value)
        self._update_headers()
    
    @property
    def blacklist(self):
        return self._blacklist

    @blacklist.setter
    def blacklist(self, value):
        self._blacklist = value
        self.writeConfig("blacklist", value)
        self.blacklist_patterns = self._split_list(value)
        
    @property
    def state_blacklist(self):
        return self._state_blacklist

    @state_blacklist.setter
    def state_blacklist(self, value):
        self._state_blacklist = value
        self.writeConfig("state_blacklist", value)
        self.state_blacklist_items = set(self._split_list(value))

    @property
    def use_mdi_icons(self):
        return self._use_mdi_icons

    @use_mdi_icons.setter
    def use_mdi_icons(self, value):
        self._use_mdi_icons = value
        self.writeConfig("use_mdi_icons", value)

    @property
    def cache_seconds(self):
        return self._cache_seconds

    @cache_seconds.setter
    def cache_seconds(self, value):
        self._cache_seconds = value
        self.writeConfig("cache_seconds", value)
        
    @property
    def favorites(self):
        return self._favorites

    @favorites.setter
    def favorites(self, value):
        self._favorites = value
        self.writeConfig("favorites", value)
        self.favorite_items = set(self._split_list(value))

    @staticmethod
    def _split_list(value):
        """Items of a comma (or whitespace) separated setting."""
        return [v for v in re.split(r"[,\s]+", value) if v]

    def _init_configuration(self):
        """Load settings from config file or set defaults."""
        for key, type, default in [
            ("internal_url", str, self._internal_url),
            ("public_url", str, self._public_url),
            ("hass_key", str, self._hass_key),
            ("blacklist", str, self._blacklist),
            ("state_blacklist", str, self._state_blacklist),
            ("use_mdi_icons", bool, self._use_mdi_icons),
            ("cache_seconds", int, self._cache_seconds),
            ("favorites", str, self._favorites),
        ]:
            conf = self.readConfig(key, type)
            if conf is None:
                self.writeConfig(key, default)
                setattr(self, f"_{key}", default) 
            else:
                setattr(self, f"_{key}", conf)
        
        self.blacklist_patterns = self._split_list(self._blacklist)
        self.state_blacklist_items = set(self._split_list(self._state_blacklist))
        self.favorite_items = set(self._split_list(self._favorites))
    
    def configWidget(self):
        return [
            {"type": "label", "text": "Connect to your Home Assistant instance."},
            {"type": "lineedit", "label": "Internal URL", "property": "internal_url", "widget_properties": {"placeholderText": "e.g., http://homeassistant.local:8123"}},
            {"type": "lineedit", "label": "Public URL", "property": "public_url", "widget_properties": {"placeholderText": "e.g., https://your-domain.duckdns.org"}},
            {"type": "lineedit", "label": "Long-Lived Access Token", "property": "hass_key"},
            {"type": "label", "text": "<a href=\"https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token\">Click here for documentation on creating tokens.</a>"},
            {"type": "label", "text": "<b>Appearance & Behavior</b>"},
            {"type": "spinbox", "label": "Entity cache duration (seconds)", "property": "cache_seconds", "widget_properties": {"minimum": 5, "maximum": 300}},
            {"type": "checkbox", "label": "Use MDI icons from Home Assistant", "property": "use_mdi_icons"},
            {"type": "label", "text": "<b>Favorites</b>"},
            {"type": "lineedit", "label": "Favorite Entities", "property": "favorites", "widget_properties": {"placeholderText": "light.living_room, switch.desk_fan, ..."}},
            {"type": "label", "text": "Comma-separated list of favorite entity IDs to show on an empty query."},
            {"type": "label", "text": "<b>Blacklists</b>"},
            {"type": "lineedit", "label": "Entity ID Blacklist", "property": "blacklist"},
            {"type": "label", "text": "Comma-separated entity ids to hide, shell wildcards (* ? [..]). E.g.: <i>update.*, *_rssi</i>"},
            {"type": "lineedit", "label": "State Blacklist", "property": "state_blacklist"},
            {"type": "label", "text": "Comma-separated states to hide. E.g.: <i>unknown, unavailable</i>"},
        ]

    def _plugin_icon(self):
        return Icon.image(str(self.icon_path))

    def _error_item(self, e):
        return StandardItem(id="error", text="API Request Error", subtext=str(e),
                            icon_factory=self._plugin_icon)

    def _update_headers(self):
        self.headers = {"Authorization": f"Bearer {self.hass_key}", "content-type": "application/json"}

    def defaultTrigger(self):
        return "ha "

    def synopsis(self, query):
        return "<filter> | play|pause|next|prev|mute|unmute <filter> | vol|bright <filter> <pct> | on|off|toggle <filter> | info"

    def _get_colored_icon_path(self, icon_path: Path, color: str) -> Path:
        colored_icon_path = self.icon_cache_path / f"{icon_path.stem}-{color}.svg"
        if colored_icon_path.exists():
            return colored_icon_path
        
        try:
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            tree = ET.parse(icon_path)
            for path in tree.findall(".//{http://www.w3.org/2000/svg}path"):
                path.set("fill", color)
            tree.write(colored_icon_path)
            return colored_icon_path
        except Exception as e:
            warning(f"Failed to create colored icon for {icon_path.name}: {e}")
            return icon_path

    def _is_hass_url(self, url: str) -> bool:
        return any(url.startswith(u) for u in (self._internal_url, self._public_url) if u)

    def _background_download(self, key: str, fetch):
        with self._download_lock:
            if key in self._pending_downloads:
                return
            self._pending_downloads.add(key)

        def _run():
            try:
                fetch()
            finally:
                with self._download_lock:
                    self._pending_downloads.discard(key)

        threading.Thread(target=_run, daemon=True).start()

    def _get_url_icon_path(self, url: str) -> Path | None:
        if not url: return None
        url_hash = hashlib.sha1(url.encode()).hexdigest()
        cached_path = self.icon_cache_path / url_hash
        if cached_path.exists():
            return cached_path

        if url.startswith('/api/'):
            full_url = f"{self.active_url}{url}"
            headers = self.headers
        else:
            full_url = url
            headers = self.headers if self._is_hass_url(url) else None

        def _fetch():
            try:
                response = requests.get(full_url, timeout=5, headers=headers)
                if response.status_code == 200:
                    with open(cached_path, 'wb') as f:
                        f.write(response.content)
            except requests.exceptions.RequestException as e:
                warning(f"Failed to download entity picture '{url}': {e}")

        self._background_download(f"url:{url_hash}", _fetch)
        return None

    def _get_icon_path(self, entity: dict) -> Path:
        attributes = entity.get("attributes", {})
        domain = entity.get("entity_id", "").split('.')[0]
        state = entity.get("state")
        
        base_icon_path = None

        if self.use_mdi_icons and (mdi_icon := attributes.get("icon")):
            if mdi_icon.startswith("mdi:"):
                mdi_name = mdi_icon.replace("mdi:", "")
                cached_icon_path = self.icon_cache_path / f"{mdi_name}.svg"
                if cached_icon_path.exists():
                    base_icon_path = cached_icon_path
                else:
                    def _fetch_mdi(name=mdi_name, dest=cached_icon_path):
                        try:
                            url = f"https://cdn.jsdelivr.net/npm/@mdi/svg/svg/{name}.svg"
                            response = requests.get(url, timeout=5)
                            if response.status_code == 200:
                                with open(dest, 'wb') as f:
                                    f.write(response.content)
                        except requests.exceptions.RequestException as e:
                            warning(f"Failed to download MDI icon '{name}': {e}")

                    self._background_download(f"mdi:{mdi_name}", _fetch_mdi)
        
        if not base_icon_path:
            for name in (attributes.get("device_class"), attributes.get("unit_of_measurement"), domain):
                if name and (self.icons_path / f"{name}.svg").exists():
                    base_icon_path = self.icons_path / f"{name}.svg"
                    break

        if domain == "media_player" and state in ["playing", "paused", "stopped"]:
            base_icon_path = self.icons_path / f"speaker-{state}.svg"

        if state in ["on","playing"] and base_icon_path and base_icon_path.suffix == ".svg":
            return self._get_colored_icon_path(base_icon_path, "#FFC107")

        return base_icon_path or self.icon_path

    def _is_blacklisted(self, entity_id: str) -> bool:
        return any(fnmatchcase(entity_id, pattern) for pattern in self.blacklist_patterns)

    def _toggle_active_url(self):
        if not (self._internal_url and self._public_url):
            warning("Both internal and public URLs must be configured to toggle.")
            return

        if self.active_url == self._internal_url:
            self.active_url = self._public_url
            info(f"Switched active Home Assistant URL to public: {self.active_url}")
        else:
            self.active_url = self._internal_url
            info(f"Switched active Home Assistant URL to internal: {self.active_url}")

    def _make_api_request(self, method, endpoint, **kwargs):
        urls_to_try = [self.active_url, self.public_url if self.active_url == self.internal_url else self.internal_url]
        last_exception = None
        for url in filter(None, urls_to_try):
            try:
                response = requests.request(method, f"{url}{endpoint}", headers=self.headers, timeout=3, **kwargs)
                response.raise_for_status()
                if self.active_url != url:
                    info(f"Switched active Home Assistant URL to: {url}")
                    self.active_url = url
                return response
            except requests.exceptions.RequestException as e:
                last_exception = e
                warning(f"API request to {url} failed: {e}")
        raise last_exception

    def _get_entities(self):
        if self.entities_cache and (time.time() - self.last_entities_fetch_time) < self.cache_seconds:
            return self.entities_cache
        else:
            response = self._make_api_request("get", "/api/states")
            entities = response.json()
            self.entities_cache = entities
            self.last_entities_fetch_time = time.time()
            return entities

    def items(self, ctx):
        if not (self.internal_url or self.public_url) or not self.hass_key:
            yield [StandardItem(id=md_name, text="Not configured", subtext="Set the URL and access token in the plugin settings",
                                icon_factory=self._plugin_icon)]
            return

        query = ctx.query.strip()
        try:
            entities = self._get_entities()
        except requests.exceptions.RequestException as e:
            yield [self._error_item(e)]
            return

        if query == "info":
            yield self._info_items(entities)
        elif not query:
            if self.favorite_items:
                yield self._entity_items([e for e in entities if e["entity_id"] in self.favorite_items])
            else:
                yield [StandardItem(id=md_name, text=md_name, subtext=f"Using: {self.active_url}", icon_factory=self._plugin_icon,
                                    actions=[Action("url", "Open Home Assistant", lambda: openUrl(self.active_url))])]
        elif verb_query := self._parse_verb_query(query):
            verb, terms, value = verb_query
            yield self._entity_items(entities, terms, verb, value)
        else:
            yield self._entity_items(entities, query.lower().split())

    def _parse_verb_query(self, query: str):
        """(verb, filter terms, value) if the first word is a known verb, else None."""
        verb, *rest = query.split()
        verb = verb.lower()
        if verb not in VERB_DOMAINS:
            return None
        value = None
        if verb in VALUE_REQUIRED_VERBS:
            # The last purely numeric word is the value, the rest is the filter.
            for i in range(len(rest) - 1, -1, -1):
                if rest[i].replace(".", "", 1).isdigit():
                    value = float(rest.pop(i))
                    break
        return verb, [t.lower() for t in rest], value

    def _entity_items(self, entities, terms=(), verb=None, value=None):
        """Items for the entities matching every term in id or name, by name. With a verb only
        its domains are candidates and the verb's action comes first."""
        domains = VERB_DOMAINS.get(verb) if verb else None
        matching = []
        for entity in entities:
            entity_id = entity["entity_id"]
            if self._is_blacklisted(entity_id):
                continue
            if domains and entity_id.split(".")[0] not in domains:
                continue
            haystack = f"{entity_id} {self._name(entity)}".lower()
            if all(term in haystack for term in terms):
                matching.append(entity)

        results = []
        for entity in sorted(matching, key=self._name):
            prepend = []
            if verb and (action := self._verb_action(verb, entity, value)):
                prepend.append(action)
            if item := self._build_item(entity, prepend, completion_prefix=verb or ""):
                results.append(item)
            if not verb and entity["entity_id"].startswith("media_player.") and entity.get("state") == "playing":
                if now_playing := self._build_now_playing_item(entity):
                    results.append(now_playing)
        return results

    def _verb_action(self, verb, entity, value):
        """The action of a verb for an entity, or None when the verb does not apply to it."""
        entity_id = entity["entity_id"]
        domain = entity_id.split(".")[0]
        if (verb, domain) not in VERB_SERVICES or (verb in VALUE_REQUIRED_VERBS and value is None):
            return None
        service, label = VERB_SERVICES[(verb, domain)]
        data = {"entity_id": entity_id}
        pct = None
        if verb in ("vol", "volume"):
            level = max(0.0, min(1.0, value / 100))
            data["volume_level"] = level
            pct = int(level * 100)
        elif verb in ("bright", "brightness"):
            pct = max(0, min(100, int(value)))
            data["brightness_pct"] = pct
        elif verb == "mute":
            data["is_volume_muted"] = True
        elif verb == "unmute":
            data["is_volume_muted"] = False
        return Action(f"verb_{verb}", label.format(name=self._name(entity), pct=pct),
                      partial(self._call_service, service, data))

    def _info_items(self, entities):
        visible = sum(1 for e in entities
                      if not self._is_blacklisted(e["entity_id"])
                      and e.get("state", "unknown") not in self.state_blacklist_items)
        toggle_actions = []
        subtext = "The current URL used for API requests."
        if self._internal_url and self._public_url:
            toggle_actions.append(Action("toggle_url", "Toggle Active URL", self._toggle_active_url))
            which = "internal" if self.active_url == self._internal_url else "public"
            other = "public" if which == "internal" else "internal"
            subtext = f"Using {which} URL. Action to switch to {other}."
        return [
            StandardItem(id="info_url", text=f"Active URL: {self.active_url}", subtext=subtext,
                         icon_factory=self._plugin_icon, actions=toggle_actions),
            StandardItem(id="info_total", text=f"Total entities: {len(entities)}",
                         subtext="Number of entities exposed by the Home Assistant API",
                         icon_factory=self._plugin_icon),
            StandardItem(id="info_visible", text=f"Visible entities: {visible}",
                         subtext="Entities remaining after applying blacklists",
                         icon_factory=self._plugin_icon),
        ]

    def _call_service(self, service: str, data: dict):
        domain, service_name = service.split(".")
        try:
            self._make_api_request("post", f"/api/services/{domain}/{service_name}", json=data)
            info(f"Called service '{service}' on '{data.get('entity_id')}' successfully.")
        except requests.exceptions.RequestException as e:
            warning(f"Error calling service '{service}' on '{data.get('entity_id')}': {e}")

    @staticmethod
    def _name(entity):
        return entity.get("attributes", {}).get("friendly_name", entity["entity_id"])

    @staticmethod
    def _format_state(state: str, unit: str | None) -> str:
        try:
            number = float(state)
        except (ValueError, TypeError):
            return state.replace("_", " ").capitalize()
        formatted = f"{number:.2f}".rstrip("0").rstrip(".")
        return f"{formatted} {unit}" if unit else formatted

    def _build_now_playing_item(self, entity: dict):
        attributes = entity.get("attributes", {})
        title = attributes.get("media_title")
        if not title:
            return None
        artist = attributes.get("media_artist")
        album = attributes.get("media_album_name")
        text = f"{artist} - {album}" if artist and album else artist or album or "unknown Track/Artist"
        icon_path = self._get_url_icon_path(attributes.get("entity_picture")) or self.icon_path
        return StandardItem(
            id=f"now_playing_{entity['entity_id']}", text=text, subtext="now playing",
            icon_factory=partial(Icon.image, str(icon_path)),
            actions=[Action("copy_title", "Copy Title", partial(setClipboardText, title)),
                     Action("copy_artist", "Copy Artist", partial(setClipboardText, artist or ""))]
        )

    def _media_player_actions(self, entity):
        """Actions a media player supports, by its supported_features bits."""
        entity_id, state = entity["entity_id"], entity.get("state")
        attributes = entity.get("attributes", {})
        features = attributes.get("supported_features", 0)
        actions = []

        def add(action_id, label, service, **data):
            actions.append(Action(action_id, label,
                                  partial(self._call_service, f"media_player.{service}", {"entity_id": entity_id, **data})))

        if state != "paused" and features & MP_PAUSE:
            add("pause", "Pause", "media_pause")
        if state != "playing" and features & MP_PLAY:
            add("play", "Play", "media_play")
        if features & MP_NEXT_TRACK:
            add("next", "Next Track", "media_next_track")
        if features & MP_PREVIOUS_TRACK:
            add("prev", "Previous Track", "media_previous_track")
        if features & MP_VOLUME_MUTE:
            muted = bool(attributes.get("is_volume_muted"))
            add("mute", "Unmute" if muted else "Mute", "volume_mute", is_volume_muted=not muted)
        if features & MP_VOLUME_SET:
            for pct in (20, 40, 60, 80, 100):
                add(f"vol_{pct}", f"Set Volume to {pct}%", "volume_set", volume_level=pct / 100)
        if features & MP_VOLUME_STEP:
            add("volume_up", "Volume Up", "volume_up")
            add("volume_down", "Volume Down", "volume_down")
        return actions

    def _input_number_actions(self, entity):
        """One action per step of the number's range."""
        entity_id = entity["entity_id"]
        attributes = entity.get("attributes", {})
        low, high, step = (float(attributes.get(k, 0.0)) for k in ("min", "max", "step"))
        if step <= 0 or high <= low:
            step = max(high - low, 1.0)
        actions = []
        value = low
        while round(value, 4) <= round(high, 4):
            actions.append(Action(f"set_{value}", f"Set to {value}",
                                  partial(self._call_service, "input_number.set_value", {"entity_id": entity_id, "value": value})))
            value += step
        return actions

    def _build_item(self, entity: dict, prepend_actions=(), completion_prefix=""):
        entity_id = entity.get("entity_id")
        if not entity_id:
            return None
        state = entity.get("state", "unknown")
        if state in self.state_blacklist_items:
            return None

        domain = entity_id.split(".")[0]
        attributes = entity.get("attributes", {})
        friendly_name = self._name(entity)
        formatted_state = self._format_state(state, attributes.get("unit_of_measurement"))

        actions = list(prepend_actions)
        if domain == "media_player":
            actions += self._media_player_actions(entity)
        elif domain == "input_number":
            actions += self._input_number_actions(entity)
        else:
            for action_id, label, service, data in DOMAIN_ACTIONS.get(domain, []):
                actions.append(Action(action_id, label,
                                      partial(self._call_service, service, {"entity_id": entity_id, **data})))
        actions.append(Action("copy_entity_id", f"Copy '{entity_id}'", partial(setClipboardText, entity_id)))
        actions.append(Action("copy_state", f"Copy '{formatted_state}'", partial(setClipboardText, formatted_state)))

        subtext = f"{domain.replace('_', ' ').title()} is {formatted_state}"
        if domain == "media_player" and (volume := attributes.get("volume_level")) is not None:
            muted = " (muted)" if attributes.get("is_volume_muted") else ""
            subtext += f" · {int(volume * 100)}%{muted}"

        return StandardItem(
            id=entity_id,
            text=friendly_name,
            subtext=subtext,
            input_action_text=f"{completion_prefix} {friendly_name}".strip(),
            icon_factory=partial(Icon.image, str(self._get_icon_path(entity))),
            actions=actions
        )
