# Runbooks

Operational procedures — each one names its trigger, its owner, and the command that proves
it worked. Behaviour SoT stays the code; a runbook is the order you do things in.

| Runbook | Trigger |
| --- | --- |
| [New workstation](new-workstation.md) | moving R&D to another machine — settle the old one, bootstrap the new one, verify |
| [Deploy to Render](deploy-render.md) | shipping the API and the Telegram bot to production (Render is the default target; Fly is standby) |

Related: [`config/workstation.yml`](../../config/workstation.yml) ·
[architecture](../architecture/) · [ADRs](../adr/README.md) ·
[developer guide](../developer-guide/README.md) · [platform](../platform/README.md)
