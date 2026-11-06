#!/bin/sh
# Install or upgrade maelstrom from a release bundle.
#
#   curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh
#
# Usage: install.sh [--tag TAG | --bundle FILE | --source DIR] [--root DIR] [--projects-dir DIR] [--no-setup]
#
#   (none)        download the latest GitHub Release
#   --tag TAG     download the GitHub Release TAG, such as an older one to roll back to
#   --bundle FILE install a local maelstrom.tar.gz
#   --source DIR  pull the git checkout DIR, build its bundle, and install that
#   --root DIR    the install root (default: ~/.maelstrom/install)
#   --projects-dir DIR
#                 the directory that holds the projects (default: the
#                 projects_dir: key, else a question, else ~/Projects)
#   --no-setup    run neither `mael self check-host` nor `mael self setup`; the
#                 projects directory is still resolved and written
#
# The script needs curl and tar. It installs uv when uv is missing, and uv
# fetches the Python the venv uses. Before it downloads or builds, it asks for
# the projects directory when projects_dir: is unset, and writes the key. It places each component version once,
# builds the release's venv, and moves `current` with one rename as its last
# step, so a failure leaves the release that runs as it was. On a first install
# it then runs `mael self check-host`, which offers the host tools and the
# sign-ins. Last, it runs the new release's `mael self setup`, which brings the
# host to the state the release wants. See docs/dev/host-services.md.
#
# The body is one function, called on the last line, so that `curl | sh` runs
# nothing until the whole script has arrived.
set -eu

# The components of a host bundle. COMPONENTS in
# lib/domain/src/mael_domain/release_bundle/layout.py holds the same list.
COMPONENTS="cli orchestrator agent-daemon web common agent domain python-deps"
MANIFEST="manifest.toml"
TARBALL="maelstrom.tar.gz"
# The Python of each release venv. uv downloads it when the host has none.
PYTHON="${MAEL_INSTALL_PYTHON:-3.13}"
# Where the stable channel's bundles are published.
RELEASES="${MAEL_RELEASE_URL:-https://github.com/sminnee/maelstrom/releases}"
CONFIG="$HOME/.maelstrom/config.yaml"
DEFAULT_PROJECTS="~/Projects"

say() { printf '%s\n' "$*"; }
die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

# A tag or a version is one path segment under the install root.
is_segment() {
  case "$1" in
  "" | [!A-Za-z0-9]* | *[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}

# manifest_key FILE KEY: a top-level string value of the manifest.
manifest_key() {
  awk -v want="$2" '
    /^\[/ { exit }
    $1 == want {
      v = $0; sub(/^[^=]*=[ \t]*/, "", v); gsub(/^"|"$/, "", v); print v; exit
    }' "$1"
}

# component_version FILE NAME: the version of one component, or nothing.
component_version() {
  awk -v want="[components.$2]" '
    $0 == want { on = 1; next }
    /^\[/ { on = 0 }
    on && $1 == "version" {
      v = $0; sub(/^[^=]*=[ \t]*/, "", v); gsub(/^"|"$/, "", v); print v; exit
    }' "$1"
}

# versions FILE: one `name=version` word per component, for comparing releases.
versions() {
  for name in $COMPONENTS; do
    printf '%s=%s ' "$name" "$(component_version "$1" "$name")"
  done
}

# check_manifest FILE: refuse a manifest this script cannot install.
check_manifest() {
  [ -f "$1" ] || die "the bundle has no $MANIFEST"
  [ "$(manifest_key "$1" format)" = 1 ] || die "$1: manifest format is not 1"
  tag="$(manifest_key "$1" tag)"
  is_segment "$tag" || die "$1: '$tag' is not a tag or a version"
  for name in $COMPONENTS; do
    version="$(component_version "$1" "$name")"
    [ -n "$version" ] || die "the bundle has no $name component"
    is_segment "$version" || die "$1: '$version' is not a tag or a version"
  done
}

# resolve DIR: the physical path of a directory.
resolve() { (cd "$1" && pwd -P); }

# copy_component SOURCE PARENT VERSION: copy one version, unless it is there.
copy_component() {
  [ -d "$2/$3" ] && return 0
  mkdir -p "$2"
  staging="$(mktemp -d "$2/.$3.tmp-XXXXXX")"
  cp -R "$1/." "$staging/" || {
    rm -rf "$staging"
    die "could not copy $1"
  }
  mv "$staging" "$2/$3"
}

# install_wheels VENV COMPONENTS MANIFEST: every component's wheels, unresolved.
install_wheels() {
  venv="$1"
  components="$2"
  manifest="$3"
  set --
  for name in $COMPONENTS; do
    for wheel in "$components/$name/$(component_version "$manifest" "$name")"/*.whl; do
      if [ -f "$wheel" ]; then set -- "$@" "$wheel"; fi
    done
  done
  uv pip install --quiet --python "$venv/bin/python" --no-deps "$@"
}

# build_venv VENV REQUIREMENTS COMPONENTS MANIFEST: the release's venv.
build_venv() {
  uv venv --quiet --python "$PYTHON" "$1" &&
    uv pip install --quiet --python "$1/bin/python" --require-hashes -r "$2" &&
    install_wheels "$1" "$3" "$4"
}

# point_current ROOT TAG: move `current` to the release with one rename.
point_current() {
  staging="$1/.current.tmp-$$"
  rm -f "$staging"
  ln -s "releases/$2" "$staging"
  if [ "$(uname -s)" = Darwin ]; then
    mv -h -f "$staging" "$1/current"
  else
    mv -T -f "$staging" "$1/current"
  fi
}

# install_tree BUNDLE ROOT: install an unpacked bundle; set $installed_tag
# and $changed, the comma-separated components that differ from `current`.
install_tree() {
  bundle="$1"
  root="$2"
  manifest="$bundle/$MANIFEST"
  check_manifest "$manifest"
  tag="$(manifest_key "$manifest" tag)"
  components="$root/components"
  for name in $COMPONENTS; do
    copy_component "$bundle/components/$name" "$components/$name" \
      "$(component_version "$manifest" "$name")"
  done

  # A copy: the switch below moves what `current/` names.
  before=""
  if [ -f "$root/current/$MANIFEST" ]; then
    before="$bundle/.before-$MANIFEST"
    cp "$root/current/$MANIFEST" "$before"
  fi
  release="$root/releases/$tag"
  if [ -f "$release/$MANIFEST" ]; then
    if [ "$(versions "$release/$MANIFEST")" != "$(versions "$manifest")" ]; then
      # A tag installed with other versions is a dirty rebuild under one tag.
      if [ -n "$before" ] && [ "$(resolve "$root/current")" = "$(resolve "$release")" ]; then
        die "release $tag runs now with other component versions; build the bundle under a new tag"
      fi
      rm -rf "$release"
    fi
  fi
  if [ ! -f "$release/$MANIFEST" ]; then
    mkdir -p "$release"
    cp "$manifest" "$release/$MANIFEST"
  fi

  venv="$release/venv"
  if [ ! -e "$venv/bin/python" ]; then
    say "Building the venv of $tag..."
    if ! build_venv "$venv" \
      "$components/python-deps/$(component_version "$manifest" python-deps)/requirements.txt" \
      "$components" "$manifest"; then
      # A half-built venv has a python, so the next try would take it for a
      # finished one.
      rm -rf "$venv"
      die "the venv of $tag did not build; the release that runs is unchanged"
    fi
  fi
  point_current "$root" "$tag"

  changed=""
  unchanged=""
  for name in $COMPONENTS; do
    old=""
    [ -n "$before" ] && old="$(component_version "$before" "$name")"
    if [ "$old" = "$(component_version "$manifest" "$name")" ]; then
      unchanged="${unchanged:+$unchanged, }$name"
    else
      changed="${changed:+$changed,}$name"
    fi
  done
  installed_tag="$tag"
  if [ -z "$changed" ]; then
    say "$tag is installed; no component changed."
  else
    say "Installed $tag. Changed: $(printf '%s' "$changed" | sed 's/,/, /g')."
    [ -n "$unchanged" ] && say "Unchanged: $unchanged."
  fi
  return 0
}

# install_bundle TARBALL ROOT WORK: unpack into WORK, then install_tree.
install_bundle() {
  members="$(tar -tzf "$1")" || die "$1 is not a bundle"
  # Every member is under maelstrom/, and none climbs out with `..`.
  if printf '%s\n' "$members" | grep -Ev '^maelstrom(/|$)' | grep -q . ||
    printf '%s\n' "$members" | grep -Eq '(^|/)\.\.(/|$)'; then
    die "$1 holds a path outside maelstrom/"
  fi
  mkdir "$3/unpacked"
  tar -xzf "$1" -C "$3/unpacked" || die "$1 is not a bundle"
  install_tree "$3/unpacked/maelstrom" "$2"
}

# ensure_uv: install uv when it is not on PATH.
ensure_uv() {
  command -v uv >/dev/null && return 0
  hint="Install uv with \`curl -LsSf https://astral.sh/uv/install.sh | sh\`, then run this again."
  say "uv is not on PATH. Installing it..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "$hint"
  # Where uv's installer puts it.
  PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}:$PATH"
  export PATH
  command -v uv >/dev/null || die "$hint"
}

# has_tty: whether a user can answer a question. Under `curl | sh`, stdin is
# the script, so the questions read /dev/tty.
has_tty() { (exec </dev/tty) 2>/dev/null; }

# --- The projects directory. install-main.sh holds a copy of these functions. ---

# config_value KEY: the value of a top-level key of the config. It reads the
# subset yaml_scalar writes: a plain value with an optional `# comment`, or a
# quoted one with \" and \\ escapes. A plain ~ is YAML null, so it reads as unset.
config_value() {
  [ -f "$CONFIG" ] || return 0
  sed -n "s/^$1:[[:space:]]*//p" "$CONFIG" | head -n 1 | sed \
    -e '/^"/{s/^"\(.*\)".*$/\1/;s/\\"/"/g;s/\\\\/\\/g;b' -e '}' \
    -e "/^'/{s/^'\\(.*\\)'.*\$/\\1/;b" -e '}' \
    -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e 's/^~$//'
}

# yaml_scalar VALUE: VALUE as one YAML scalar, as setup's _yaml_scalar writes
# it. A bare ~ is quoted, because plain ~ is YAML null.
yaml_scalar() {
  case "$1" in
  "" | "~" | *[!A-Za-z0-9_./~-]*)
    printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
    ;;
  *) printf '%s' "$1" ;;
  esac
}

# set_config_key KEY SCALAR: replace the KEY: line of the config, or append one.
set_config_key() {
  mkdir -p "$(dirname "$CONFIG")"
  if [ -f "$CONFIG" ] && grep -q "^$1:" "$CONFIG"; then
    tmp="$(mktemp "$CONFIG.tmp-XXXXXX")"
    KEY="$1:" LINE="$1: $2" awk '
      !done && index($0, ENVIRON["KEY"]) == 1 { print ENVIRON["LINE"]; done = 1; next }
      { print }' "$CONFIG" >"$tmp"
    # cat, not mv, so the file keeps its mode.
    cat "$tmp" >"$CONFIG"
    rm -f "$tmp"
  else
    # A new config is private, as it holds API keys.
    (
      umask 077
      if [ -s "$CONFIG" ] && [ "$(tail -c 1 "$CONFIG")" != "" ]; then
        printf '\n' >>"$CONFIG"
      fi
      printf '%s: %s\n' "$1" "$2" >>"$CONFIG"
    )
  fi
}

# resolve_projects_dir GIVEN: set $projects, the directory as the user spelled
# it, and $projects_path, the directory with a leading ~ expanded. Write the
# projects_dir: key when it changes, and make the directory.
resolve_projects_dir() {
  stored="$(config_value projects_dir)"
  projects="${1:-$stored}"
  if [ -z "$projects" ]; then
    if has_tty; then
      printf 'Where do your projects live? [%s] ' "$DEFAULT_PROJECTS" >/dev/tty
      answer=""
      read -r answer </dev/tty || true
      projects="${answer:-$DEFAULT_PROJECTS}"
    else
      projects="$DEFAULT_PROJECTS"
      say "projects_dir: $projects. To change it, edit $CONFIG."
    fi
  fi
  case "$projects" in
  "~") projects_path="$HOME" ;;
  "~/"*) projects_path="$HOME/${projects#"~/"}" ;;
  /*) projects_path="$projects" ;;
  *) die "projects_dir '$projects' is relative. Give a path that starts with / or ~." ;;
  esac
  [ "$projects" = "$stored" ] || set_config_key projects_dir "$(yaml_scalar "$projects")"
  mkdir -p "$projects_path"
}

# --- End of the copy. ---

current_tag() {
  if [ -f "$1/current/$MANIFEST" ]; then
    manifest_key "$1/current/$MANIFEST" tag
  fi
}

main() {
  tag=""
  bundle=""
  source=""
  root="${HOME}/.maelstrom/install"
  projects_given=""
  setup=1
  pull=1
  while [ $# -gt 0 ]; do
    case "$1" in
    --tag) tag="$2"; shift 2 ;;
    --bundle) bundle="$2"; shift 2 ;;
    --source) source="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    --projects-dir) projects_given="$2"; shift 2 ;;
    --no-setup) setup=0; shift ;;
    # Internal: the re-run of a pulled checkout's own installer.
    --no-pull) pull=0; shift ;;
    -h | --help)
      say "Usage: install.sh [--tag TAG | --bundle FILE | --source DIR] [--root DIR] [--projects-dir DIR] [--no-setup]"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
    esac
  done
  given=0
  for value in "$tag" "$bundle" "$source"; do
    [ -z "$value" ] || given=$((given + 1))
  done
  [ "$given" -le 1 ] || die "give one of --tag, --bundle and --source"
  ensure_uv
  # Setup then finds projects_dir: set and does not ask.
  resolve_projects_dir "$projects_given"
  mkdir -p "$root"
  root="$(resolve "$root")"
  # A first install offers the host tools before setup.
  first=0
  [ -f "$root/current/$MANIFEST" ] || first=1

  if [ -n "$source" ]; then
    source="$(resolve "$source")"
    if [ "$pull" = 1 ]; then
      say "Updating $source..."
      git -C "$source" pull --ff-only
      # The pull can change this script, so the checkout's own copy goes on.
      set -- --source "$source" --root "$root" --no-pull
      [ "$setup" = 1 ] || set -- "$@" --no-setup
      exec sh "$source/bin/install.sh" "$@"
    fi
    say "Installing dependencies in $source..."
    # The release's own setup migrates the notebook, so this migrates nothing.
    (cd "$source" && env -u MAEL_NOTEBOOK_ROOT bin/install)
    commit="$(git -C "$source" rev-parse HEAD)"
    tag="main-$(printf '%s' "$commit" | cut -c1-12)"
  fi

  work="$(mktemp -d "$root/.work-XXXXXX")"
  trap 'rm -rf "$work"' EXIT
  trap 'exit 1' INT TERM HUP
  changed=""
  if [ -n "$source" ] && [ "$tag" = "$(current_tag "$root")" ]; then
    say "$tag is installed already."
  else
    if [ -n "$source" ]; then
      say "Building $tag..."
      bundle="$work/$TARBALL"
      (cd "$source" && bin/build-bundle --tag "$tag" --out "$bundle")
    elif [ -z "$bundle" ]; then
      if [ -n "$tag" ]; then
        url="$RELEASES/download/$tag/$TARBALL"
      else
        url="$RELEASES/latest/download/$TARBALL"
      fi
      say "Downloading $url..."
      bundle="$work/$TARBALL"
      curl -LsSf -o "$bundle" "$url" || die "could not download $url"
    fi
    install_bundle "$bundle" "$root" "$work"
  fi
  # exec skips the EXIT trap.
  rm -rf "$work"

  # Setup deletes this record once the host services have restarted, so a
  # setup that stops early leaves their restart to the next one.
  if [ -n "$changed" ]; then
    pending="$root/pending-changed"
    if [ -f "$pending" ]; then
      printf '%s,%s\n' "$(cat "$pending")" "$changed" >"$pending.tmp"
    else
      printf '%s\n' "$changed" >"$pending.tmp"
    fi
    mv "$pending.tmp" "$pending"
  fi
  [ "$setup" = 1 ] || return 0
  mael="$root/current/venv/bin/mael"
  # The production shim sets it; setup runs before the shim exists.
  export MAEL_PRODUCTION=1
  if [ "$first" = 1 ]; then
    # Setup writes orchestrator_url: from dev_host:, which the check sets. Its
    # exit code is ignored: the host services start without gh or codex.
    set -- self check-host
    # A source install builds with node and pnpm.
    [ -z "$source" ] || set -- "$@" --dev
    if has_tty; then
      "$mael" "$@" </dev/tty || true
    else
      "$mael" "$@" || true
    fi
  fi
  set -- self setup --changed "$changed"
  [ -z "$source" ] || set -- "$@" --source "$source"
  if has_tty; then
    exec "$mael" "$@" </dev/tty
  fi
  exec "$mael" "$@"
}

main "$@"
