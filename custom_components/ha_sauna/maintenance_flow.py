"""Options steps for explicit names, entity IDs and confirmed cleanup."""

import voluptuous as vol
from homeassistant.helpers import selector

from .maintenance import (
    MaintenanceError,
    async_rename_entity,
    async_rename_sauna,
    cleanup_candidates,
    manageable_entities,
    remove_entities,
)


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
        entities = manageable_entities(self.hass, self.config_entry)
        errors = {}
        if user_input is not None:
            selected = next((
                entity for entity in entities if entity.entity_id == user_input.get("entity_id")
            ), None)
            if selected is None:
                errors["entity_id"] = "entity_changed"
            else:
                self._rename_selected = selected
                return await self.async_step_rename_entity_edit()
        return self.async_show_form(
            step_id="rename_entity",
            data_schema=vol.Schema({
                vol.Required("entity_id"): selector.SelectSelector({
                    "options": [
                        {"value": entity.entity_id, "label": (
                            f"{entity.name or entity.original_name or entity.entity_id} · {entity.entity_id}"
                        )}
                        for entity in entities
                    ],
                    "mode": "dropdown",
                }),
            }),
            errors=errors,
        )

    async def async_step_rename_entity_edit(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
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
                    vol.Required("new_entity_id"): selector.TextSelector(),
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
            return self.async_show_menu(step_id="cleanup_empty", menu_options=["init"])
        return self.async_show_form(
            step_id="cleanup_entities",
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): selector.BooleanSelector(),
            }),
            description_placeholders={
                "entities": "\n".join(
                    f"- `{entity.entity_id}`"
                    for entity in self._cleanup_preview
                ),
                "count": str(len(self._cleanup_preview)),
            },
            errors=errors,
        )
