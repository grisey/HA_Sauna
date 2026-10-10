"""Small, functional entity groups for registry rename selection."""

from homeassistant.helpers import device_registry as dr

from .bindings import ROLES
from .const import CONF_BINDINGS, DOMAIN
from .core.defaults import section
from .core.parameters import BY_KEY
from .maintenance import manageable_entities


def rename_groups(hass, entry):
    """Group registry entries without depending on live or enabled entities."""
    frontend = section("frontend")
    labels = {
        group["id"]: group["label"]
        for group in (*frontend["settings_groups"], *frontend["settings_subgroups"])
    }
    devices = dr.async_get(hass)
    bindings = entry.options.get(CONF_BINDINGS, {})
    groups = {}
    for entity in manageable_entities(hass, entry):
        if entity.config_entry_id == entry.entry_id and entity.platform == DOMAIN:
            key = entity.unique_id.removeprefix(f"{entry.entry_id}_")
            definition = BY_KEY.get(key) if entity.domain == "number" else None
            if definition is not None:
                category = definition.settings_subgroup or definition.settings_group
                group_id, label = f"own:{category}", labels[category]
            elif entity.domain in {"switch", "button", "climate"}:
                group_id, label = "own:controls", "Bedienung"
            elif entity.domain == "sensor":
                group_id, label = "own:readings", "Messwerte und Status"
            else:
                group_id, label = "own:other", "Weitere Entitäten"
            label = f"{entry.title} · {label}"
        else:
            device = devices.async_get(entity.device_id) if entity.device_id else None
            roles = ", ".join(role.label for role in ROLES if bindings.get(role.key) == entity.entity_id)
            if device is not None:
                group_id = f"device:{device.id}"
                label = f"Zugeordnetes Gerät · {device.name_by_user or device.name or roles}"
            else:
                group_id = f"entity:{entity.id}"
                label = f"Zugeordnet · {roles or entity.name or entity.original_name or entity.entity_id}"
        group = groups.setdefault(group_id, {"label": label, "entities": []})
        group["entities"].append(entity)
    for group_id, group in groups.items():
        if group_id.startswith("device:"):
            entity_ids = {entity.entity_id for entity in group["entities"]}
            roles = ", ".join(role.label for role in ROLES if bindings.get(role.key) in entity_ids)
            if roles:
                group["label"] += f" · {roles}"
    return dict(sorted(groups.items(), key=lambda item: (
        not item[0].startswith("own:"), item[1]["label"].casefold(), item[0],
    )))
