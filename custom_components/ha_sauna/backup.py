"""HA-Backup-Hooks: Schreibpause mit weiterlaufender Eingangswarteschlange."""

from .const import DOMAIN


def archives(hass):
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if (
            runtime is not None
            and not runtime.closed
            and getattr(runtime, "archive", None)
        ):
            yield runtime.archive


async def async_pre_backup(hass):
    prepared = []
    try:
        for archive in archives(hass):
            prepared.append(archive)
            await archive.pre_backup()
    except BaseException:
        for archive in prepared:
            await archive.post_backup()
        raise


async def async_post_backup(hass):
    for archive in archives(hass):
        await archive.post_backup()
