---
name: deploy-greenhouse-services
description: 'Build, transfer, install, and verify the greenhouse collector or greenhouse datastore consumer Debian package. Use when asked to deploy, upgrade, release, or reinstall either Python service on a remote host.'
argument-hint: 'collector|consumer and deployment target'
---

# Deploy Greenhouse Services

Deploy either Debian-packaged service with the same validated workflow. Never write a deployment hostname into this skill or another tracked file.

## Required Inputs

Determine these before running commands:

- `COMPONENT`: `collector` or `consumer`.
- `DEPLOY_HOST`: SSH destination supplied by the user or current conversation. Ask if it is unknown; do not guess or persist it.
- Optional version and Debian revision overrides. Use repository defaults unless the user requests otherwise.

Use this component map:

| Component | Build command | Package glob | Service | Config |
|---|---|---|---|---|
| collector | `bash packaging/build-deb.sh` | `dist/greenhouse-monitor_*_all.deb` | `greenhouse-monitor` | `/etc/greenhouse/greenhouse.env` |
| consumer | `bash packaging/consumer/build-deb.sh` | `dist/greenhouse-datastore_*_all.deb` | `greenhouse-datastore` | `/etc/greenhouse-datastore/greenhouse-datastore.env` |

## Procedure

1. From the repository root, run `git status --short`. Do not revert unrelated or uncommitted work. Remember that the builders package the current working tree, not only committed files.
2. Run the narrow Python syntax check:
   - Collector: `python3 -m py_compile greenhouse/greenhouse_monitor.py`
   - Consumer: `python3 -m py_compile consumer/greenhouse_datastore/consumer.py`
3. Run the component build command. If overriding versions, pass the version and revision as the build script's first two arguments.
4. Identify the exact artifact emitted by the build. Do not select an older matching file from `dist/`.
5. Validate it before transfer:
   - Run `dpkg-deb --info "$PACKAGE_PATH"` and verify package name, version, and `Architecture: all`.
   - Extract into a fresh temporary directory with `dpkg-deb -x`.
   - Confirm the changed source and required dependencies are present in the extracted payload.
   - Run `shasum -a 256 "$PACKAGE_PATH"` and retain the hash in the deployment output.
6. Preflight the supplied host with BatchMode SSH:
   - Confirm SSH connectivity and expected machine identity with `hostname`.
   - Confirm `sudo -n true` succeeds. If it does not, stop and ask the user to perform the privileged step directly; never request a password.
   - Query the installed package with `dpkg-query -W` and compare versions. Do not downgrade unless explicitly requested. Use reinstall semantics if versions are equal.
   - Confirm the service config exists. Inspect only non-secret keys needed for the change; never print the entire environment file.
7. Copy the artifact to `/tmp/$(basename "$PACKAGE_PATH")` with `scp -o BatchMode=yes`.
8. Install over SSH with noninteractive APT and preserve the deployed conffile explicitly:

   ```sh
   sudo -n env DEBIAN_FRONTEND=noninteractive apt-get \
     -o Dpkg::Options::=--force-confold install -y /tmp/PACKAGE_FILE
   ```

   The package post-install script recreates its virtual environment, installs dependencies from the network, enables the unit, and restarts it. Allow this command to finish; do not separately interrupt or restart during package configuration.
9. Verify on the target:
   - `dpkg-query -W PACKAGE_NAME` reports the intended Debian version.
   - `systemctl is-active SERVICE_NAME` reports `active`.
   - The package-managed Python executable can import any newly added dependency.
   - `journalctl -u SERVICE_NAME -n 30 --no-pager` shows a clean startup and resumed work.
   - Recheck the specific non-secret setting involved in the deployment when relevant.
10. Only after verification succeeds, remove the transferred package from `/tmp`. Confirm the service remains active.

## Failure Handling

- If package installation fails, retain the remote artifact for diagnosis and inspect `dpkg --audit`, service status, and the relevant journal.
- If the service fails after upgrade, report the exact package version and logs before changing anything else.
- Do not run `apt autoremove`; unrelated package cleanup is outside this deployment.
- Do not overwrite a deployed config with the package maintainer version unless the user explicitly requests it.

## Completion Report

Report the component, old and new package versions, service state, relevant configuration confirmation, dependency/import result, and whether the remote temporary artifact was removed.
