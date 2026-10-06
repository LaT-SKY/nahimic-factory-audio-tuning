# nahimic-factory-audio-tuning

Tools for extracting and translating A-Volute/Nahimic factory audio data into EasyEffects presets.

The scripts were developed against a MECHREVO JIAOLONG with a Conexant SN6140 codec and
EasyEffects 8.2.x / 8.3.x. They are not universal presets: device files, FIR variants,
plugin versions, and output paths must be checked on the target machine.

## Requirements

- Python 3.10+
- NumPy for the DSP and FIR tools
- Matplotlib for `analyze_crosstalk.py --plot`
- `python-registry` for `dump_runtime_state.py`
- EasyEffects data directory (Flatpak defaults to `~/.var/app/com.github.wwmm.easyeffects/data/easyeffects`)
- `objdump` for the DLL inspection tools

## Configuration

The generator scripts read archive data from `data/` relative to this repository.
Override local paths when needed:

```bash
export EASYEFFECTS_DATA="$HOME/.var/app/com.github.wwmm.easyeffects/data/easyeffects"
export NAHIMIC_ARCHIVE="$PWD"
export NAHIMIC_DEVICES="$PWD/data/Devices"
export NAHIMIC_DLL="/path/to/NahimicAPO4.dll"
export NAHIMIC_WINDOWS_MOUNT="/mnt/windows"   # dump_runtime_state.py; auto-detected if unset
export NAHIMIC_WINDOWS_USER_DIR=""            # optional; found via Users/*/NTUSER.DAT
```

No script assumes a particular machine layout: the Windows mount point and the Windows
user directory are auto-detected, and no absolute path is hard-coded.

## Tools

| Group | Scripts |
|---|---|
| Preset generators | `build_scenario_presets.py`, `build_official_eq_presets.py`, `build_capture_presets.py`, `build_factory_preset.py`, `build_complete_preset.py`, `build_nahimic_music.py`, `build_headphone_presets.py`, `build_spatial_presets.py` |
| FIR / HRTF | `decode_nahimic.py`, `decode_hrtf.py`, `hrir_to_irs.py`, `hrir_to_sofa.py`, `add_bass_compressor.py`, `remove_limiter.py`, `update_irs.py` |
| Analysis | `analyze_crosstalk.py` (RACE transfer function; `--self-test` arbitrates the analytic model against a sample-by-sample simulation, `--plot` renders it) |
| Device / variant resolution | `read_smbios_oem.py` (matches the machine's SMBIOS Type 11 OEM string against the `<SMBIOS>` selector of each device profile) |
| Runtime state | `dump_runtime_state.py` (dumps every on-disk place where Nahimic could persist settings) |
| Validation | `validate_presets.py`, `validate_complete.py` |
| DLL inspection | `find_xrefs.py`, `disasm_window.py` |

## Safety and status

**Stopping EasyEffects.** `systemctl --user stop app-…@autostart.service` alone does *not* stop
the Flatpak app — Flatpak puts it in its own transient scope. Also stop
`app-flatpak-com.github.wwmm.easyeffects-<id>.scope`, or use `flatpak kill`.
`update_irs.py` refuses to touch the IR directory while EasyEffects is running unless `--force`
is given, because a burst of changes can crash it while the Convolver page is open.

**Impulse responses are loaded lazily.** EasyEffects only re-reads an `.irs` when `kernel-name`
changes, and on restart it restores the *runtime* state from `config/easyeffects/db/*.rc`
rather than re-reading the preset. Renaming an impulse response therefore requires updating both.

**Validation.** `validate_presets.py` checks key sets exactly (a misspelled key is silently
ignored by EasyEffects), enum labels, and value ranges, and it reports an error for any plugin
it has no rules for instead of passing it silently.

**Not claimed.** The HRTF-to-EasyEffects experiment is retained as historical tooling and is not
a working surround renderer. The same applies to `crosstalk_canceller`: it is a recursive
ambiophonic canceller (RACE) whose feedback filter is fitted to a KEMAR head, so it requires a
matching speaker geometry — on closely spaced laptop speakers it colours the mid channel instead
of cancelling anything. VoiceBoost, DRC, and Attenuator are not claimed to be reproduced.

The repository contains tools only. The accompanying research notes and driver documentation
are kept separately from this source repository.