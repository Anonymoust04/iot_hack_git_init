# Level 2 integration review (2026-09-20)

Target: integrate `origin/lvl2_simulation_branch` into `level2-combined`, then validate Level 2.
This is a review, not a completion claim. The shared working tree still contains uncommitted work from multiple people.

## Merge status

- Tee's fetched branch is at `b85b1ac`. It had two commits beyond the common ancestor `1974d09`; both changed only `backend/fastapi_project/main.py`. The merge conflict in that file was resolved in `level2-combined`.
- A direct `HEAD..origin/lvl2_simulation_branch` diff appears to delete many Level 2 files because Tee's branch started before the combined branch. That diff is **not** the three-way merge result. Review the two Tee commits against their common ancestor instead.
- Accepted Tee's blocked-entrance-gate check and eligibility of general `Any` spots for Electric/Accessible cars. A blocked entrance now releases its reserved spot and sends the car away. Kept the combined branch's atomic spot reservation, automatic spot maintenance, CO safety fix and tests. Tee's occupied-EntrySpot queue guard and release were commented out, so the merge kept the existing direct reroute behavior. **Occupied EntrySpot rerouting still needs a working queue and simulator test.**
- Other uncommitted financial-report and frontend work was not included in the merge commit.

## Feature review for the team

| Level 2 requirement | Current combined-branch status | Check before calling complete |
| --- | --- | --- |
| Normal car flow, three zones, multiple entrances and exits | Implemented in merged `main.py`; Tee's blocked-gate and general-spot changes are included | Run cars through every entry/exit, full-park rejection, wrong-gate rerouting, and concurrent arrivals. Complete and test an occupied-EntrySpot queue. Confirm gates close. |
| Usage cycles and preventive maintenance | Gates/lights/fans tracked in memory; alarm-driven repair exists; combined branch's automatic spot repair was preserved | Add or verify spot cycles, durable history, threshold scheduling, repair outcomes, and safe operation during failures. |
| Broken/unavailable components and dashboard | Webhooks, sync and dashboard display exist | Trigger a real failure and repair; verify simulator, database event, audit row, and UI all agree. |
| CO control and lighting | `main.py` has CO fan and simulator-hour lighting controllers. Focused fake-simulator tests pass, including Safe fan shutdown and 05:00/06:00/17:00/18:00 light boundaries | Restart backend with merged code; verify a live high-to-Safe CO transition and both day/night light transitions. The clock comes from simulator webhooks. |
| Vehicle duration and charges | Automatic exit charging exists; financial-summary code reads paid sessions | Test each vehicle type and one complete paid exit; verify the simulator charge and database amount agree, with no duplicate charge. |
| Events, login attempts, audit, penalties, daily and financial reports | Backend services/routes and UI pages exist; financial-summary changes are uncommitted | Run a clean MySQL test suite in a dedicated test database, then verify authenticated pages with populated live data. The current running backend did not expose the new financial route at the last check. |
| RBAC | `/api/control` and financial-summary use permissions | Protect the direct simulator-control routes in `main.py`; check repair requires `REPAIR` and the UI uses effective permissions. |
| Signed webhooks | Bad supplied signatures are logged by the database worker | Missing signatures are accepted by default, and `main.py` processes webhooks before the worker validates them. Reject and log unsigned or invalid calls before any car or component action. |
| Manually parked car with missed sensors | No complete recovery flow found | Identify/register the car, estimate duration, charge once, free the spot, log it, and route through an exit. |

## Verification already observed

- Local FastAPI `/system-status` reported backend and simulator online, but reported **127 cars inside for 90 spots**. Investigate state consistency before judging.
- Focused fake-simulator environment and merge tests passed using a dedicated test DB name; these did not exercise a real level.
- Parking-flow and webhook tests: **21 passed on real Aiven MySQL** using the dedicated `carpark_test_merge_jiaying` database. No shared test database was wiped.
- The full Level 2 MySQL suite is **not currently verified**; `docs/JIAYING_LEVEL2_PLAN.md` records prior failures and connection problems.

## Safe next integration step

Have the team review the remaining checklist rows above. Finish and commit each person's own uncommitted files, then run a real simulator entry-to-exit scenario and the complete MySQL suite in a dedicated test database before opening the PR to `main`. Keep the review open until the remaining rows have live evidence.
