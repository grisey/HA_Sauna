"""Strict, deterministic persistence boundary for explicit user assignments."""

from .core.notification_recipients import RecipientAssignment, RecipientRef

CONF_NOTIFICATION_ASSIGNMENTS = "notification_assignments"


def decode_notification_assignments(value) -> tuple[RecipientAssignment, ...]:
    """Decode stored identities; live suitability belongs to the resolver."""
    if not isinstance(value, list):
        raise ValueError("Notification assignments must be a list")
    assignments = []
    users = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"user_id", "recipients"}:
            raise ValueError("Invalid notification assignment")
        if not isinstance(item["recipients"], list):
            raise ValueError("Notification recipients must be a list")
        refs = []
        for raw in item["recipients"]:
            if not isinstance(raw, dict) or set(raw) != {
                "kind",
                "reference_id",
                "transport",
            }:
                raise ValueError("Invalid notification reference")
            ref = RecipientRef(**raw)
            if ref.transport != "home_assistant" or ref in refs:
                raise ValueError("Unsupported or duplicate notification reference")
            refs.append(ref)
        assignment = RecipientAssignment(item["user_id"], frozenset(refs))
        if assignment.user_id in users:
            raise ValueError("Duplicate notification user")
        users.add(assignment.user_id)
        assignments.append(assignment)
    return tuple(sorted(assignments, key=lambda item: item.user_id))


def encode_notification_assignments(assignments) -> list[dict]:
    """Emit canonical JSON-compatible options, validating the result once."""
    result = [
        {
            "user_id": assignment.user_id,
            "recipients": [
                {
                    "kind": ref.kind,
                    "reference_id": ref.reference_id,
                    "transport": ref.transport,
                }
                for ref in sorted(
                    assignment.recipients,
                    key=lambda ref: (ref.transport, ref.kind, ref.reference_id),
                )
            ],
        }
        for assignment in sorted(assignments, key=lambda item: item.user_id)
    ]
    decode_notification_assignments(result)
    return result


class NotificationOptionsMixin:
    """Bounded selectors for explicit HA user-to-target assignments."""

    async def async_step_notification_user(self, user_input=None):
        import voluptuous as vol
        from homeassistant.helpers import selector

        if self._has_session():
            return self.async_abort(reason="session_exists")
        users = {
            user.id: user.name or user.id
            for user in await self.hass.auth.async_get_users()
            if user.is_active and not user.system_generated
        }
        errors = {}
        if user_input is not None:
            if set(user_input) == {"user_id"} and user_input["user_id"] in users:
                self._notification_user_id = user_input["user_id"]
                return await self.async_step_notification_targets()
            errors["base"] = "invalid_notification_user"
        return self.async_show_form(
            step_id="notification_user",
            data_schema=vol.Schema(
                {
                    vol.Required("user_id"): selector.SelectSelector(
                        {
                            "options": [
                                {"value": key, "label": label}
                                for key, label in sorted(users.items())
                            ],
                            "mode": "dropdown",
                            "sort": True,
                        }
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_notification_targets(self, user_input=None):
        import voluptuous as vol
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        from homeassistant.helpers import selector

        from .core.notification_recipients import (
            eligible_recipients,
            validate_assignment,
        )
        from .notifications import async_resolve_recipients
        from .runtime import Configuration

        if self._has_session():
            return self.async_abort(reason="session_exists")
        user_id = getattr(self, "_notification_user_id", None)
        users = await self.hass.auth.async_get_users()
        if not any(
            user.id == user_id and user.is_active and not user.system_generated
            for user in users
        ):
            return await self.async_step_notification_user()
        resolutions = await async_resolve_recipients(self.hass)
        candidates = {
            f"{ref.kind}:{ref.reference_id}": ref
            for ref in sorted(
                eligible_recipients(resolutions),
                key=lambda ref: (ref.kind, ref.reference_id),
            )
        }
        saved = decode_notification_assignments(
            self.config_entry.options.get(CONF_NOTIFICATION_ASSIGNMENTS, [])
        )
        errors = {}
        if user_input is not None:
            try:
                selected = user_input.get("recipients", [])
                if (
                    set(user_input) - {"recipients"}
                    or not isinstance(selected, list)
                    or any(
                        not isinstance(key, str) or key not in candidates
                        for key in selected
                    )
                    or len(set(selected)) != len(selected)
                ):
                    raise ValueError("Invalid recipient selection")
                assignment = validate_assignment(
                    RecipientAssignment(
                        user_id, frozenset(candidates[key] for key in selected)
                    ),
                    resolutions,
                )
                assignments = [item for item in saved if item.user_id != user_id]
                if assignment.recipients:
                    assignments.append(assignment)
                candidate = {
                    **self.config_entry.options,
                    CONF_NOTIFICATION_ASSIGNMENTS: encode_notification_assignments(
                        assignments
                    ),
                }
                candidate = Configuration.from_options(candidate).as_options()
            except ValueError:
                errors["base"] = "invalid_notification_targets"
            else:
                return self._save_and_return(candidate, "init")
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        indexed = {item.reference: item.recipient for item in resolutions}
        options = []
        for key, ref in candidates.items():
            entry = (
                registry.async_get(ref.reference_id)
                if ref.kind == "entity"
                else devices.async_get(ref.reference_id)
            )
            label = (
                (getattr(entry, "name_by_user", None) or getattr(entry, "name", None))
                if entry
                else None
            )
            mode = (
                "Companion: Daten und Antworten"
                if indexed[ref].capability.interactive
                else "Nur Nachricht"
            )
            display = f"{label} · {ref.reference_id}" if label else ref.reference_id
            options.append({"value": key, "label": f"{display} ({mode})"})
        suggested = {
            "recipients": [
                key
                for key, ref in candidates.items()
                if any(
                    item.user_id == user_id and ref in item.recipients for item in saved
                )
            ]
        }
        return self.async_show_form(
            step_id="notification_targets",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional("recipients"): selector.SelectSelector(
                            {
                                "options": options,
                                "multiple": True,
                                "mode": "dropdown",
                                "sort": True,
                            }
                        ),
                    }
                ),
                suggested,
            ),
            errors=errors,
        )
