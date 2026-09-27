"""HA-Backup-Hooks: Schreibpause mit weiterlaufender Eingangswarteschlange."""

import asyncio

from .const import DOMAIN


def archives(hass):
    """Writers remain owned until their worker has actually stopped."""
    return tuple(hass.data.get(DOMAIN, {}).get("archives", ()))


async def async_start_archive(hass, runtime, path, entry_id):
    """Serialize database creation with backup preparation and copying."""
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault("archive_start_lock", asyncio.Lock())
    while True:
        async with lock:
            finished = data.get("backup_finished")
            if finished is None:
                starting = asyncio.create_task(runtime.start_archive(path, entry_id))
                try:
                    try:
                        await asyncio.shield(starting)
                    except asyncio.CancelledError:
                        # A cancelled setup must not release the creation lock
                        # while SQLite initialization is still running in a thread.
                        await starting
                        raise
                finally:
                    archive = runtime.archive
                    if archive is not None and archive.worker is not None:
                        writers = data.setdefault("archives", {})
                        writers[archive] = None
                        archive.worker.add_done_callback(
                            lambda _, a=archive: writers.pop(a, None)
                        )
                return
        # Even schema initialization writes SQLite. A new entry must wait
        # before opening its archive, not merely pause its later records.
        await finished.wait()


def _finish_backup(data):
    data.pop("backup_archives", None)
    finished = data.pop("backup_finished", None)
    if finished is not None:
        finished.set()


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
    data = hass.data.setdefault(DOMAIN, {})
    async with data.setdefault("archive_start_lock", asyncio.Lock()):
        await _prepare_backup(hass, data)


async def _prepare_backup(hass, data):
    if "backup_archives" in data:
        raise RuntimeError("Saunaarchive werden bereits gesichert")
    prepared = []
    data["backup_archives"] = prepared
    data["backup_finished"] = asyncio.Event()
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
        finally:
            _finish_backup(data)
        raise


async def async_post_backup(hass):
    data = hass.data.setdefault(DOMAIN, {})
    prepared = data.get("backup_archives")
    try:
        errors = await _release_and_flush(
            tuple(archives(hass)) if prepared is None else prepared
        )
    finally:
        _finish_backup(data)
    _raise_release_errors(errors)
