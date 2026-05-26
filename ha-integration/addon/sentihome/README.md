# SentiHome — Home Assistant Add-on

AI-powered home security and presence understanding, deployed as a single
Supervisor-managed add-on. Bundles every SentiHome service (core, memory,
ha-agent, notify, vlm-router, preprocessor) in one container, supervised
by s6-overlay.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**
2. Paste `https://github.com/DarinShapiro/SentiHome` and click **Add**
3. Refresh — **SentiHome** appears as a new tile
4. Click **Install**
5. Open the **Configuration** tab, pick a deployment profile, save
6. Click **Start**, then enable **Start on boot** + **Watchdog**

## After install

- Install the [SentiHome HA custom integration](../custom_components/sentihome) (HACS or manual copy) so HA entities + service calls bind to the add-on
- Point at least one NVR adapter at a camera (see DOCS.md)
- Configure your VLM backend(s); the `yellow_plus_inference` profile assumes one at `http://inference.lan:11434`

## Architecture

See [docs/architecture/02-deployment-topologies.md](../../docs/architecture/02-deployment-topologies.md)
for the three supported deployment shapes and the topology config schema.
