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
md_version = "3.7"

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

# 'fav' lists the Sonos favorites (sensor.*_favorites of the Sonos integration) to play on a
# Sonos player, i.e. a media_player with the group_members attribute.
FAVORITE_VERB = "fav"
FAVORITE_ICON = "star"

# 'todo' lists the to-do lists, 'todo <list> [text]' the open items of one with complete, reopen
# and remove, and adds the text as an item when no open item matches it.
TODO_VERB = "todo"
TODO_ICONS = {"needs_action": "checkbox-blank-outline", "completed": "checkbox-marked-outline"}

# Fixed actions per domain: (action id, label, service, extra service data).
DOMAIN_ACTIONS = {
    # light and media_player actions depend on the current value, see _light_actions and
    # _media_player_actions.
    "switch": [("toggle", "Toggle", "switch.toggle", {})],
    "fan": [("toggle", "Toggle", "fan.toggle", {})],
    "input_boolean": [("toggle", "Toggle", "input_boolean.toggle", {})],
    "scene": [("turn_on", "Turn On", "scene.turn_on", {})],
    "script": [("turn_on", "Turn On", "script.turn_on", {})],
    "automation": [("trigger", "Trigger", "automation.trigger", {})],
    "button": [("press", "Press", "button.press", {})],
    "input_button": [("press", "Press", "input_button.press", {})],
    "cover": [("toggle", "Toggle", "cover.toggle", {}),
              ("open", "Open", "cover.open_cover", {}),
              ("close", "Close", "cover.close_cover", {})],
}

# Default icons as the Home Assistant frontend derives them when an entity has no icon of its
# own: by domain, by sensor device_class, and by binary_sensor device_class and state.
# All names are Material Design Icons.
DOMAIN_ICONS = {
    "alarm_control_panel": "shield-home", "automation": "robot", "binary_sensor": "radiobox-blank",
    "button": "gesture-tap-button", "calendar": "calendar", "camera": "video", "climate": "thermostat",
    "counter": "counter", "cover": "window-shutter", "date": "calendar", "datetime": "calendar-clock",
    "device_tracker": "account", "event": "eye-check", "fan": "fan", "group": "google-circles-communities",
    "humidifier": "air-humidifier", "image": "image", "input_boolean": "toggle-switch-variant",
    "input_button": "gesture-tap-button", "input_datetime": "calendar-clock", "input_number": "ray-vertex",
    "input_select": "format-list-bulleted", "input_text": "form-textbox", "lawn_mower": "robot-mower",
    "light": "lightbulb", "lock": "lock", "media_player": "speaker", "notify": "message", "number": "ray-vertex",
    "person": "account", "plant": "flower", "remote": "remote", "scene": "palette", "schedule": "calendar-clock",
    "script": "script-text", "select": "format-list-bulleted", "sensor": "eye", "siren": "bullhorn",
    "sun": "white-balance-sunny", "switch": "toggle-switch-variant", "text": "form-textbox", "time": "clock",
    "timer": "timer-outline", "todo": "clipboard-list", "update": "package", "vacuum": "robot-vacuum",
    "valve": "pipe-valve", "water_heater": "water-boiler", "weather": "weather-partly-cloudy",
    "zone": "map-marker-radius",
}

SENSOR_CLASS_ICONS = {
    "apparent_power": "flash", "aqi": "air-filter", "atmospheric_pressure": "thermometer-lines",
    "battery": "battery", "carbon_dioxide": "molecule-co2", "carbon_monoxide": "molecule-co",
    "current": "current-ac", "data_rate": "transmission-tower", "data_size": "database", "date": "calendar",
    "distance": "arrow-left-right", "duration": "progress-clock", "energy": "lightning-bolt",
    "energy_storage": "car-battery", "frequency": "sine-wave", "gas": "meter-gas", "humidity": "water-percent",
    "illuminance": "brightness-5", "irradiance": "sun-wireless", "moisture": "water-percent", "monetary": "cash",
    "nitrogen_dioxide": "molecule", "nitrogen_monoxide": "molecule", "nitrous_oxide": "molecule", "ozone": "molecule",
    "ph": "ph", "pm1": "molecule", "pm10": "molecule", "pm25": "molecule", "power": "flash",
    "power_factor": "angle-acute", "precipitation": "weather-rainy", "precipitation_intensity": "weather-pouring",
    "pressure": "gauge", "reactive_power": "flash", "signal_strength": "wifi", "sound_pressure": "ear-hearing",
    "speed": "speedometer", "sulphur_dioxide": "molecule", "temperature": "thermometer", "timestamp": "clock",
    "volatile_organic_compounds": "molecule", "voltage": "sine-wave", "volume": "car-coolant-level",
    "volume_storage": "storage-tank", "water": "water", "weight": "weight", "wind_speed": "weather-windy",
}

# binary_sensor device_class -> (icon when off, icon when on)
BINARY_SENSOR_CLASS_ICONS = {
    "battery": ("battery", "battery-outline"), "battery_charging": ("battery", "battery-charging"),
    "carbon_monoxide": ("smoke-detector", "smoke-detector-alert"), "cold": ("thermometer", "snowflake"),
    "connectivity": ("close-network-outline", "check-network-outline"), "door": ("door-closed", "door-open"),
    "garage_door": ("garage", "garage-open"), "gas": ("check-circle", "alert-circle"),
    "heat": ("thermometer", "fire"), "light": ("brightness-5", "brightness-7"), "lock": ("lock", "lock-open"),
    "moisture": ("water-off", "water"), "motion": ("motion-sensor-off", "motion-sensor"),
    "moving": ("arrow-right", "arrow-right"), "occupancy": ("home-outline", "home"),
    "opening": ("square", "square-outline"), "plug": ("power-plug-off", "power-plug"),
    "power": ("power-plug-off", "power-plug"), "presence": ("home-outline", "home"),
    "problem": ("check-circle", "alert-circle"), "running": ("stop", "play"), "safety": ("check-circle", "alert-circle"),
    "smoke": ("smoke-detector-variant", "smoke-detector-variant-alert"), "sound": ("music-note-off", "music-note"),
    "tamper": ("check-circle", "alert-circle"), "update": ("package", "package-up"),
    "vibration": ("crop-portrait", "vibrate"), "window": ("window-closed", "window-open"),
}

# A few state dependent ones.
STATE_ICONS = {
    ("binary_sensor", "on"): "checkbox-marked-circle",
    ("cover", "open"): "window-shutter-open",
    ("input_boolean", "off"): "toggle-switch-variant-off", ("switch", "off"): "toggle-switch-variant-off",
    ("media_player", "playing"): "speaker-play", ("media_player", "paused"): "speaker-pause",
    ("media_player", "off"): "speaker-off",
    ("update", "on"): "package-up",
}

ACTIVE_STATES = {"on", "playing", "open"}

# Domains whose state is only the time of the last press/activation ('unknown' before the
# first one), so the hidden states do not apply and the subtext says when instead of what.
STATELESS_DOMAINS = {"button", "input_button", "scene", "event"}
ACTIVE_COLOR = "#FFC107"  # amber, as in the Home Assistant UI

# media_player supported_features bits, see homeassistant.components.media_player.const
MP_PAUSE, MP_VOLUME_SET, MP_VOLUME_MUTE = 1, 4, 8
MP_PREVIOUS_TRACK, MP_NEXT_TRACK, MP_VOLUME_STEP, MP_PLAY = 16, 32, 1024, 16384

md_name = "Home Assistant"
md_description = "View and control Home Assistant devices"
md_license = "MIT"
md_url = "https://github.com/Bierchermuesli/albert-home-assistant"
md_authors = ["@Bierchermuesli"]
md_lib_dependencies = ["requests"]

class EntityItem(Item):
    """An entity result whose subtext and icon follow the plugin's entity cache, so a row that
    is repainted after a step action shows the new value. Text and actions are fixed."""

    def __init__(self, plugin, entity, actions, completion):
        Item.__init__(self)
        self._plugin = plugin
        self._entity_id = entity["entity_id"]
        self._entity = entity
        self._text = plugin._name(entity)
        self._actions = actions
        self._completion = completion

    def _current(self):
        for entity in self._plugin.entities_cache or ():
            if entity["entity_id"] == self._entity_id:
                return entity
        return self._entity

    def id(self): return self._entity_id
    def text(self): return self._text
    def subtext(self): return self._plugin._subtext(self._current())
    def inputActionText(self): return self._completion
    def icon(self): return Icon.image(str(self._plugin._get_icon_path(self._current())))
    def actions(self): return self._actions


class Plugin(PluginInstance, RankedQueryHandler):
    # --- private attributes
    _internal_url = ""
    _public_url = ""
    _hass_key = ""
    # Domains a launcher cannot do anything with, and id patterns for the noise in the others.
    _hidden_domains = "update, event, notify, tts, stt, conversation, wake_word, zone, sun"
    _blacklist = "*.test_*, *_rssi"
    _state_blacklist = "unknown,unavailable"
    _use_mdi_icons = True
    _cache_seconds = 30

    # --- plugin state
    headers = {}
    entities_cache = None
    last_entities_fetch_time = 0
    active_url = ""

    def __init__(self):
        RankedQueryHandler.__init__(self)
        PluginInstance.__init__(self)
        self.icons_path = Path(__file__).parent / "icons" / "mdi"  # bundled seed, see Icons below
        self.icon_path = self.icons_path / "home-assistant.svg"
        self.icon_cache_path = Path(self.cacheLocation()) / "icons"
        self.icon_cache_path.mkdir(parents=True, exist_ok=True)

        self._download_lock = threading.Lock()
        self._pending_downloads = set()
        self.todo_cache = {}  # list entity id -> (fetch time, items)

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
    def hidden_domains(self):
        return self._hidden_domains

    @hidden_domains.setter
    def hidden_domains(self, value):
        self._hidden_domains = value
        self.writeConfig("hidden_domains", value)
        self.hidden_domain_set = set(self._split_list(value))

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
            ("hidden_domains", str, self._hidden_domains),
            ("blacklist", str, self._blacklist),
            ("state_blacklist", str, self._state_blacklist),
            ("use_mdi_icons", bool, self._use_mdi_icons),
            ("cache_seconds", int, self._cache_seconds),
        ]:
            conf = self.readConfig(key, type)
            if conf is None:
                self.writeConfig(key, default)
                setattr(self, f"_{key}", default) 
            else:
                setattr(self, f"_{key}", conf)
        
        self.hidden_domain_set = set(self._split_list(self._hidden_domains))
        self.blacklist_patterns = self._split_list(self._blacklist)
        self.state_blacklist_items = set(self._split_list(self._state_blacklist))
    
    def configWidget(self):
        return [
            {"type": "label", "text": "Connect to your Home Assistant instance."},
            {"type": "lineedit", "label": "Internal URL", "property": "internal_url", "widget_properties": {"placeholderText": "e.g., http://homeassistant.local:8123"}},
            {"type": "lineedit", "label": "Public URL", "property": "public_url", "widget_properties": {"placeholderText": "e.g., https://your-domain.duckdns.org"}},
            {"type": "lineedit", "label": "Long-Lived Access Token", "property": "hass_key"},
            {"type": "label", "text": "<a href=\"https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token\">Click here for documentation on creating tokens.</a>"},
            {"type": "label", "text": "<b>Appearance & Behavior</b>"},
            {"type": "spinbox", "label": "Entity cache duration (seconds)", "property": "cache_seconds", "widget_properties": {"minimum": 5, "maximum": 300}},
            {"type": "checkbox", "label": "Download missing icons from jsDelivr (MDI)", "property": "use_mdi_icons"},
            {"type": "label", "text": "<b>Hidden entities</b>"},
            {"type": "lineedit", "label": "Hidden domains", "property": "hidden_domains"},
            {"type": "label", "text": "Comma-separated domains to leave out entirely. E.g.: <i>update, device_tracker, number</i>"},
            {"type": "lineedit", "label": "Hidden entity ids", "property": "blacklist"},
            {"type": "label", "text": "Comma-separated id patterns with shell wildcards (* ? [..]). E.g.: <i>*_rssi, media_player.*_ma</i>"},
            {"type": "lineedit", "label": "Hidden states", "property": "state_blacklist"},
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
        return "<filter> | play|pause|next|prev|mute|unmute <filter> | vol|bright <filter> <pct> | on|off|toggle <filter> | fav <filter> | todo [list] [item] | info"

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

    def _is_blacklisted(self, entity_id: str) -> bool:
        return (entity_id.split(".")[0] in self.hidden_domain_set
                or any(fnmatchcase(entity_id, pattern) for pattern in self.blacklist_patterns))

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

    # The empty query shows the entities used most, ranked by Albert's usage scoring.
    EMPTY_QUERY_ITEMS = 10

    def items(self, ctx):
        # The base implementation applies the usage scoring in C++ and sorts lazily; only cut
        # its stream for the empty query. (Scoring from Python does not work: the binding
        # copies the list, the modified scores never come back.)
        shown = 0
        for batch in RankedQueryHandler.items(self, ctx):
            if not ctx.query.strip():
                batch = batch[:self.EMPTY_QUERY_ITEMS - shown]
                if not batch:
                    return
                shown += len(batch)
            yield batch

    def rankItems(self, ctx):
        if not (self.internal_url or self.public_url) or not self.hass_key:
            return [RankItem(StandardItem(id=md_name, text="Not configured",
                                          subtext="Set the URL and access token in the plugin settings",
                                          icon_factory=self._plugin_icon), 1.0)]
        query = ctx.query.strip()
        try:
            entities = self._get_entities()
        except requests.exceptions.RequestException as e:
            return [RankItem(self._error_item(e), 1.0)]

        if query == "info":
            return [RankItem(item, 1.0) for item in self._info_items(entities)]
        words = query.lower().split()
        if not words:
            return self._entity_items(entities, [])
        if words[0] == FAVORITE_VERB:
            return self._favorite_items(entities, words[1:])
        if words[0] == TODO_VERB:
            return self._todo_items(entities, query.split()[1:])
        if verb_query := self._parse_verb_query(query):
            verb, terms, value = verb_query
            return self._entity_items(entities, terms, verb, value)
        results = self._entity_items(entities, words)
        if self._todo_list_named(entities, words[0]):
            # 'shop milk' works like 'todo shop milk', ranked together with the entities.
            results += self._todo_items(entities, query.split())
        return results

    def _todo_list_named(self, entities, word):
        """The to-do list whose name starts with word (at least 3 letters), else None."""
        if len(word) < 3:
            return None
        for entity in entities:
            if entity["entity_id"].startswith("todo.") and not self._is_blacklisted(entity["entity_id"]):
                if any(w.startswith(word) for w in self._name(entity).lower().split()):
                    return entity
        return None

    @staticmethod
    def _scorer(words):
        """A function rating how well strings match the words: Albert's word-prefix score, a
        low score for a plain substring hit (so 'lamp' still finds 'StandLamp'), None for no
        match. Without words everything scores 0 and Albert's usage scoring orders the list."""
        if not words:
            return lambda *strings: 0.0
        matcher = Matcher(" ".join(words))

        def score(*strings):
            match = matcher.match(list(strings))
            if match:
                return match
            haystack = " ".join(strings).lower()
            return 0.1 if all(word in haystack for word in words) else None
        return score

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

    def _entity_items(self, entities, words, verb=None, value=None):
        """Ranked items for the entities matching the words by id or name. With a verb only its
        domains are candidates and the verb's action comes first."""
        score_of = self._scorer(words)
        domains = VERB_DOMAINS.get(verb) if verb else None
        results = []
        for entity in entities:
            entity_id = entity["entity_id"]
            if self._is_blacklisted(entity_id):
                continue
            if domains and entity_id.split(".")[0] not in domains:
                continue
            score = score_of(entity_id, self._name(entity))
            if score is None:
                continue
            prepend = []
            if verb and (action := self._verb_action(verb, entity, value)):
                prepend.append(action)
            # 'vol office' / 'bright desk' without a number: the steps come first.
            steps_first = verb in VALUE_REQUIRED_VERBS and value is None
            if item := self._build_item(entity, prepend, completion_prefix=verb or "", steps_first=steps_first):
                results.append(RankItem(item, score))
            if not verb and entity_id.startswith("media_player.") and entity.get("state") == "playing":
                if now_playing := self._build_now_playing_item(entity):
                    results.append(RankItem(now_playing, score))
        return results

    def _sonos_groups(self, entities):
        """[(coordinator entity, [member entities])] of the Sonos players, grouped as they are
        now. Sonos players are the media players with a group_members list."""
        players = {e["entity_id"]: e for e in entities
                   if e["entity_id"].startswith("media_player.") and e.get("attributes", {}).get("group_members")
                   and not self._is_blacklisted(e["entity_id"]) and e.get("state") not in self.state_blacklist_items}
        groups = {}
        for player in players.values():
            coordinator_id = player["attributes"]["group_members"][0]
            coordinator = players.get(coordinator_id, player)
            groups.setdefault(coordinator["entity_id"], (coordinator, []))[1].append(player)
        return list(groups.values())

    def _favorite_items(self, entities, words):
        """Ranked items, one per Sonos favorite that matches. Enter plays it on the group that
        is playing now (or the first one), the other actions pick another group or player."""
        favorites = {}
        for entity in entities:
            if entity["entity_id"].endswith("_favorites") and isinstance(entity.get("attributes", {}).get("items"), dict):
                favorites.update(entity["attributes"]["items"])
        groups = self._sonos_groups(entities)
        if not favorites or not groups:
            return [RankItem(StandardItem(id="no_favorites", text="No Sonos favorites",
                                          subtext="Needs the Sonos integration with a favorites sensor and a player",
                                          icon_factory=self._plugin_icon), 1.0)]

        def label(group):
            coordinator, members = group
            return self._name(coordinator) + (f" group ({len(members)})" if len(members) > 1 else "")

        ordered = sorted(groups, key=lambda g: (g[0].get("state") != "playing", self._name(g[0])))
        default = ordered[0]
        score_of = self._scorer(words)
        results = []
        for item_id, name in favorites.items():
            score = score_of(name)
            if score is None:
                continue
            actions = []
            for group in ordered:
                coordinator = group[0]
                data = {"entity_id": coordinator["entity_id"], "media_content_type": "favorite_item_id",
                        "media_content_id": item_id}
                actions.append(Action(f"play_{coordinator['entity_id']}", f"Play on {label(group)}",
                                      partial(self._call_service, "media_player.play_media", data)))
            results.append(RankItem(StandardItem(
                id=f"favorite_{item_id}",
                text=name,
                subtext=f"Sonos favorite · plays on {label(default)}",
                input_action_text=f"{FAVORITE_VERB} {name}",
                icon_factory=partial(Icon.image, str(self._mdi_icon_path(FAVORITE_ICON) or self.icon_path)),
                actions=actions
            ), score))
        return results

    # --- to-do lists --------------------------------------------------------------------------

    def _todo_list_items(self, list_entity):
        """The items of a to-do list from todo.get_items, cached like the entities."""
        list_id = list_entity["entity_id"]
        stamp, items = self.todo_cache.get(list_id, (0, []))
        if time.time() - stamp < self.cache_seconds:
            return items
        response = self._make_api_request("post", "/api/services/todo/get_items?return_response",
                                          json={"entity_id": list_id})
        items = response.json().get("service_response", {}).get(list_id, {}).get("items", [])
        self.todo_cache[list_id] = (time.time(), items)
        return items

    def _todo_call(self, service, data):
        self._call_service(f"todo.{service}", data)
        self.todo_cache.pop(data["entity_id"], None)

    def _todo_icon(self, name):
        return partial(Icon.image, str(self._mdi_icon_path(name) or self.icon_path))

    def _todo_items(self, entities, words):
        """'todo': the lists. 'todo <list> [text]': the open items of that list, with an item to
        add the text when no open item matches it. Other words search all lists. The words keep
        their case, the text is added as typed."""
        lists = [e for e in entities if e["entity_id"].startswith("todo.") and not self._is_blacklisted(e["entity_id"])]
        if not lists:
            return [RankItem(StandardItem(id="no_todo", text="No to-do lists", icon_factory=self._plugin_icon), 1.0)]
        if not words:
            return [RankItem(StandardItem(
                id=lst["entity_id"], text=self._name(lst),
                subtext=f"To-do list · {lst.get('state')} open",
                input_action_text=f"{TODO_VERB} {self._name(lst).lower().split()[0]} ",
                icon_factory=self._todo_icon(DOMAIN_ICONS["todo"]),
                actions=[Action("open", "Open in Home Assistant",
                                partial(openUrl, f"{self.active_url}/todo?entity_id={lst['entity_id']}"))]
            ), 1.0) for lst in lists]

        first = words[0].lower()
        chosen = [lst for lst in lists if first in self._name(lst).lower() or first in lst["entity_id"]]
        if chosen:
            lists, text_words = chosen[:1], words[1:]
        else:
            text_words = words
        score_of = self._scorer([w.lower() for w in text_words])
        text = " ".join(text_words)
        results = []
        for lst in lists:
            list_id, list_name = lst["entity_id"], self._name(lst)
            try:
                items = self._todo_list_items(lst)
            except (requests.exceptions.RequestException, ValueError) as e:
                return [RankItem(self._error_item(e), 1.0)]
            exact = False
            for item in items:
                summary, status = item.get("summary", "").strip(), item.get("status")
                score = score_of(summary)
                if score is None or (status == "completed" and not text_words):
                    continue
                exact = exact or summary.lower() == text.lower()
                ref = {"entity_id": list_id, "item": item.get("uid") or summary}
                if status == "completed":
                    actions = [Action("reopen", "Reopen", partial(self._todo_call, "update_item", {**ref, "status": "needs_action"}))]
                else:
                    actions = [self._repeat_action("complete", "Complete",
                                                   partial(self._todo_call, "update_item", {**ref, "status": "completed"}))]
                actions.append(Action("remove", "Remove", partial(self._todo_call, "remove_item", ref)))
                results.append(RankItem(StandardItem(
                    id=f"todo-{list_id}-{item.get('uid', summary)}", text=summary,
                    subtext=f"{list_name} · {'done' if status == 'completed' else 'open'}",
                    input_action_text=f"{TODO_VERB} {list_name.lower().split()[0]} {summary}",
                    icon_factory=self._todo_icon(TODO_ICONS.get(status, TODO_ICONS["needs_action"])),
                    actions=actions
                ), score if status != "completed" else min(score, 0.05)))
            if chosen and text and not exact:
                results.append(RankItem(StandardItem(
                    id=f"todo-add-{list_id}", text=f"Add \"{text}\"", subtext=f"to {list_name}",
                    icon_factory=self._todo_icon("plus-box-outline"),
                    actions=[Action("add", f"Add to {list_name}",
                                    partial(self._todo_call, "add_item", {"entity_id": list_id, "item": text}))]
                ), 0.5))
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
        visible = sum(1 for e in entities if not self._is_blacklisted(e["entity_id"]) and self._build_item(e))
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
            response = self._make_api_request("post", f"/api/services/{domain}/{service_name}", json=data)
            info(f"Called service '{service}' on '{data.get('entity_id')}' successfully.")
        except requests.exceptions.RequestException as e:
            warning(f"Error calling service '{service}' on '{data.get('entity_id')}': {e}")
            return
        # The response lists the states the call changed; keep the cache current so the next
        # query shows the new value without waiting for the cache to expire.
        try:
            self._update_cache(response.json())
        except ValueError:
            pass

    def _update_cache(self, states):
        if not self.entities_cache or not isinstance(states, list):
            return
        changed = {e["entity_id"]: e for e in states if isinstance(e, dict) and "entity_id" in e}
        self.entities_cache = [changed.get(e["entity_id"], e) for e in self.entities_cache]

    def _current_state(self, entity_id: str) -> dict:
        """The entity's state fresh from the API, for steps that build on the current value."""
        try:
            return self._make_api_request("get", f"/api/states/{entity_id}").json()
        except (requests.exceptions.RequestException, ValueError) as e:
            warning(f"Could not read {entity_id}: {e}")
            return {}

    def _step_brightness(self, entity_id: str, step: int):
        entity = self._current_state(entity_id)
        if not entity:
            return
        target = max(0, min(100, self._brightness_pct(entity) + step))
        self._call_service("light.turn_on" if target else "light.turn_off",
                           {"entity_id": entity_id, "brightness_pct": target} if target else {"entity_id": entity_id})

    def _step_volume(self, entity_id: str, step: int):
        entity = self._current_state(entity_id)
        level = entity.get("attributes", {}).get("volume_level") if entity else None
        if level is None:
            return
        target = max(0, min(100, round(level * 100) + step))
        self._call_service("media_player.volume_set", {"entity_id": entity_id, "volume_level": target / 100})

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

    @staticmethod
    def _repeat_action(action_id, text, callable):
        """An action that keeps the Albert window open, for steps one wants to repeat. The
        hide_on_activation argument exists only in newer Python plugin builds; older ones get a
        plain action that closes the window."""
        try:
            return Action(action_id, text, callable, hide_on_activation=False)
        except TypeError:
            return Action(action_id, text, callable)

    @staticmethod
    def _brightness_pct(entity):
        """Current brightness in percent, 0 when off or unknown."""
        if entity.get("state") != "on":
            return 0
        return round((entity.get("attributes", {}).get("brightness") or 0) * 100 / 255)

    def _light_actions(self, entity):
        """Toggle; the brightness steps come from _step_actions, exact values go via
        'bright <name> <pct>'."""
        entity_id = entity["entity_id"]
        return [Action("toggle", "Toggle", partial(self._call_service, "light.toggle", {"entity_id": entity_id}))]

    def _step_actions(self, entity):
        """Brightness or volume steps of a light or media player. A step reads the current value
        when it runs, so it can be repeated while the window stays open."""
        entity_id = entity["entity_id"]
        domain = entity_id.split(".")[0]
        attributes = entity.get("attributes", {})
        if domain == "light":
            return [self._repeat_action(f"step_{step}", f"{step:+d}%",
                                        partial(self._step_brightness, entity_id, step)) for step in (10, -10)]
        if domain != "media_player":
            return []
        features = attributes.get("supported_features", 0)
        if features & MP_VOLUME_SET and attributes.get("volume_level") is not None:
            return [self._repeat_action(f"vol_step_{step}", f"Volume {step:+d}%",
                                        partial(self._step_volume, entity_id, step)) for step in (10, -10)]
        if features & MP_VOLUME_STEP:
            # No current level to step from (e.g. a media player group): HA's own steps, their
            # size is up to the integration.
            return [self._repeat_action(f"volume_{direction}", f"Volume {sign}",
                                        partial(self._call_service, f"media_player.volume_{direction}",
                                                {"entity_id": entity_id}))
                    for direction, sign in (("up", "+"), ("down", "-"))]
        return []

    def _media_player_actions(self, entity):
        """Actions a media player supports, by its supported_features bits; the volume steps
        come from _step_actions."""
        entity_id, state = entity["entity_id"], entity.get("state")
        attributes = entity.get("attributes", {})
        features = attributes.get("supported_features", 0)
        actions = []

        def add(action_id, label, service, repeat=False, **data):
            make = self._repeat_action if repeat else Action
            actions.append(make(action_id, label,
                                partial(self._call_service, f"media_player.{service}", {"entity_id": entity_id, **data})))

        if state != "paused" and features & MP_PAUSE:
            add("pause", "Pause", "media_pause")
        if state != "playing" and features & MP_PLAY:
            add("play", "Play", "media_play")
        if features & MP_NEXT_TRACK:
            add("next", "Next Track", "media_next_track", repeat=True)
        if features & MP_PREVIOUS_TRACK:
            add("prev", "Previous Track", "media_previous_track", repeat=True)
        if features & MP_VOLUME_MUTE:
            muted = bool(attributes.get("is_volume_muted"))
            add("mute", "Unmute" if muted else "Mute", "volume_mute", is_volume_muted=not muted)
        return actions

    def _select_actions(self, entity):
        """One action per option of a select, the current one left out."""
        entity_id = entity["entity_id"]
        domain = entity_id.split(".")[0]
        attributes = entity.get("attributes", {})
        return [Action(f"option_{option}", f"Select {option}",
                       partial(self._call_service, f"{domain}.select_option", {"entity_id": entity_id, "option": option}))
                for option in attributes.get("options", []) if option != entity.get("state")]

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

    def _build_item(self, entity: dict, prepend_actions=(), completion_prefix="", steps_first=False):
        entity_id = entity.get("entity_id")
        if not entity_id:
            return None
        state = entity.get("state", "unknown")
        domain = entity_id.split(".")[0]
        if state in self.state_blacklist_items and not (domain in STATELESS_DOMAINS and state == "unknown"):
            return None

        attributes = entity.get("attributes", {})
        friendly_name = self._name(entity)
        formatted_state = self._format_state(state, attributes.get("unit_of_measurement"))

        actions = list(prepend_actions)
        steps = self._step_actions(entity)
        if steps_first:
            actions += steps
        if domain == "light":
            actions += self._light_actions(entity)
        elif domain == "media_player":
            actions += self._media_player_actions(entity)
        elif domain == "input_number":
            actions += self._input_number_actions(entity)
        elif domain in ("select", "input_select"):
            actions += self._select_actions(entity)
        if not steps_first:
            actions += steps
        else:
            for action_id, label, service, data in DOMAIN_ACTIONS.get(domain, []):
                actions.append(Action(action_id, label,
                                      partial(self._call_service, service, {"entity_id": entity_id, **data})))
        actions.append(Action("copy_entity_id", f"Copy '{entity_id}'", partial(setClipboardText, entity_id)))
        if domain not in STATELESS_DOMAINS:
            actions.append(Action("copy_state", f"Copy '{formatted_state}'", partial(setClipboardText, formatted_state)))

        return EntityItem(self, entity, actions, f"{completion_prefix} {friendly_name}".strip())

    def _subtext(self, entity):
        """'Light is On · 55%': domain, state and the value a step changes."""
        domain = entity["entity_id"].split(".")[0]
        attributes = entity.get("attributes", {})
        state = entity.get("state", "unknown")
        kind = domain.replace("_", " ").title()
        if domain in STATELESS_DOMAINS:
            when = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", state)
            return f"{kind} · last used {when.group(1)} {when.group(2)}" if when else f"{kind} · never used"
        subtext = f"{kind} is {self._format_state(state, attributes.get('unit_of_measurement'))}"
        if domain == "light" and state == "on" and attributes.get("brightness") is not None:
            subtext += f" · {self._brightness_pct(entity)}%"
        elif domain == "media_player" and (volume := attributes.get("volume_level")) is not None:
            muted = " (muted)" if attributes.get("is_volume_muted") else ""
            subtext += f" · {round(volume * 100)}%{muted}"
        return subtext

    # --- icons --------------------------------------------------------------------------------
    #
    # Every icon is a Material Design Icon, named like Home Assistant names it: the entity's own
    # icon attribute, or the default the HA frontend would derive from domain, device_class and
    # state. A name resolves to the cached download, else to the bundled seed in icons/mdi, else
    # the download starts in the background and the HA logo stands in until it is there. Active
    # entities get a tinted copy, there is no tinting primitive in the Albert API.

    def _mdi_name(self, entity: dict) -> str:
        attributes = entity.get("attributes", {})
        icon = attributes.get("icon") or ""
        if icon.startswith("mdi:"):
            return icon[4:]
        domain = entity.get("entity_id", "").split(".")[0]
        state = entity.get("state")
        device_class = attributes.get("device_class")
        if domain == "binary_sensor" and device_class in BINARY_SENSOR_CLASS_ICONS:
            return BINARY_SENSOR_CLASS_ICONS[device_class][1 if state == "on" else 0]
        if domain == "sensor" and device_class in SENSOR_CLASS_ICONS:
            return SENSOR_CLASS_ICONS[device_class]
        if domain == "media_player" and device_class == "tv":
            return "television"
        return STATE_ICONS.get((domain, state)) or DOMAIN_ICONS.get(domain) or "home-assistant"

    def _mdi_icon_path(self, name: str) -> Path | None:
        """The SVG for an MDI name: cached download, bundled seed, or None while downloading."""
        cached = self.icon_cache_path / f"{name}.svg"
        if cached.exists():
            return cached
        bundled = self.icons_path / f"{name}.svg"
        if bundled.exists():
            return bundled
        if not self.use_mdi_icons:
            return None

        def fetch():
            try:
                response = requests.get(f"https://cdn.jsdelivr.net/npm/@mdi/svg/svg/{name}.svg", timeout=5)
                if response.status_code == 200:
                    cached.write_bytes(response.content)
            except requests.exceptions.RequestException as e:
                warning(f"Failed to download MDI icon '{name}': {e}")

        self._background_download(f"mdi:{name}", fetch)
        return None

    def _tinted(self, icon_path: Path, color: str) -> Path:
        """A copy of the SVG with every path filled in color, cached next to the downloads."""
        tinted = self.icon_cache_path / f"{icon_path.stem}-{color}.svg"
        if tinted.exists():
            return tinted
        try:
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            tree = ET.parse(icon_path)
            for path in tree.findall(".//{http://www.w3.org/2000/svg}path"):
                path.set("fill", color)
            tree.write(tinted)
            return tinted
        except (OSError, ET.ParseError) as e:
            warning(f"Failed to tint {icon_path.name}: {e}")
            return icon_path

    def _get_icon_path(self, entity: dict) -> Path:
        path = self._mdi_icon_path(self._mdi_name(entity))
        if path is None:
            return self.icon_path  # stand-in while the icon downloads, not tinted
        if entity.get("state") in ACTIVE_STATES:
            return self._tinted(path, ACTIVE_COLOR)
        return path
