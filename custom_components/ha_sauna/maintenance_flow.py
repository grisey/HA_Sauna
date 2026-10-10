"""Options steps for explicit names, entity IDs and confirmed cleanup."""

from html import escape

import voluptuous as vol
from homeassistant.helpers import selector

from .maintenance import (
    MaintenanceError,
    async_rename_entity,
    async_rename_sauna,
    cleanup_candidates,
    remove_entities,
)

from .rename_selection import rename_groups


class MaintenanceOptionsMixin:
    async def async_step_name(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        errors = {}
        if user_input is not None:
            try:
                await async_rename_sauna(self.hass, self.config_entry, user_input.get("name"))
            except MaintenanceError as error:
                errors["name" if error.code == "required" else "base"] = error.code
            else:
                return self._show_area_menu("init")
        return self.async_show_form(
            step_id="name",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required("name"): selector.TextSelector()}),
                user_input if user_input is not None else {"name": self.config_entry.title},
            ),
            errors=errors,
        )

    async def async_step_rename_entity(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        self._rename_selected = None
        groups = rename_groups(self.hass, self.config_entry)
        errors = {}
        if user_input is not None:
            if user_input.get("group") == "back":
                return self._show_area_menu("init")
            if user_input.get("group") in groups:
                self._rename_group = user_input["group"]
                return await self.async_step_rename_entity_select()
            errors["group"] = "entity_changed"
        return self.async_show_form(
            step_id="rename_entity",
            data_schema=vol.Schema({
                vol.Required("group"): selector.SelectSelector({
                    "options": [
                        {"value": key, "label": f"{group['label']} ({len(group['entities'])})"}
                        for key, group in groups.items()
                    ] + [{"value": "back", "label": "Zurück zur Übersicht"}],
                    "mode": "dropdown",
                }),
            }),
            errors=errors,
        )

    async def async_step_rename_entity_select(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        if user_input is not None and user_input.get("back"):
            return await self.async_step_rename_entity()
        group = rename_groups(self.hass, self.config_entry).get(
            getattr(self, "_rename_group", None)
        )
        if group is None:
            return await self.async_step_rename_entity()
        entities = group["entities"]
        errors = {}
        if user_input is not None:
            choices = [user_input[key] for key in ("entity_id", "inactive_entity_id")
                       if user_input.get(key)]
            selected = next((entity for entity in entities
                             if len(choices) == 1 and entity.entity_id == choices[0]), None)
            if selected is None:
                errors["base"] = "entity_changed"
            else:
                self._rename_selected = selected
                return await self.async_step_rename_entity_edit()
        active = [entity.entity_id for entity in entities
                  if self.hass.states.get(entity.entity_id) is not None]
        inactive = [entity for entity in entities if entity.entity_id not in active]
        fields = {}
        if active:
            fields[vol.Optional("entity_id")] = selector.EntitySelector({
                "include_entities": active,
            })
        # HA's native entity picker uses states. Registry-only entries need a
        # separate labelled selector so disabled entities remain discoverable.
        if inactive:
            fields[vol.Optional("inactive_entity_id")] = selector.SelectSelector({
                "options": [
                    {"value": entity.entity_id, "label": (
                        f"{entity.name or entity.original_name or entity.entity_id} · {entity.entity_id}"
                    )} for entity in sorted(inactive, key=lambda item: (
                        (item.name or item.original_name or item.entity_id).casefold(), item.entity_id,
                    ))
                ],
                "mode": "dropdown",
            })
        fields[vol.Optional("back", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="rename_entity_select",
            data_schema=vol.Schema(fields),
            description_placeholders={"group": group["label"]},
            errors=errors,
        )

    async def async_step_rename_entity_edit(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        if user_input is not None and user_input.get("back"):
            self._rename_selected = None
            return await self.async_step_rename_entity_select()
        selected = getattr(self, "_rename_selected", None)
        if selected is None:
            return await self.async_step_rename_entity()
        errors = {}
        if user_input is not None:
            try:
                new_id = user_input.get("new_entity_id", "").strip()
                name = user_input.get("name", "").strip() or None
                # Leaving the suggested original name untouched must not
                # create a permanent override of the integration's label.
                if name == (selected.name or selected.original_name):
                    name = selected.name
                await async_rename_entity(
                    self.hass, self.config_entry, selected, new_id, name
                )
            except MaintenanceError as error:
                field = "new_entity_id" if error.code in {"invalid_entity_id", "entity_id_in_use"} else "base"
                errors[field] = error.code
            else:
                del self._rename_selected
                return self._show_area_menu("init")
        return self.async_show_form(
            step_id="rename_entity_edit",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Optional("name"): selector.TextSelector(),
                    vol.Optional("new_entity_id"): selector.TextSelector(),
                    vol.Optional("back", default=False): selector.BooleanSelector(),
                }),
                user_input if user_input is not None else {
                    "name": selected.name or selected.original_name or "",
                    "new_entity_id": selected.entity_id,
                },
            ),
            description_placeholders={"entity_id": selected.entity_id},
            errors=errors,
        )

    async def async_step_cleanup_entities(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        errors = {}
        if user_input is not None and not user_input.get("confirm"):
            return self._show_area_menu("init")
        try:
            if user_input is not None and hasattr(self, "_cleanup_preview"):
                remove_entities(self.hass, self.config_entry, self._cleanup_preview)
                del self._cleanup_preview
                return self._show_area_menu("init")
        except MaintenanceError as error:
            errors["base"] = error.code
        try:
            self._cleanup_preview = cleanup_candidates(self.hass, self.config_entry)
        except MaintenanceError as error:
            return self.async_abort(reason=error.code)
        if not self._cleanup_preview:
            return await self.async_step_cleanup_empty()
        return self.async_show_form(
            step_id="cleanup_entities",
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): selector.BooleanSelector(),
            }),
            description_placeholders={
                "entities": "\n".join(
                    f"- `{entity.entity_id}`"
                    + (f" — {escape(entity.name or entity.original_name)}"
                       if entity.name or entity.original_name else "")
                    for entity in self._cleanup_preview
                ),
                "count": str(len(self._cleanup_preview)),
            },
            errors=errors,
        )

    async def async_step_cleanup_empty(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        return self.async_show_menu(step_id="cleanup_empty", menu_options=["init"])
