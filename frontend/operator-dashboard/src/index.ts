/**
 * @sentihome/operator-dashboard — standalone diagnostics dashboard.
 *
 * Optional: falls back to HA's built-in dashboard if not deployed.
 * Target audience: operators and developers needing deep diagnostics,
 * variant testing rollouts, AI chat synthesis, replay tooling.
 *
 * See architecture: docs/architecture/17-observability.md (Levels 2 and 3)
 *
 * Implementation tracked in:
 *   planning/epics/12-observability.md
 *   planning/epics/14-calibration-spatial.md
 */

import { VERSION as SHARED_VERSION } from '@sentihome/shared';

export const VERSION = '0.1.0';
export const SHARED_LIB_VERSION = SHARED_VERSION;
