# Signal Gen Bench Test — PR #7 + PR #14 features

**Setup:** BNC from sig gen **CH1 → scope CH1** (1 MΩ input). ~15 min total.
Launch: `.venv/bin/python gui.py`

---

## A. Connect & populate (PR #7, #11)

- [ ] SG tab status shows **Connected: BK,4055B,…** (green) on launch
- [ ] CH1/CH2 input fields match the **front panel's** current settings
- [ ] Gray *applied* readouts beside each field match the front panel too
- [ ] Output buttons match reality (green **Output: ON** only if a channel is actually on)

## B. Adaptive fields (#8) — flip waveform on CH1, watch the form

- [ ] SINE → Frequency / Amplitude / Offset only (Basic mode)
- [ ] SQUARE → **Duty Cycle (%)** appears
- [ ] RAMP → **Symmetry (%)** appears (duty gone)
- [ ] PULSE → Duty appears (Rise/Fall/Delay only after step C)
- [ ] DC → only DC Offset remains; NOISE → no parameter fields
- [ ] Preview redraws to the right shape on every switch

## C. Basic/Advanced toggle

- [ ] Toggle **Advanced mode** on → Phase, Load (Ω), Polarity appear; with PULSE selected, Rise/Fall/Delay appear
- [ ] Toggle off → they hide again (values are still applied regardless — verified in E)

## D. Apply vs Output split (#9) — the big one

1. CH1: SQUARE, 1000 Hz, 2 Vpp, offset 0, **Duty 25** → **Apply CH1 Settings**
   - [ ] Front panel shows the new config
   - [ ] **Output LED did NOT change** (Apply must not touch output)
   - [ ] Applied readouts update to 1000 / 2 / 0 / 25
2. Press **Output** button
   - [ ] Button goes green **Output: ON**, front-panel output LED lights
   - [ ] Scope shows a 1 kHz square, high ~25% of the period
   - [ ] Scope tab → Get CH1 Measurements: Frequency ≈ 1 kHz, Pk-Pk ≈ 2 V
3. Press **Output** again → [ ] OFF, LED out, scope flatlines

## E. Applied readouts catch clamping (#11)

- [ ] Enter Frequency `999999999` (1 GHz) → Apply → input still shows your number, but the **applied readout shows the instrument's clamped max** (≠ input) — or an error dialog, either proves the readback works
- [ ] Restore a sane frequency → Apply → applied matches input again

## F. Load control (#10) — Advanced mode on, CH1 SINE 1 Vpp, output ON

- [ ] Load shows **High-Z** (not "HZ") and scope reads ≈ 1 Vpp
- [ ] Set Load `50` → Apply → front panel shows 50 Ω, **scope now reads ≈ 2 Vpp** (amplitude is calibrated *into 50 Ω*; the 1 MΩ scope sees double)
- [ ] Type a custom value `600` → Apply → front panel shows 600 Ω (no error)
- [ ] Back to High-Z → Apply → scope reads ≈ 1 Vpp again
- [ ] Junk load (`fifty`) → Apply → clean error dialog, nothing sent

## G. Preview accuracy (#12)

- [ ] SQUARE duty `10` → preview shows narrow pulses (live while typing)
- [ ] RAMP symmetry `100` → rising sawtooth; `0` → falling
- [ ] Advanced: SINE phase `90` → preview starts at the crest
- [ ] After Apply + Output ON: **preview shape ≈ scope shape** for each of the above

## H. Presets (PR #7 + extended schema)

1. CH1: SQUARE duty 25; CH2: RAMP symmetry 30 → save as `bench_test`
   - [ ] Appears in dropdown; `presets/siggen_presets.json` contains `duty_pct`/`sym_pct`
2. Change both channels to SINE defaults → Apply both
3. Load `bench_test`
   - [ ] Inputs restore **including duty/symmetry**, config pushed (applied readouts + front panel confirm)
   - [ ] **Output state unchanged** by the preset load
4. Delete `bench_test` → [ ] gone from dropdown and file

## I. Error handling & reconnect

- [ ] Frequency `abc` → Apply → error dialog, GUI alive
- [ ] Duty `150` → Apply → validation error ("between 0 and 100"), nothing sent
- [ ] (Optional) Unplug sig gen USB → Apply → error dialog; replug → **Reconnect** → status green, fields repopulate

## J. Channel independence

- [ ] Configure + Apply CH2 only → CH1 applied readouts and front-panel CH1 unchanged
- [ ] CH2 Output button drives only CH2's LED

## K. Arbitrary waveforms (PR: sg-arb-upload, #13)

1. CH1 → waveform **ARB** → [ ] "Arb Waveform:" row appears with **Waveform Editor…** button
2. Open editor → **Save CSV Template…** to e.g. `~/arb_template.csv`
   - [ ] File has `value` header + 32 rows (one sine period)
3. Edit a few rows in the CSV (e.g. clip the top: change values > 0.8 to 0.8) → **Load CSV…**
   - [ ] Info shows "32 points loaded", preview shows the clipped sine
4. Name `clipsine`, CH1 freq 1000 / amp 2 / offset 0 → **Upload & Select on CH1**
   - [ ] No error; channel panel switches to ARB, arb name label shows `clipsine`
   - [ ] Applied readout (gray, next to Waveform Editor) shows `clipsine`
   - [ ] Front panel shows ARB mode with the waveform name
5. Output ON → [ ] scope shows the clipped sine at 1 kHz, 2 Vpp
6. **Save Current** to library → [ ] `presets/arb/clipsine.csv` exists
7. Save a channel preset while ARB selected → reload it later
   - [ ] Preset restores ARB + `clipsine` selection (arb must already be in instrument memory — preset load selects, does not re-upload)
8. **Max-length probe** (fills in the unknown): make a CSV with 16384 rows
   (`python -c "print('value'); [print(__import__('math').sin(6.283*i/16384)) for i in range(16384)]" > big.csv`)
   - [ ] Uploads OK → try larger by editing `ARB_MAX_POINTS` in instruments.py; note where the box errors/truncates

---

## L. Waveform editor — compose + draw (PR: sg-arb-editor, EasyWaveX-style)

1. CH1 → waveform **ARB** → **Waveform Editor…** opens the editor
2. **Compose via sidebar (typed coordinates)** — build the worked example:
   - Point 0 = (0, 0); double-click cells to type exact X/Y
   - Add a point, set it (2, 0.25), segment-to-next of point 0 = **LINE**
   - Add a point (3, 0.25), segment-to-next of the (2,0.25) point = **HOLD**
   - [ ] Canvas shows a ramp 0→0.25 then a flat line at 0.25
3. **Draw on canvas** — click empty space to add a point; **drag a dot** to move it; **right-click a dot** to delete
   - [ ] Dragging updates the X/Y in the sidebar live; readout shows coords
4. **Segment types** — select a row, change its **To-next** type to SINE, set Cycles/Amplitude → [ ] canvas shows the sine riding that interval
5. **Undo/redo** — **Ctrl-Z** reverts the last add/move/type change; **Ctrl-Y** reapplies
6. **View** — **Fit All**, **Zoom +/-** (zoom in far enough to grab a single point), **Periods: 2** shows the repeating output, **X = time** flips the axis labels to ms (using CH1 Frequency)
7. **Save to Library** as `bench_edit` → [ ] `presets/arb/bench_edit.csv` **and** `bench_edit.recipe.json` exist
8. Close + reopen the editor (or **Load** `bench_edit`) → [ ] the **segment list repopulates** (re-editable, not just a flat curve)
9. **Upload && Select on CH1** at 1 kHz / 2 Vpp → channel panel shows ARB + name; on the **scope** the output period = 1 ms, ~2 Vpp, shape matches the editor (use Periods=2 as the expected repeating view)
10. **Import CSV** (a value-column file) → [ ] becomes an editable LINE-anchored approximation you can tweak
11. Save a **channel preset** referencing `bench_edit`, reload → [ ] select-only loads the named arb

---

**Pass =** every box ticked. Anything off: note section letter + what the applied readout / front panel / scope showed.
