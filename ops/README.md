# Coastal Warning Windows startup

These scripts keep the local dashboard, authenticated device gateway, and
Cloudflare Named Tunnel running after Windows restarts.

Run `install-startup.ps1` once from an elevated PowerShell window. It copies a
deployment-only server runtime plus the device and Tunnel credentials into
ACL-restricted system directories, registers two `SYSTEM` startup tasks, and
installs `cloudflared` as an automatic Windows service. The tasks never execute
mutable workspace code. Secrets are never placed in a scheduled-task argument
or service `ImagePath`.

The administrator username is fixed to `admin`. Supply the initial password as
a `SecureString`, so the plaintext does not appear in the repository or command
history:

```powershell
$adminPassword = Read-Host 'Initial administrator password' -AsSecureString
.\ops\install-startup.ps1 -AdminPassword $adminPassword
Remove-Variable adminPassword
```

For automated provisioning, pass a precomputed verifier with
`-AdminPasswordHash`. Its exact format is
`pbkdf2_sha256$310000$<32 lowercase salt hex>$<64 lowercase digest hex>`.
Keep the verifier out of source control and read it from a protected file before
calling the installer. Supplying neither parameter preserves an existing valid
verifier; a first installation fails closed if no verifier is supplied.

The installer creates a random 32-byte administrator session key on first use
and preserves it across runtime updates. `-RotateAdminSessionSecret` replaces
that key and invalidates every existing administrator session.

The SQLite database and a trained custom-water model are persistent state under
`C:\ProgramData\CoastalWarning\data` and `C:\ProgramData\CoastalWarning\models`.
UK official-data bundles are read only from
`C:\ProgramData\CoastalWarning\data\official_datasets`; immutable registration
records and trained official-model runs are kept in `official_registry` and
`C:\ProgramData\CoastalWarning\models\official_runs`. The service wrappers set
these paths explicitly so a runtime upgrade cannot redirect training to files
inside the replaceable application directory.
Re-running the installer replaces only the generated runtime and preserves both.

Runtime updates are transactional. While the current services remain running,
the installer builds the complete virtual environment under a protected
`runtime.stage.<id>` sibling, runs dependency, bytecode, import, and credential
configuration checks, and only then enters a short commit window. The active
runtime is renamed to `runtime.previous.<id>` and the staged directory is
renamed to `runtime`; files are never copied into the live runtime in place.
The exact `coastal_risk_v1.json` artifact is copied into the staged server,
checked against the source SHA-256, and loaded during the smoke test. Deployment
therefore fails before commit if the logistic-risk model would silently fall
back to rules.

The previous directory and original Scheduled Task definitions/states are kept
until both local services, the administrator login page, and `cloudflared` pass
post-start checks. Any commit or health failure restores the exact previous
runtime and the previous bytes/attributes of managed configuration files,
restores the old task definitions, restarts the tasks that were previously
running, and restores/restarts the original `cloudflared` service. Managed
configuration ACLs are deliberately normalized to the secure installer policy
(`SYSTEM` and local Administrators only) during restore; a weaker historical
DACL is never reinstated. Only direct children with generated deployment names
and reparse-point-free trees are eligible for recursive cleanup.

Before switching, the installer also requires ports 8000 and 8001 to be free,
which catches orphaned Python children. Each candidate runtime gets a random
deployment identifier. Its Uvicorn bootstrap atomically records that identifier,
the listener PID, and the port under the protected ProgramData `run` directory.
Both Scheduled Task launchers pass the absolute runtime server as `--app-dir`;
the bootstrap validates the directory and required app/model files, then sets
the working directory and Python import path explicitly. It does not depend on
the caller's current directory.
Post-start validation requires both Scheduled Tasks to remain running and
matches each port listener to that candidate-only identity record. Windows
process executable metadata is retained only as optional diagnostics because it
can be unavailable or report a base interpreter for a `SYSTEM` task.
When `-NoStart` is used, live health checks are intentionally skipped and the
previous runtime is retained rather than being deleted.

The installer requires the system-wide Python 3.12 installation at
`C:\Program Files\Python312\python.exe`; it creates a fresh protected virtual
environment and does not depend on Codex or PlatformIO caches.

When `server\tmp\runtime-wheelhouse` exists, the installer validates its files
and installs from it with `--no-index`; otherwise it downloads the declared
runtime dependencies normally.

The device token, administrator password verifier, and administrator session
key are separate files under `C:\ProgramData\CoastalWarning\secrets`. Their
DACLs allow only `SYSTEM` and the local Administrators group. The gateway
receives only the administrator secret file paths; its device API and
administrator session authentication remain separate.

The installer does not start a PowerShell transcript because a transcript
header can include sensitive command-line parameter values. It also removes the
exact legacy `tmp\startup-install.log` file, if present, after rejecting
directories and reparse points at that path. Use the protected service logs and
`status.ps1` for operational diagnostics; neither records the administrator
verifier.

If installation fails, the installer completes rollback and atomically writes
`C:\ProgramData\CoastalWarning\logs\install-error.log`. Its restricted JSON
schema contains only the UTC time, a controlled installation phase, exception
type, redacted message and script location, and redacted rollback errors. It
never serializes the command line, invocation record, bound parameters, or
environment. Credential-shaped values are removed and the file inherits the
same `SYSTEM`/Administrators-only policy. A successful installation deletes the
previous failure record.

The public hostname exposes `http://127.0.0.1:8001` through
`https://weather.ashlxylock.uk`, including the authenticated `/admin` surface.
The unauthenticated local dashboard remains available only at
`http://127.0.0.1:8000` for local development.

After installation, run `status.ps1` from an elevated PowerShell window to
check the service, scheduled tasks, ports, credential formats and ACLs,
authenticated device health endpoints, and the public login-page route. The
status command never performs an administrator login or prints a credential.

The non-administrator deployment tests exercise real directory rename,
injected switch failure, rollback retry, path rejection, and the staged Python
runtime checks:

```powershell
powershell.exe -NoProfile -File .\ops\tests\installer-transaction.tests.ps1
powershell.exe -NoProfile -File .\ops\tests\runtime-deployment.tests.ps1
powershell.exe -NoProfile -File .\ops\tests\staged-runtime-smoke.tests.ps1
powershell.exe -NoProfile -File .\ops\tests\rollback-file-restore.tests.ps1
powershell.exe -NoProfile -File .\ops\tests\status-runtime-identity.tests.ps1
powershell.exe -NoProfile -File .\ops\tests\install-diagnostic.tests.ps1
.\server\.venv\Scripts\python.exe -m unittest ops.tests.test_run_uvicorn -v
```
