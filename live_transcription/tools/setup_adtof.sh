#!/usr/bin/env bash
# Optional, isolated offline research dependency. Never used by the native runtime.
set -euo pipefail
module_dir="$(cd "$(dirname "$0")/.." && pwd)"
adtof_dir="$module_dir/build/ADTOF"
adtof_env="$module_dir/.venv-adtof"
mkdir -p "$module_dir/build"
if [ ! -d "$adtof_dir/.git" ]; then
  git clone https://github.com/MZehren/ADTOF.git "$adtof_dir"
fi
if [ -n "$(git -C "$adtof_dir" status --porcelain)" ]; then
  echo 'ADTOF checkout has local changes; preserve them before setup.' >&2
  exit 1
fi
git -C "$adtof_dir" checkout --detach b3968fb332f69b65ee07c089fc62f436503755db
if [ ! -d "$adtof_env" ]; then uv venv --python 3.10 "$adtof_env"; fi
# Relative constraint paths avoid uv interpreting spaces in an absolute -c path.
(
  cd "$module_dir"
  uv pip install --python "$adtof_env/bin/python" -r config/adtof-requirements.txt -c config/adtof-constraints.txt
)
# madmom's setup imports numpy/Cython, so install these before its build.
uv pip install --python "$adtof_env/bin/python" --no-deps --no-build-isolation 'madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae'
uv pip install --python "$adtof_env/bin/python" --no-deps --no-build-isolation -e "$adtof_dir"
"$adtof_env/bin/python" -c 'import imageio_ffmpeg, pathlib, sys; p=pathlib.Path(sys.executable).parent/"ffmpeg"; p.exists() or p.symlink_to(imageio_ffmpeg.get_ffmpeg_exe())'
uv pip freeze --python "$adtof_env/bin/python" > "$module_dir/build/adtof-resolved.txt"
"$adtof_env/bin/python" -c 'from adtof.model.model import Model; m,h=Model.modelFactory(modelName="Frame_RNN",scenario="adtofAll",fold=0); assert m.weightLoadedFlag; print("Pretrained ADTOF checkpoint loaded")'
