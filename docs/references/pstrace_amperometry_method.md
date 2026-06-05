# PSTrace amperometry (Chronoamperometry) method reference

Decoded `MethodForMeasurement` from a genuine PSTrace-native `.pssession`
(device PS4A22Z003341, PSTrace 5.12, fw 1.7). Use this to fix
`build_method_string` so PSTrace reads our metadata instead of showing
template defaults.

## Key finding
The DC potential is emitted as **`E=`** (in the `#Time method parameters`
block), NOT `E_DC=`. That's why PSTrace shows its 0.5 V default for our
exports. `T_RUN` / `T_INTERVAL` ARE the correct keys (PSTrace reads them).
Equilibration is `T_EQUIL` (not `T_EQ`).

## The full field set PSTrace writes (amperometry, METHOD_ID=ad TECHNIQUE=7)
METHOD_VERSION=1
METHOD_ID=ad
TECHNIQUE=7
NOTES=
#Pretreatment and standby
E_COND, T_COND, E_DEP, T_DEP, T_EQUIL, E_STBY, T_STBY, USE_STBY
#Peaks or levels
PEAK_HEIGHT_MIN, PEAK_WIDTH_MIN, PEAK_OVERLAP, PEAK_WINDOW, SMOOTH_LEVEL
#Current ranges
IRANGE_MIN_F, IRANGE_MAX_F, IRANGE_START_F, IRANGE_MIN, IRANGE_MAX, IRANGE_START
#Potential ranges
E_RANGE_MIN_F, E_RANGE_MAX_F, E_RANGE_F, E_RANGE, E_RANGE_MIN, E_RANGE_MAX
#Auxiliary
EXTRA_VALUES_MSK, EXTRA_VALUE_SE2_VS_X, USE_STIRRER, E_BIPOT
#Mux Settings
MUX_METHOD, USE_MUX_CH, MUX_SETTINGS, MUX_NO_TIME_RESET
#Plot view ... #Corrosion analysis ... #Polypotentiostat ...
#Reference electrode ... #Bipot ... #IR Drop Compensation ... #Options ...
#Triggering ... #Method overrides
#Time method parameters
E=<DC potential>          <-- the one we get wrong (we emit E_DC)
EOCP=NaN
T_INTERVAL=<interval>
T_RUN=<run time>
VS_PREV_E, USE_CHARGE_LIMIT_MAX/MIN, CHARGE_LIMIT_MAX/MIN, SIGNAL, REACTION

## Follow-up fix
Map params -> PSTrace keys per technique in build_method_string (at
minimum CA: e_dc -> E, t_eq -> T_EQUIL). Re-open an export in PSTrace to
confirm E reads back. Check the CMU.49.011 analysis pipeline doesn't
depend on the old E_DC key before changing it.
