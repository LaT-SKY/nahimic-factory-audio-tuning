# nahimic-factory-audio-tuning

Tools for extracting and translating A-Volute/Nahimic factory audio data into EasyEffects presets.

The scripts were developed against a MECHREVO JIAOLONG with a Conexant SN6140 codec and EasyEffects 8.2.x. They are not universal presets: device files, FIR variants, plugin versions, and output paths must be checked on the target machine.

## Requirements

- Python 3.10+
- NumPy for the DSP and FIR tools
- EasyEffects data directory (Flatpak defaults to `~/.var/app/com.github.wwmm.easyeffects/data/easyeffects`)
- `objdump` for the DLL inspection tools

The generator scripts read archive data from `data/` relative to this repository. Override local paths when needed:

```bash
export EASYEFFECTS_DATA="$HOME/.var/app/com.github.wwmm.easyeffects/data/easyeffects"
export NAHIMIC_ARCHIVE="$PWD"
export NAHIMIC_DEVICES="$PWD/data/Devices"
export NAHIMIC_DLL="/path/to/NahimicAPO4.dll"
```

The repository contains tools only. The accompanying research notes and driver documentation are kept separately from this source repository.

## Safety and status

Do not overwrite an active EasyEffects installation while its Convolver page is open. `update_irs.py` refuses to modify the IR directory while EasyEffects is running unless `--force` is supplied.

The HRTF-to-EasyEffects experiment is retained as historical tooling and is not a working surround renderer. VoiceBoost, DRC, and Attenuator are not claimed to be reproduced by these scripts.
