---
name: deploy-grafana-dashboard
description: 'Validate, atomically deploy, reload, and verify the provisioned greenhouse Grafana dashboard. Use when asked to publish dashboard JSON, update Grafana panels, or deploy visualization changes to a remote Grafana host.'
argument-hint: 'Grafana deployment target'
---

# Deploy Grafana Dashboard

Deploy the repository dashboard without rediscovering its provisioning layout. Never write a deployment hostname into this skill or another tracked file.

## Required Input

Set `GRAFANA_HOST` from a target supplied by the user or current conversation. Ask if it is unknown; do not guess or persist it.

Established paths and service:

- Local dashboard: `grafana/dashboards/climate.json`
- Remote dashboard: `/etc/grafana/dashboards/climate.json`
- Provisioning config: `/etc/grafana/provisioning/dashboards/greenhouse.yaml`
- Service: `grafana-server`
- Expected remote dashboard ownership/mode: `root:grafana`, `0640`

## Procedure

1. Validate locally before connecting:

   ```sh
   python3 -m json.tool grafana/dashboards/climate.json >/dev/null
   git diff --check
   ```

2. Add focused assertions for the requested panel behavior by loading the JSON with Python and inspecting fields structurally. Do not validate JSON with text replacement or regex alone.
3. Preflight the supplied host with BatchMode SSH:
   - Confirm `hostname`, `systemctl is-active grafana-server`, and `sudo -n true`.
   - Confirm the remote dashboard and provisioning config paths exist.
   - Read the dashboard file's owner, group, and mode with `stat`; preserve them during deployment.
   - Compare local and remote SHA-256 hashes. If they match, report that no deployment is needed.
4. Upload the local JSON to a temporary unprivileged path, never directly over the provisioned file.
5. On the remote host:
   - Create a temporary backup of the current dashboard with metadata preserved.
   - Install the staged file atomically with `sudo install -o root -g grafana -m 0640`.
   - Restart `grafana-server` to load the dashboard immediately instead of waiting for the provisioning scan interval.
6. Verify deployment:
   - `systemctl is-active grafana-server` reports `active`.
   - The deployed SHA-256 equals the local SHA-256.
   - Parse the deployed file as JSON, or verify the exact requested structured content when remote Python is available.
   - Inspect `journalctl -u grafana-server --since "5 minutes ago" --no-pager` for provisioning or startup errors.
   - If the dashboard change depends on an HTTP image/data endpoint, request that endpoint directly and verify its status and content type without displaying credentials.
7. If installation or restart fails, restore the backup with its original metadata, restart Grafana, and report both the original failure and rollback state.
8. On success, remove the staged file and temporary backup.

## Safety

- Do not display datasource passwords, connection strings, cookies, API keys, or the contents of datasource provisioning files.
- Do not use Grafana's database as the deployment surface; this dashboard is file-provisioned.
- Do not alter unrelated dashboards or Grafana configuration.
- Do not commit a host-specific URL unless the repository already intentionally provisions that URL and the requested change requires it.

## Completion Report

Report local and remote hash agreement, Grafana service state, provisioning-log result, endpoint result when applicable, and cleanup status.
