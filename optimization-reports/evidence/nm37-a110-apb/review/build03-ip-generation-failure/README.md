# Historical build03 PLL IP-generation failure

This bundle documents a superseded build's reproducibility failure. It is not a functional pass, routed timing pass, or result of the final build04 hardware implementation. No build03 unit/formal/system-regression pass records are included.

Vivado stopped while sourcing the generated ClockWizard Tcl. DUT 110 MHz reported 195.800 ps output jitter; APB 55 MHz reported 214.859 ps, exceeding the mistakenly propagated 200 ps Tcl budget. No synthesis or routed checkpoint existed when this bundle was captured. The 200 ps value was a clock-diplomacy software budget, not an HBM hard-IP jitter limit. The board's explicit APB sink budget was 230 ps. Build04 restores `FixedClockBroadcast()` so that budget is propagated; its HBM APB sink already declares the required frequency.

The original XCI explicitly records `JITTER_OPTIONS=UI` and `CLKIN1_UI_JITTER=0.010`. With a 100 MHz input, the active input assumption is 0.010 UI = 100 ps; the separate stored `CLKIN1_JITTER_PS=50.0` value was not the selected mode. This is a tool input assumption, not a measurement of the NM37 oscillator. HBM REF_CLK jitter specifications concern the separate reference input and were not changed here.

`source-inventory.json` binds each byte-for-byte copied original to its absolute source path and SHA256. It also records the relevant failed/corrected Git source object identities and selected lines, without copying an obsolete functional result. `failure-excerpt.txt` is a numbered convenience extract; `implementation.log` is the unmodified original log. These files form a small diagnostic subset, not a self-contained build backup.
