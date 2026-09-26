"""HA-Backup-Hooks: Schreibpause mit weiterlaufender Eingangswarteschlange."""

import asyncio

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


def _release(archives):
    """Resume every writer before waiting for one of their flushes."""
    for archive in archives:
        archive.release_backup()


async def _release_and_flush(archives):
    """Release all writers, then collect every observable flush failure."""
    _release(archives)
    errors = []
    for archive in archives:
        try:
            await archive.flush()
        except asyncio.CancelledError as error:
            if errors:
                error.add_note(f"Vorherige Archivfreigabefehler: {_describe(errors)}")
            raise
        except Exception as error:  # noqa: BLE001 - collect each flush failure.
            errors.append((archive, error))
    return errors


def _describe(errors):
    return "; ".join(
        f"{getattr(archive, 'entry_id', repr(archive))}: "
        f"{type(error).__name__}: {error}"
        for archive, error in errors
    )


def _describe_exception(error):
    description = f"{type(error).__name__}: {error}"
    notes = getattr(error, "__notes__", ())
    return "\n".join((description, *notes))


def _raise_release_errors(errors):
    if not errors:
        return
    for archive, error in errors:
        error.add_note(
            f"Betroffenes Archiv: {getattr(archive, 'entry_id', repr(archive))}"
        )
    if len(errors) == 1:
        raise errors[0][1]
    raise ExceptionGroup(
        "Mehrere Archivfreigabefehler",
        [error for _, error in errors],
    )


async def async_pre_backup(hass):
    prepared = []
    try:
        for archive in tuple(archives(hass)):
            prepared.append(archive)
            await archive.pre_backup()
    except BaseException as original:
        try:
            errors = await _release_and_flush(prepared)
        except BaseException as cleanup_error:  # noqa: BLE001 - record, never replace.
            original.add_note(
                "Zusätzlicher Archivbereinigungsfehler: "
                f"{_describe_exception(cleanup_error)}"
            )
        else:
            if errors:
                original.add_note(
                    f"Zusätzliche Archivbereinigungsfehler: {_describe(errors)}"
                )
        raise


async def async_post_backup(hass):
    errors = await _release_and_flush(tuple(archives(hass)))
    _raise_release_errors(errors)
