# Run sheet: contention-2 — staggered connect and Classic link policy

> **Proposed sheet, not yet run.** The `WMR_CFG_...` flags below exist on `main`; V2 and V3 were run (see [`contention-2-results.md`](contention-2-results.md)), but **V1 has still not been run**. Nothing here is a hardware result. Figures quoted from earlier runs are user-reported. The ONY-176 run order is in [`single-esp32-levers.md`](single-esp32-levers.md).

Question: LEFT loses about 0.5 % on its own but about 19 % with RIGHT connected, while RIGHT loses about 10.8 % on its own and about 19 % with LEFT connected. Is that extra loss created by the way the second Classic link is brought up, by sniff mode in the current link policy, or by the ESP32 not scheduling the two ACL links as master? The working hypothesis is still radio contention between the two Classic links and the BLE link, but it is not proven, and the asymmetry (LEFT almost clean alone, RIGHT not) says RIGHT also has a link problem of its own.

Why the runs are interleaved: identical baselines have read 15–26 % across boots, and RIGHT on FF12 read 33.6 % then 23.9 % with nothing moved (user-reported). A single before/after cannot detect a difference smaller than that. All comparisons below are therefore between REF and VAR runs of the *same commit* in one session, alternated A/B/A/B; the absolute level is never compared across sessions.

## 0. What the existing runs already say

All figures are user-reported, FF14, MTU 517, phone streaming, `last5s_pct` after a 30 s settle. Sources: `docs/runs/README.md`, `docs/runs/sheets/single-controller.md`, `docs/runs/sheets/left-coex.md`, WMR_Bridge issue #18.

| Setup | LEFT | RIGHT |
|---|---|---|
| No phone connected | 9–13 % | 9–13 % |
| LEFT alone | ~0.5 % | — |
| RIGHT alone | — | ~10.8 % |
| Both connected, same build and position | ~19.6 % | ~19.1 % |
| Both connected, long baseline | 18–19 % | 18–20 % |

- In the long baseline, RIGHT fell from about 20 % to about 11 % the moment LEFT went to sleep (user-reported). That is consistent with LEFT's traffic adding to RIGHT's loss.
- `mode=active` throughout every reported run, and no `link mode:` change line has been seen yet; D7 in `docs/TESTING.md` is only **PARTIAL**. This lowers the prior for a sniff effect, but it does not remove it: the mode line has not been exercised end to end (idle-then-move), and a policy can be changed without a mode change if the link was never in sniff.
- Current policy, `src/wmr_bridge_main.c` around L109: `gap_set_default_link_policy_settings(LM_LINK_POLICY_ENABLE_SNIFF_MODE | LM_LINK_POLICY_ENABLE_ROLE_SWITCH)` and `hci_set_master_slave_policy(HCI_ROLE_MASTER)`.
- Controllers connect through `hid_host_connect()` in `src/wmr_controller.c` (L247) and also through the *incoming* reconnect path (`handle_incoming_connection()` L580, which accepts with `hid_host_accept_connection()` at L478). **The incoming path must keep working in every variant**; it is how a controller reconnects after it sleeps.
- The three flags below are compile-time options in `src/wmr_config.h`, written as `#ifndef WMR_CFG_...` defaults. Each defaults to 0 = current behaviour.

## 1. Variants

All three are proposed as a *single* flag each. Build only the one Nick picks, plus the REF image from the same commit. The BTstack file/line references below were checked in the tree at commit `e38553977a25fb0b55b383c72c289be0975f422c`, the commit `docs/BUILD_WINDOWS.md` names for this project; they are not from a newer BTstack.

### V1 — staggered start

| | |
|---|---|
| Flag | `WMR_CFG_STAGGER_FIRST_HAND` — 0 = off (current), 1 = LEFT first, 2 = RIGHT first. Gate time fixed at **30 s**. |
| What changes | The second hand's HID connection is held until the first hand has delivered input reports continuously for 30 s. This gates three paths, not one: the outgoing connect (`connect_hand()`), the discovery connect (`disc_connect_next()`), and the incoming reconnect (`handle_incoming_connection()` — keep the HID CID queued and do **not** call `hid_host_accept_connection()` while the gate is closed). A 500 ms timer retries the connect/accept once the gate is open, and logs `STAGGER: <hand> held, first=<hand> reports=<n>` and `STAGGER: <hand> released at t=<s>`. The gate is on **report flow**, not necessarily on the ACL: an incoming ACL can already exist while its HID channel is held. That is deliberate, because it separates "link present" from "reports flowing". If a truly link-free alone window is wanted, leave the second controller asleep until the mark; the flag then only makes the release automatic. Record which of the two was done. |
| BTstack API | `hid_host_connect()` and `hid_host_accept_connection()` in `src/classic/hid_host.h` (L170 and L179). `hid_host_decline_connection()` (L186) is deliberately not used by the gate: it drops the ACL and can be read by the controller as a failed connection. Timer: `btstack_run_loop_set_timer()` / `btstack_run_loop_add_timer()` in `src/btstack_run_loop.h`. No HCI command is added. `gap_register_classic_connection_filter()` (`src/gap.h` L520, `src/hci.c` L10664) is **not** used: BTstack rejects with `ERROR_CODE_CONNECTION_REJECTED_DUE_TO_SECURITY_REASONS` (`src/hci.c` around L4690), which a controller can interpret as a pairing failure. |
| ESP32 support | Not a controller feature; this is host-side timing only. |
| Reconnect risk | **Medium.** Holding an incoming HID connection can time out on the controller, which may go back to sleep instead of retrying. The run is only valid if the log shows `Incoming HID` followed by `accepting incoming HID connection` after the gate, and the hand then streams. If it does not, wake the controller by hand and retry once; record it as a failed gate, not as a loss result. With the flag at 0 the current path is unchanged. |
| Supports the working (contention) hypothesis if | The "alone" window is clean (≤ 2 %) and the "both" window is back at 15 % or more. The extra loss then arrives with the second hand's report traffic, which is what "contention" predicts; it also says the cost is not just connection setup. |
| Supports the setup/timing explanation (a possible fix) if | The VAR "both" window has LEFT ≤ 5 % and at least 5 points below the lowest REF "both" window in every pair, with the "alone" window ≤ 2 %. Delaying the second connection then changed the steady state, not only the transient. |
| Refutes the setup/timing explanation if | The "alone" window is clean (≤ 2 %) but the "both" window is back within the REF range (≥ 15 %). The loss starts with RIGHT's *traffic*, not with its connection setup; that points at ongoing contention or the controller itself. |
| Inconclusive if | The alone window is already high, or the second hand never streams after the gate, or the gate log is missing. |

Implementation note for the later task: "streaming for 30 s" should mean continuous input reports, not merely an open ACL. Timestamp the first input report per hand and open the gate at `first_report_t + 30 s`; reset the timestamp if the hand disconnects. The gate must not stop the controller's normal reconnect from being *accepted* eventually, or the incoming path is broken when the flag is on.

### V2 — Classic link policy without sniff

| | |
|---|---|
| Flag | `WMR_CFG_CLASSIC_NO_SNIFF` — 0 = off (current), 1 = sniff disabled. |
| What changes | At init, set the default policy to `LM_LINK_POLICY_ENABLE_ROLE_SWITCH` only. After every ACL connection (outgoing or incoming) send a per-handle `HCI_Write_Link_Policy_Settings` with the same value and `gap_sniff_mode_exit()` in case the link entered sniff before the write. Read the policy back with `HCI_Read_Link_Policy_Settings` and log both the write status and the read value. Without the per-handle write the default only covers new links, not a reconnect that raced the init. |
| BTstack API | `gap_set_default_link_policy_settings()` — `src/gap.h` L439, implemented in `src/hci.c` L5944 (sends `hci_write_default_link_policy_setting`, `src/hci_cmd.c` L781). Per handle: `hci_send_cmd(&hci_write_link_policy_settings, handle, settings)` — command declared `src/hci_cmd.h` L519, defined `src/hci_cmd.c` L764, opcode `HCI_OPCODE_HCI_WRITE_LINK_POLICY_SETTINGS`, format `"H2"`. Exit sniff: `gap_sniff_mode_exit()` — `src/gap.h` L1478, `src/hci.c` L10821, sends `hci_exit_sniff_mode` (`src/hci_cmd.c` L703). Read back: `hci_read_link_policy_settings` (`src/hci_cmd.h` L462, `src/hci_cmd.c` L756). Policy bits: `LM_LINK_POLICY_DISABLE_ALL_LM_MODES` / `..._ENABLE_ROLE_SWITCH` / `..._ENABLE_SNIFF_MODE` in `src/bluetooth.h` L465–468. |
| ESP32 support | Standard HCI commands. ESP-IDF v6.1's Bluedroid host has a sniff path (`BTM_SetLinkPolicy`, `components/bt/host/bluedroid/stack/btm/btm_acl.c` around L995) and masks bits the controller does not advertise in `HCI_Read_Local_Supported_Features`; the controller itself is the closed `libbtdm_app.a`, so the source cannot confirm it honours the write. BTstack does not mask unsupported bits. The run's evidence is the Command Complete status and the read-back: status `0x00` and sniff bit clear means the write was accepted; `ERROR_CODE_COMMAND_DISALLOWED` (0x0C) or `ERROR_CODE_UNSUPPORTED_FEATURE_OR_PARAMETER_VALUE` (0x11) means stop, the variant is not testable on this controller. |
| Reconnect risk | **Low.** The connect/accept logic is untouched; only policy is written after the link exists. A rejected write is logged and the link continues. Flag 0 restores the current two-bit policy. |
| Supports the sniff sub-hypothesis if | Both hands' `last5s_pct` fall by at least 5 points against REF in every pair, the write status is 0x00, and the read-back shows sniff clear. |
| Refutes the sniff sub-hypothesis if | Loss stays within the REF range while the write/read-back confirms sniff is off, or every run was `mode=active` with no mode changes. In the second case the links were never sniffing, so the policy cannot explain the loss; record it as "no effect shown", not as "sniff is safe". |
| Inconclusive if | The write is rejected, the read-back does not match, or the mode line is missing from the log. |

### V3 — Classic link policy, force master and no role switch

| | |
|---|---|
| Flag | `WMR_CFG_CLASSIC_FORCE_MASTER` — 0 = off (current), 1 = role switch disabled and master requested on both links. |
| What changes | At init call `gap_set_allow_role_switch(false)` so outgoing connections ask for no role switch and stay master, and keep `hci_set_master_slave_policy(HCI_ROLE_MASTER)` (already present). After every ACL connection, log `gap_get_role()`; if the link is slave, call `gap_request_role(address, HCI_ROLE_MASTER)` and log the `HCI_EVENT_ROLE_CHANGE` status and the new role. Write `LM_LINK_POLICY_DISABLE_ALL_LM_MODES` (0) per handle so the controller does not allow role switch or sniff on that link, and read it back. |
| BTstack API | `gap_set_allow_role_switch()` — `src/gap.h` L445, `src/hci.c` L5950. `gap_request_role()` — `src/gap.h` L411, `src/hci.c` L10070, sends `hci_switch_role_command` (`src/hci_cmd.c` L749). `hci_set_master_slave_policy()` — `src/hci.h` L1716, `src/hci.c` L10653. Role read: `gap_get_role()` (`src/gap.h` L400, `src/hci.c` L10062) and `HCI_EVENT_ROLE_CHANGE` (BTstack updates the cached role in `src/hci.c` around L5234). Policy write: `hci_write_link_policy_settings` as in V2. `HCI_ROLE_MASTER` / `HCI_ROLE_SLAVE` in `src/bluetooth.h` L382–383. |
| ESP32 support | `HCI_Switch_Role` is a standard Link Policy command, but the controller may refuse it, and a dual-mode controller with two ACL links may not be able to be master on both at once. The only safe statement is that the source does not prove it. The run's evidence is the role log per link: if either link stays slave after the request, the variant is only partly applied; report it and do not attribute a loss change to "master on both". |
| Reconnect risk | **Low–medium.** A role switch on reconnect can be refused; log it and continue. The incoming HID path is untouched. Flag 0 restores the current behaviour. |
| Supports the scheduling/role sub-hypothesis if | The role log shows both links master in the VAR runs and both hands' `last5s_pct` fall by at least 5 points against REF in every pair. |
| Refutes the scheduling/role sub-hypothesis if | The REF run already shows both links master (V3 is then a no-op; skip it), or a link stays slave after the request, or the loss stays within the REF range. |
| Inconclusive if | Role logging is missing, the switch is rejected on one link, or the controller disconnects during the switch. |

Implementation note for the later task: add one role line per link at connection complete and on every `HCI_EVENT_ROLE_CHANGE`; without it this variant cannot be read. `hci_set_master_slave_policy(HCI_ROLE_MASTER)` is already in the current code, so if the reference log shows master on both, V3 adds nothing and should be stopped before any run.

### Considered and not proposed: ACL QoS / flow specification

BTstack does expose the commands: `gap_qos_set()` (`src/gap.h` L1500, `src/hci.c` L10839, sends `hci_qos_setup`), `hci_qos_setup` (`src/hci_cmd.h` L450, `src/hci_cmd.c` L734) and `hci_flow_specification` (`src/hci_cmd.h` L436, `src/hci_cmd.c` L795). They are real and could be sent.

They are not proposed as a variant for three reasons. (1) There is no Classic "poll interval" HCI knob in BTstack; the poll interval is a BR/EDR controller scheduling detail, and the LE connection interval (`hci_le_create_connection`) is a different link. (2) `HCI_QoS_Setup` and `HCI_Flow_Specification` are advisory; a controller may return `Command Disallowed` or `Unsupported`. ESP-IDF v6.1's ESP32 controller has no Classic QoS option in `components/bt/controller/esp32/Kconfig.in`; the controller is the closed `libbtdm_app.a`, so "the ESP32 honours it" cannot be established from source. (3) A null result would be uninterpretable: it could mean the parameters do not matter or that the controller ignored them. If Nick wants this later, the first step is a one-off `HCI_Read_Local_Supported_Commands` check of the QoS bit (`HCI_QOS_SETUP_SUPPORTED` in Bluedroid's `hcidefs.h`), then a single command with the Command Complete status logged; only if the status is 0x00 is a run worth designing. This matches the task rule "only if BTstack and the ESP32 controller actually support it".

## 2. Build the images (Anya, ESP-IDF PowerShell) — **not yet run**

The flags exist on `main`. Build the REF and the one VAR image from the same commit, into separate folders, once. Do not assign `$env:Path`; the ESP-IDF profile appends to it, and appending is the only safe change. For the current V1 build commands and A/B/A block, use [`single-esp32-levers.md`](single-esp32-levers.md) §2; for V2/V3 the steps below still apply.

```powershell
# Load ESP-IDF. This profile appends the tool paths; never replace $env:Path with a new value.
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"

# Refresh the copied tree from the implementation commit (replace <sha> after Nick picks a variant).
$h = "<implementation sha>"
$clone = "$env:TEMP\WMR_Bridge-contention2-$h"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone checkout $h
git -C $clone log --oneline -1
robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_controller|wmr_linkdiag"   # must print lines

# REF: flag off, current behaviour. Fresh folder so an old cache cannot hide the define.
if (Test-Path build-wmr-contention2-ref) { Remove-Item build-wmr-contention2-ref -Recurse -Force }
Remove-Item Env:EXTRA_CFLAGS -ErrorAction SilentlyContinue
idf.py -B build-wmr-contention2-ref build
Select-String build-wmr-contention2-ref\compile_commands.json -Pattern "WMR_CFG_STAGGER|WMR_CFG_CLASSIC"   # expect no match

# VAR: pick ONE of these. Fresh folder per variant; EXTRA_CFLAGS is read at configure time.
$env:EXTRA_CFLAGS = "-DWMR_CFG_STAGGER_FIRST_HAND=1"          # V1, LEFT first
# $env:EXTRA_CFLAGS = "-DWMR_CFG_CLASSIC_NO_SNIFF=1"          # V2
# $env:EXTRA_CFLAGS = "-DWMR_CFG_CLASSIC_FORCE_MASTER=1"      # V3
idf.py -B build-wmr-contention2-var build
Select-String build-wmr-contention2-var\compile_commands.json -Pattern "WMR_CFG_"                       # must show the define above
Remove-Item Env:EXTRA_CFLAGS
```

If the `compile_commands.json` check does not show the flag, stop; the build is not the variant. `EXTRA_CFLAGS` is the ESP-IDF v6.1 mechanism (`tools/cmake/project.cmake`, "use EXTRA_CFLAGS ... to add more priority options"), so no source or CMake change is needed to select the flag.

Flash a finished folder without rebuilding (the same route as `left-coex.md` §2). `idf.py flash` can decide that the source changed since that folder was built and rebuild with the wrong config; `write_flash` uses the files already in the folder.

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32\build-wmr-contention2-ref
Test-Path flash_args                      # must be True
python -m esptool --chip esp32 -p COM5 -b 115200 write_flash "@flash_args"

# VAR: same command from build-wmr-contention2-var
```

After each flash, open the monitor, note the boot banner and which folder/image was flashed, and check the variant's one-line marker (V1 `STAGGER:`; V2/V3 policy status and role). If the marker is missing, the image is wrong; stop.

## 3. Run protocol

Common setup, every session:

- Controllers face down, still, at the **same marked spot** for the whole session; note the distance to the ESP32, orientation and phone position. A photo is the record; the position is not instrumented.
- Phone: nRF Connect (reference client), connect to `WMR Bridge`, Request MTU **517**, connection priority **High**, notify on **FF14 only**. The app is acceptable only if the `STREAM` line shows `phone=1 sub=1 stream=batched mtu=517`; record the app version if used.
- Confirm `STREAM` shows both hands with a live `rate_rx` (about 150–200/s), `mtu=517`, `stream=batched`, and `conn=[...]` recorded. Do not start timing while `rate_rx` is 0 or `sub=0`.
- **30 s settle** after the last connection (or after the second hand is released in V1), then a **2-minute window**. Use the `LINKLOG,` rows only; the metric is the mean of `last5s_pct` over the 24 rows of the window, with min–max. `cum_pct` is recorded for context but is not compared across boots.
- Reset/flash between every run. A run with a disconnect, a controller sleep, or a missing `LINKLOG` row in the window is discarded and repeated.
- With the flag off, both controllers connect normally. For V1 the VAR run deliberately has only the first hand connected during the "alone" window; the second hand is woken at the mark, and the "both" window starts 30 s after its first report.

One block is A/B/A/B. Do **two blocks per session** (four pairs), so the first REF is the session reference and the last REF is a drift check:

| Order | Image | What happens | Windows |
|---|---|---|---|
| A1 | REF | both hands wake and connect normally; 30 s settle | both, 2 min |
| B1 | VAR | V1: first hand only; 30 s settle; then wake the second hand and wait for the release; 30 s settle. V2/V3: both hands; 30 s settle | V1: alone 2 min, then both 2 min; V2/V3: both 2 min |
| A2 | REF | as A1 | both, 2 min |
| B2 | VAR | as B1 | as B1 |
| A3 | REF | as A1 | both, 2 min |
| B3 | VAR | as B1 | as B1 |
| A4 | REF | as A1 | both, 2 min |
| B4 | VAR | as B1 | as B1 |

How many repeats, given the 15–26 % spread:

- The spread that matters *between* boots is about 11 points (15–26 %); the block-to-block spread *inside* one continuous run was about 1 point (user-reported). The A/B/A/B order is what makes a paired comparison possible: it interleaves the two images and lets a slow drift show up as a trend across the four REF runs.
- **Minimum for a verdict: four pairs (eight runs) in one session, and the four REF `last5s_pct` means must span 3 points or less.** If they span more, the session drifted; discard it and repeat another day, do not average it.
- **A pass needs a second session** (another power-up, ideally another day) with the same direction, so eight pairs in total. This is a judgement based on the reported 1-point within-run and 11-point between-boot spreads, not a formal power calculation.
- **Do not chase effects under 3 points.** Call them "not shown". If a variant shows no direction after two pairs, stop that variant; a 5-point effect is the smallest this design is meant to detect.
- If a REF run lands outside the other three by more than 3 points, stop and check the position/phone before continuing; do not silently drop it.

Reconnect check after each run (this is a functional check, not a loss result): put the gated/second controller to sleep, wake it, and confirm the incoming path accepts it and `rate_rx` resumes. For V1 this is the main risk; a variant that breaks the reconnect is rejected regardless of its loss figures.

## 4. Pass criteria (fixed before the data)

| Variant | Pass | Refute | Not testable / stop |
|---|---|---|---|
| V1 staggered start | In every VAR "both" window: LEFT ≤ 5 % and ≥ 5 points below the lowest REF LEFT; RIGHT ≤ 13 % and ≥ 5 points below the lowest REF RIGHT; and the VAR "alone" window ≤ 2 % for the first hand. | LEFT "alone" ≤ 2 % but LEFT "both" within the REF range (≥ 15 %): the loss starts with the second hand's traffic, not its setup. | The gate never releases; the second hand never streams; the alone window is already ≥ 10 %; or the reconnect check fails. |
| V2 no sniff | In every VAR window: both hands ≥ 5 points below the lowest REF for that hand; policy write status 0x00; read-back shows the sniff bit clear in both links. | Loss within the REF range while the read-back confirms sniff is off; or every run is `mode=active` with no mode changes (the links were never sniffing). | Write rejected (0x0C/0x11), read-back mismatch, or mode line missing. |
| V3 force master | Both links log `role=master` in the VAR runs and both hands ≥ 5 points below the lowest REF for that hand. | REF already shows both links master (skip V3 as a no-op); a link stays slave after the request; or loss within the REF range with master confirmed. | Role logging missing; switch rejected on one link; controller disconnects during the switch. |

A result that moves loss in the wrong direction by ≥ 5 points is a real adverse effect and is reported as such, not as "no effect". A result between 3 and 5 points is "possible, not shown" and needs the second session before anyone acts on it.

## 5. Results table template (empty)

Copy this block per session. One row per run, per hand, per window. `last5s_pct` is the mean over the 24 `LINKLOG,` rows in the window; `min–max` is the range of those rows; `cum_pct` is the last `cum_pct` in the window. `mode` and `role` come from the `link:` line and the role log, not from `LINKLOG`.

**Session:** `<date/time>` — **position:** `<distance, orientation, photo>` — **phone:** `<model, Android>` — **ESP32:** `<board, COM>` — **firmware:** `<sha>` — **REF/VAR flags:** `<...>` — **MTU / interval:** `<...>`

| # | Image | Window | Hand | last5s_pct mean | min–max | cum_pct end | rate_rx mean | dev_missing_delta | rssi | lq | fcc | mode | role | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | REF | both | LEFT | | | | | | | | | | | |
| A1 | REF | both | RIGHT | | | | | | | | | | | |
| B1 | VAR | alone | LEFT | | | | | | | | | | | |
| B1 | VAR | both | LEFT | | | | | | | | | | | |
| B1 | VAR | both | RIGHT | | | | | | | | | | | |
| A2 | REF | both | LEFT | | | | | | | | | | | |
| A2 | REF | both | RIGHT | | | | | | | | | | | |
| B2 | VAR | alone | LEFT | | | | | | | | | | | |
| B2 | VAR | both | LEFT | | | | | | | | | | | |
| B2 | VAR | both | RIGHT | | | | | | | | | | | |
| A3 | REF | both | LEFT | | | | | | | | | | | |
| A3 | REF | both | RIGHT | | | | | | | | | | | |
| B3 | VAR | alone | LEFT | | | | | | | | | | | |
| B3 | VAR | both | LEFT | | | | | | | | | | | |
| B3 | VAR | both | RIGHT | | | | | | | | | | | |
| A4 | REF | both | LEFT | | | | | | | | | | | |
| A4 | REF | both | RIGHT | | | | | | | | | | | |
| B4 | VAR | alone | LEFT | | | | | | | | | | | |
| B4 | VAR | both | LEFT | | | | | | | | | | | |
| B4 | VAR | both | RIGHT | | | | | | | | | | | |

Build record:

| Tag | Commit | Flag | Build folder | `compile_commands.json` check | Flashed? |
|---|---|---|---|---|---|
| REF | | off | `build-wmr-contention2-ref` | no `WMR_CFG_` match | |
| VAR | | `WMR_CFG_...` | `build-wmr-contention2-var` | define present | |

Paste back per run: the `LINKLOG_FIELDS` header once, all `LINKLOG,` lines of the window, the `link:` lines for the same period, any `STAGGER:` / role / policy lines, the firmware hash, the image folder, and anything that changed (phone moved, controller slept, someone walked by). Say which window is "alone" and which is "both"; the row labels above are the mapping.
