/**
 * @kukiihome/ha-cards — custom Lovelace cards for Home Assistant.
 *
 * Cards register themselves with HA's CustomElementRegistry on import.
 * See architecture: docs/architecture/17-observability.md (Level 1 overview)
 *                   docs/architecture/15-alerting-and-actions.md (alert UX)
 *
 * Card implementations are tracked in:
 *   planning/epics/12-observability.md
 *   planning/epics/15-failure-modes.md
 */

import { VERSION as SHARED_VERSION } from '@kukiihome/shared';

export const VERSION = '0.1.0';
export const SHARED_LIB_VERSION = SHARED_VERSION;
