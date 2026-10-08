#!/usr/bin/env python3
"""
Move stored recordings from Supabase Storage into a company's Google Drive.

    python scripts/drive_migration.py connect --tenant <id> --credentials <file>
    python scripts/drive_migration.py migrate --tenant <id> --dry-run
    python scripts/drive_migration.py migrate --tenant <id>

**The Supabase object is kept.** Nothing is deleted: after a row is moved, the
audio exists in both places and the row remembers both — `recordingPath` points
at Drive, and `recordingObject` still points at the bucket. That is what makes
the bucket a backup rather than a previous location, and it is also what keeps
the recording visible in Conversation-Agent's older dashboard, which has never
heard of a `gd://` reference.

`--delete-source` exists for later, once you trust the copies. It is not the
default and it asks before it runs.

Every step is verified before the row is changed: the upload is read back and
its length compared to the original. A row is repointed only after that
matches, so an interrupted run leaves rows that still play from Supabase rather
than rows pointing at a half-written Drive file.

Safe to run repeatedly. Rows already on Drive are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run from anywhere: the app package lives one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.call import Call, RecordingState  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.repositories import calls as call_repo  # noqa: E402
from app.repositories import tenants as tenant_repo  # noqa: E402
from app.services import storage  # noqa: E402
from app.services.drive import DriveError, TenantDrive, web_link  # noqa: E402

GREEN, YELLOW, RED, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def say(colour: str, tag: str, message: str) -> None:
    print(f"{colour}{tag:>9}{OFF}  {message}", flush=True)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


async def connect(tenant_id: str, credentials_path: Path) -> int:
    """Attach an existing Google refresh token to a company.

    The normal route is the consent screen in the app. This exists for the
    operator's own number, where a refresh token already exists from an earlier
    project and clicking through OAuth again would only produce another one.

    The token must have been issued to the SAME OAuth client as
    GOOGLE_CLIENT_ID, or Google refuses the refresh with `invalid_grant` —
    which it also returns for a revoked token, so the two are worth telling
    apart before blaming the token.
    """
    tenant = await tenant_repo.get(tenant_id)
    if tenant is None:
        say(RED, "error", f"no company is registered on {tenant_id}")
        return 1

    try:
        payload = json.loads(credentials_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        say(RED, "error", f"could not read {credentials_path}: {exc}")
        return 1

    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token:
        say(RED, "error", f"{credentials_path.name} has no refresh_token")
        return 1

    from app.config import settings

    client_id = str(payload.get("client_id") or "").strip()
    if client_id and client_id != settings.google_client_id.strip():
        say(RED, "error",
            "that token belongs to a different OAuth client than GOOGLE_CLIENT_ID. "
            "Google would refuse it with invalid_grant.")
        return 1

    tenant.google_drive.refresh_token = refresh_token
    tenant.google_drive.connected_at = datetime.now(timezone.utc).isoformat()

    drive = TenantDrive(tenant)
    try:
        tenant.google_drive.account_email = await drive.account_email()
        folder = await drive.folder()
    except DriveError as exc:
        say(RED, "error", f"Google refused the token: {exc}")
        return 1

    await tenant_repo.save(tenant)
    say(GREEN, "connected",
        f"{tenant.name or tenant_id} -> {tenant.google_drive.account_email or 'that Google account'}")
    say(DIM, "folder", f"{tenant.google_drive.folder_name}  ({folder})")
    return 0


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


async def migrate(tenant_id: str, dry_run: bool, delete_source: bool, limit: int) -> int:
    tenant = await tenant_repo.get(tenant_id)
    if tenant is None:
        say(RED, "error", f"no company is registered on {tenant_id}")
        return 1

    drive = TenantDrive(tenant)
    if not drive.configured:
        say(RED, "error",
            f"{tenant.name or tenant_id} has not connected Google Drive. "
            f"Run: drive_migration.py connect --tenant {tenant_id} --credentials <file>")
        return 1

    rows = await call_repo.list_calls(tenant_id, limit=limit)
    pending = [c for c in rows if c.recording_path.startswith(storage.REMOTE_PREFIX)]
    already = [c for c in rows if c.recording_path.startswith(storage.DRIVE_PREFIX)]

    say(DIM, "company", f"{tenant.name or tenant_id} -> {tenant.google_drive.account_email}")
    say(DIM, "found", f"{len(rows)} calls, {len(pending)} to move, {len(already)} already in Drive")
    if dry_run:
        say(YELLOW, "dry run", "nothing will be uploaded or changed")
    if not pending:
        say(GREEN, "done", "there is nothing to move")
        return 0
    print()

    moved = failed = 0
    for call in pending:
        label = call.id[:18]
        source = call.recording_path
        try:
            audio = await storage.get(tenant, source)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
            say(RED, "failed", f"{label}  could not read from Supabase: {exc}")
            failed += 1
            continue
        if not audio:
            # Not a failure worth stopping for: the row points at an object
            # that is not there, which the move cannot fix.
            say(YELLOW, "missing", f"{label}  no object at {source}")
            failed += 1
            continue

        extension = source.rsplit(".", 1)[-1] if "." in source.rsplit("/", 1)[-1] else "wav"
        if dry_run:
            say(DIM, "would move", f"{label}  {len(audio) / 1024:,.0f} KB -> Drive")
            moved += 1
            continue

        try:
            uploaded = await drive.upload(
                f"call-{call.id}.{extension}", audio,
                call.recording_mime or "audio/wav",
            )
        except (DriveError, Exception) as exc:  # noqa: BLE001
            say(RED, "failed", f"{label}  Drive refused the upload: {exc}")
            failed += 1
            continue

        # Read it back before trusting it. An upload that reported success and
        # wrote nothing would otherwise leave a row pointing at silence, and
        # the Supabase copy is only a safety net if somebody notices.
        check = await drive.download(uploaded["id"])
        if not check or len(check) != len(audio):
            got = len(check) if check else 0
            say(RED, "failed", f"{label}  Drive returned {got:,} of {len(audio):,} bytes — row left alone")
            failed += 1
            continue

        call.metadata = {
            **call.metadata,
            # Both pointers, on purpose. The first is the backup; the second is
            # what the TypeScript service reads.
            "supabaseBackup": source,
            "supabaseObject": source[len(storage.REMOTE_PREFIX):],
            "movedToDriveAt": datetime.now(timezone.utc).isoformat(),
        }
        call.recording_path = f"{storage.DRIVE_PREFIX}{uploaded['id']}"
        call.recording_bytes = len(audio)
        call.recording_state = RecordingState.READY
        await call_repo.save_call(call)
        say(GREEN, "moved", f"{label}  {len(audio) / 1024:,.0f} KB  {web_link(uploaded['id'])}")
        moved += 1

        if delete_source:
            await storage.delete(tenant, source)
            say(YELLOW, "deleted", f"{label}  removed the Supabase copy")

    print()
    say(GREEN if not failed else YELLOW, "done",
        f"{moved} moved, {failed} left alone" + (", sources deleted" if delete_source else
        ", Supabase copies kept as the backup"))
    return 0 if not failed else 1


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("connect", help="attach an existing Google refresh token to a company")
    c.add_argument("--tenant", required=True)
    c.add_argument("--credentials", required=True, type=Path,
                   help="a JSON file containing refresh_token (and ideally client_id)")

    m = sub.add_parser("migrate", help="copy Supabase-stored recordings into Drive")
    m.add_argument("--tenant", required=True)
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--limit", type=int, default=1000)
    m.add_argument("--delete-source", action="store_true",
                   help="also remove the Supabase copy — not the default")

    args = parser.parse_args()

    if args.command == "connect":
        return asyncio.run(connect(args.tenant, args.credentials))

    if args.delete_source:
        say(YELLOW, "warning", "this will DELETE the Supabase copies after moving them.")
        if input("          type 'delete' to confirm: ").strip() != "delete":
            say(DIM, "stopped", "nothing was changed")
            return 1
    return asyncio.run(migrate(args.tenant, args.dry_run, args.delete_source, args.limit))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
