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

# Verb-first command mode: first token of the query is a verb that restricts
# the candidate domain, so e.g. "play office" never lists office lamps.
VERB_DOMAINS = {
    "play":       ["media_player"],
    "pause":      ["media_player"],
    "next":       ["media_player"],
    "prev":       ["media_player"],
    "vol":        ["media_player"],
    "volume":     ["media_player"],
    "mute":       ["media_player"],
    "unmute":     ["media_player"],
    "bright":     ["light"],
    "brightness": ["light"],
    "on":         ["light", "switch", "fan", "input_boolean", "scene", "script", "cover", "media_player"],
    "off":        ["light", "switch", "fan", "input_boolean", "cover", "media_player"],
    "toggle":     ["light", "switch", "fan", "input_boolean", "cover"],
}

VERB_SERVICES = {
    # (verb, domain): (service, label_template). {pct} is filled when verb has a numeric value.
    ("play",       "media_player"):  ("media_player.media_play",          "Play {name}"),
    ("pause",      "media_player"):  ("media_player.media_pause",         "Pause {name}"),
    ("next",       "media_player"):  ("media_player.media_next_track",    "Next track on {name}"),
    ("prev",       "media_player"):  ("media_player.media_previous_track", "Previous track on {name}"),
    ("vol",        "media_player"):  ("media_player.volume_set",          "Set {name} volume to {pct}%"),
    ("volume",     "media_player"):  ("media_player.volume_set",          "Set {name} volume to {pct}%"),
    ("mute",       "media_player"):  ("media_player.volume_mute",         "Mute {name}"),
    ("unmute",     "media_player"):  ("media_player.volume_mute",         "Unmute {name}"),
    ("bright",     "light"):         ("light.turn_on",                    "Set {name} brightness to {pct}%"),
    ("brightness", "light"):         ("light.turn_on",                    "Set {name} brightness to {pct}%"),
    ("on",         "light"):         ("light.turn_on",                    "Turn on {name}"),
    ("on",         "switch"):        ("switch.turn_on",                   "Turn on {name}"),
    ("on",         "fan"):           ("fan.turn_on",                      "Turn on {name}"),
    ("on",         "input_boolean"): ("input_boolean.turn_on",            "Turn on {name}"),
    ("on",         "scene"):         ("scene.turn_on",                    "Activate {name}"),
    ("on",         "script"):        ("script.turn_on",                   "Run {name}"),
    ("on",         "cover"):         ("cover.open_cover",                 "Open {name}"),
    ("on",         "media_player"): ("media_player.media_play",          "Play {name}"),
    ("off",        "light"):         ("light.turn_off",                   "Turn off {name}"),
    ("off",        "switch"):        ("switch.turn_off",                  "Turn off {name}"),
    ("off",        "fan"):           ("fan.turn_off",                     "Turn off {name}"),
    ("off",        "input_boolean"): ("input_boolean.turn_off",           "Turn off {name}"),
    ("off",        "cover"):         ("cover.close_cover",                "Close {name}"),
    ("off",        "media_player"): ("media_player.media_stop",          "Stop {name}"),
    ("toggle",     "light"):         ("light.toggle",                     "Toggle {name}"),
    ("toggle",     "switch"):        ("switch.toggle",                    "Toggle {name}"),
    ("toggle",     "fan"):           ("fan.toggle",                       "Toggle {name}"),
    ("toggle",     "input_boolean"): ("input_boolean.toggle",             "Toggle {name}"),
    ("toggle",     "cover"):         ("cover.toggle",                     "Toggle {name}"),
}

VALUE_REQUIRED_VERBS = {"vol", "volume", "bright", "brightness"}
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

        stripped_query = ctx.query.strip()

        if stripped_query == "info":
            yield self._run_info_query()
            return

        if not stripped_query:
            if self.favorite_items:
                yield self._run_favorites_search()
            else:
                yield [StandardItem(id=md_name, text=md_name, subtext=f"Using: {self.active_url}", icon_factory=self._plugin_icon,
                                    actions=[Action("url", "Open Home Assistant", lambda: openUrl(self.active_url))])]
            return

        if verb_match := self._parse_verb_query(stripped_query):
            yield self._run_verb_query(*verb_match)
            return

        yield self._run_search(stripped_query)

    def _parse_verb_query(self, query: str):
        """Return (verb, filter_terms, value) if first token is a known verb, else None."""
        parts = query.split()
        if not parts:
            return None
        verb = parts[0].lower()
        if verb not in VERB_DOMAINS:
            return None

        rest = parts[1:]
        value = None
        if verb in VALUE_REQUIRED_VERBS:
            # Last purely-numeric token is the value, the rest is the filter.
            for i in range(len(rest) - 1, -1, -1):
                token = rest[i]
                if token.replace('.', '', 1).isdigit():
                    value = float(token)
                    rest = rest[:i] + rest[i+1:]
                    break

        return (verb, [t.lower() for t in rest], value)

    def _build_verb_action(self, verb, entity, value):
        """Return (label, callable) or None when verb can't be applied."""
        entity_id = entity['entity_id']
        domain = entity_id.split('.')[0]
        if (verb, domain) not in VERB_SERVICES:
            return None
        if verb in VALUE_REQUIRED_VERBS and value is None:
            return None

        service, label_tmpl = VERB_SERVICES[(verb, domain)]
        name = entity.get('attributes', {}).get('friendly_name', entity_id)
        payload = {"entity_id": entity_id}
        pct = None

        if verb in ("vol", "volume"):
            v = max(0.0, min(1.0, float(value) / 100))
            payload["volume_level"] = v
            pct = int(v * 100)
        elif verb in ("bright", "brightness"):
            v = max(0, min(100, int(value)))
            payload["brightness_pct"] = v
            pct = v
        elif verb == "mute":
            payload["is_volume_muted"] = True
        elif verb == "unmute":
            payload["is_volume_muted"] = False

        label = label_tmpl.format(name=name, pct=pct) if pct is not None else label_tmpl.format(name=name)
        return (label, lambda s=service, p=payload: self._call_service(s, p))

    def _run_verb_query(self, verb, filter_terms, value):
        results = []
        try:
            entities = self._get_entities()
            applicable_domains = set(VERB_DOMAINS.get(verb, []))
            candidates = [
                e for e in entities
                if not self._is_blacklisted(e['entity_id'])
                and e['entity_id'].split('.')[0] in applicable_domains
                and all(
                    term in f"{e['entity_id']} {e.get('attributes', {}).get('friendly_name', '')}".lower()
                    for term in filter_terms
                )
            ]
            for entity in sorted(candidates, key=lambda e: e.get("attributes", {}).get("friendly_name", e['entity_id'])):
                prepend = []
                if (va := self._build_verb_action(verb, entity, value)) is not None:
                    label, fn = va
                    prepend.append(Action(f"verb_{verb}", label, fn))
                if item := self._build_item(entity, prepend_actions=prepend, completion_prefix=verb):
                    results.append(item)
        except requests.exceptions.RequestException as e:
            results.append(self._error_item(e))
        return results

    def _run_info_query(self):
        results = []
        try:
            entities = self._get_entities()
            total_entities = len(entities)

            visible_entities = sum(
                1 for e in entities
                if not self._is_blacklisted(e['entity_id'])
                and e.get('state', 'unknown') not in self.state_blacklist_items
            )

            toggle_actions = []
            subtext = "The current URL used for API requests."
            if self._internal_url and self._public_url:
                toggle_actions.append(Action("toggle_url", "Toggle Active URL", self._toggle_active_url))
                if self.active_url == self._internal_url:
                    subtext = "Using internal URL. Action to switch to public."
                else:
                    subtext = "Using public URL. Action to switch to internal."

            results.append(StandardItem(
                id="info_url",
                text=f"Active URL: {self.active_url}",
                subtext=subtext,
                icon_factory=self._plugin_icon,
                actions=toggle_actions
            ))

            results.append(StandardItem(
                id="info_total",
                text=f"Total entities: {total_entities}",
                subtext="Number of entities exposed by the Home Assistant API",
                icon_factory=self._plugin_icon
            ))

            results.append(StandardItem(
                id="info_visible",
                text=f"Visible entities: {visible_entities}",
                subtext="Entities remaining after applying blacklists",
                icon_factory=self._plugin_icon
            ))

        except requests.exceptions.RequestException as e:
            results.append(self._error_item(e))

        return results

    def _run_search(self, query_string):
        results = []
        try:
            entities = self._get_entities()
            search_terms = query_string.lower().split()
            filtered_entities = [
                e for e in entities 
                if not self._is_blacklisted(e['entity_id']) and all(
                    term in f"{e['entity_id']} {e.get('attributes', {}).get('friendly_name', '')}".lower()
                    for term in search_terms
                )
            ]

            for entity in sorted(filtered_entities, key=lambda e: e.get("attributes", {}).get("friendly_name", e['entity_id'])):
                if item := self._build_item(entity):
                    results.append(item)
                if entity['entity_id'].startswith("media_player.") and entity.get('state') == 'playing':
                    if now_playing_item := self._build_now_playing_item(entity):
                        results.append(now_playing_item)

        except requests.exceptions.RequestException as e:
            results.append(self._error_item(e))
        
        return results

    def _run_favorites_search(self):
        results = []
        try:
            entities = self._get_entities()
            favorite_entities = [e for e in entities if e['entity_id'] in self.favorite_items]
            for entity in sorted(favorite_entities, key=lambda e: e.get("attributes", {}).get("friendly_name", e['entity_id'])):
                if item := self._build_item(entity):
                    results.append(item)
                if entity['entity_id'].startswith("media_player.") and entity.get('state') == 'playing':
                    if now_playing_item := self._build_now_playing_item(entity):
                        results.append(now_playing_item)
        except requests.exceptions.RequestException as e:
            results.append(self._error_item(e))

        return results

    def _call_service(self, service: str, data: dict):
        domain, service_name = service.split('.')
        endpoint = f"/api/services/{domain}/{service_name}"
        try:
            self._make_api_request("post", endpoint, json=data)
            info(f"Called service '{service}' on '{data.get('entity_id')}' successfully.")
        except requests.exceptions.RequestException as e:
            warning(f"Error calling service '{service}' on '{data.get('entity_id')}': {e}")
            
    def _format_state(self, state: str, unit: str | None) -> str:
        try:
            num_state = float(state)
            formatted = f"{num_state:.2f}".rstrip('0').rstrip('.') if '.' in str(num_state) else str(int(num_state))
            return f"{formatted} {unit}" if unit else formatted
        except (ValueError, TypeError):
            return state.replace('_', ' ').capitalize()

    def _build_now_playing_item(self, entity: dict):
        attributes = entity.get('attributes', {})
        title = attributes.get("media_title")
        if not title: return None

        artist = attributes.get("media_artist")
        album = attributes.get("media_album_name")
        text = f"{artist} - {album}" if artist and album else artist or album or "unknown Track/Artist"
        
        icon_path = self._get_url_icon_path(attributes.get("entity_picture")) or self.icon_path

        return StandardItem(
            id=f"now_playing_{entity['entity_id']}", text=text, subtext="now playing",
            icon_factory=lambda p=icon_path: Icon.image(str(p)),
            actions=[Action("copy_title", "Copy Title", partial(setClipboardText, title)),
                     Action("copy_artist", "Copy Artist", partial(setClipboardText, artist or ""))]
        )

    def _build_item(self, entity: dict, prepend_actions=(), completion_prefix=""):
        entity_id = entity.get('entity_id')
        if not entity_id: return None

        state = entity.get('state', 'unknown')
        if state in self.state_blacklist_items: return None

        domain = entity_id.split('.')[0]
        attributes = entity.get('attributes', {})
        friendly_name = attributes.get('friendly_name', entity_id)

        actions = list(prepend_actions)
        unit = attributes.get("unit_of_measurement")
        supported_features = attributes.get("supported_features", 0)
        formatted_state = self._format_state(state, unit)
        icon_path = self._get_icon_path(entity) # Pass full entity dict

        # Domain-specific actions
        if domain == "light":
            actions.extend([
                Action("toggle", "Toggle", lambda eid=entity_id: self._call_service("light.toggle", {"entity_id": eid})),
                #Action("turn_on", "Turn On", lambda eid=entity_id: self._call_service("light.turn_on", {"entity_id": eid})),
                #Action("turn_off", "Turn Off", lambda eid=entity_id: self._call_service("light.turn_off", {"entity_id": eid}))
            ])
            for pct in [20, 40, 60, 80]:
                actions.append(Action(f"set_{pct}", f"Set to {pct}%", lambda eid=entity_id, p=pct: self._call_service("light.turn_on", {"entity_id": eid, "brightness_pct": p})))
        elif domain == "input_number":
            min_val, max_val, step = (float(attributes.get(k, 0.0)) for k in ("min", "max", "step"))
            if step <= 0 or max_val <= min_val:
                step = max(max_val - min_val, 1.0)
            value = min_val
            while round(value, 4) <= round(max_val, 4):
                actions.append(Action(f"set_{value}", f"Set to {value}", lambda eid=entity_id, v=value: self._call_service("input_number.set_value", {"entity_id": eid, "value": v})))
                value += step
        elif domain == "media_player":
            if state != "paused" and supported_features & 1:
                actions.append(Action("pause", "Pause", lambda eid=entity_id: self._call_service("media_player.media_pause", {"entity_id": eid})))
            if state != "playing" and supported_features & 16384:
                actions.append(Action("play", "Play", lambda eid=entity_id: self._call_service("media_player.media_play", {"entity_id": eid})))
            if supported_features & 32:
                actions.append(Action("next", "Next Track", lambda eid=entity_id: self._call_service("media_player.media_next_track", {"entity_id": eid})))
            if supported_features & 16:
                actions.append(Action("prev", "Previous Track", lambda eid=entity_id: self._call_service("media_player.media_previous_track", {"entity_id": eid})))
            if supported_features & 8:
                is_muted = bool(attributes.get('is_volume_muted'))
                actions.append(Action("mute", "Unmute" if is_muted else "Mute",
                    lambda eid=entity_id, m=not is_muted: self._call_service("media_player.volume_mute", {"entity_id": eid, "is_volume_muted": m})))
            if supported_features & 4:  # VOLUME_SET
                for pct in (20, 40, 60, 80, 100):
                    actions.append(Action(f"vol_{pct}", f"Set Volume to {pct}%",
                        lambda eid=entity_id, p=pct: self._call_service("media_player.volume_set", {"entity_id": eid, "volume_level": p / 100})))
            if supported_features & 1024:  # VOLUME_STEP
                actions.append(Action("volume_up", "Volume Up",
                    lambda eid=entity_id: self._call_service("media_player.volume_up", {"entity_id": eid})))
                actions.append(Action("volume_down", "Volume Down",
                    lambda eid=entity_id: self._call_service("media_player.volume_down", {"entity_id": eid})))
        elif domain in ["switch", "fan", "input_boolean"]:
            actions.append(Action("toggle", "Toggle", lambda eid=entity_id: self._call_service(f"{domain}.toggle", {"entity_id": eid})))
        elif domain in ["scene", "script"]:
            actions.append(Action("turn_on", "Turn On", lambda eid=entity_id: self._call_service(f"{domain}.turn_on", {"entity_id": eid})))
        elif domain == "automation":
            actions.append(Action("trigger", "Trigger", lambda eid=entity_id: self._call_service("automation.trigger", {"entity_id": eid})))
        elif domain == "cover":
            actions.extend([
                Action("toggle", "Toggle", lambda eid=entity_id: self._call_service("cover.toggle", {"entity_id": eid})),
                Action("open", "Open", lambda eid=entity_id: self._call_service("cover.open_cover", {"entity_id": eid})),
                Action("close", "Close", lambda eid=entity_id: self._call_service("cover.close_cover", {"entity_id": eid}))
            ])
        elif domain == "sensor":
            #friendly_name = f"{friendly_name}: {formatted_state}"
            pass

        mdi_name = attributes.get("icon")
        actions.append(Action("copy_entity_id", f"Copy '{entity_id}'", lambda eid=entity_id: setClipboardText(eid)))
        actions.append(Action("copy_friendly_name", f"Copy '{friendly_name}'", lambda fn=friendly_name: setClipboardText(fn)))
        actions.append(Action("copy_state", f"Copy '{state}'", lambda v=state: setClipboardText(v)))
        actions.append(Action("copy_formatted_state", f"Copy '{formatted_state}'", lambda v=formatted_state: setClipboardText(v)))
        actions.append(Action("copy_icon_path", f"Copy Icon '{icon_path}'", lambda p=icon_path: setClipboardText(str(p) if p else "")))
        actions.append(Action("copy_mdi_name", f"Copy mdi_name '{mdi_name}'", lambda p=mdi_name: setClipboardText(p or "")))

        subtext = f"{domain.replace('_', ' ').title()} is {formatted_state}"
        if domain == "media_player" and (vol := attributes.get("volume_level")) is not None:
            muted = " (muted)" if attributes.get("is_volume_muted") else ""
            subtext += f" · {int(vol * 100)}%{muted}"

        completion = f"{completion_prefix} {friendly_name}".strip() if completion_prefix else friendly_name

        return StandardItem(
            id=entity_id,
            text=friendly_name,
            subtext=subtext,
            input_action_text=completion,
            icon_factory=lambda p=icon_path: Icon.image(str(p)),
            actions=actions
        )
