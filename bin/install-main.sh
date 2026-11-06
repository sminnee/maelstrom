#!/bin/sh
# Install or upgrade maelstrom from the main branch, on a host with no release.
#
#   curl -LsSf https://raw.githubusercontent.com/sminnee/maelstrom/main/bin/install-main.sh | sh
#
# Usage: install-main.sh [--projects-dir DIR] [--no-setup]
#
#   --projects-dir DIR  the directory that holds the projects (default: the
#                       projects_dir: key, else a question, else ~/Projects)
#   --no-setup          passed on to install.sh
#
# The script clones maelstrom as a bare repo into <projects_dir>/maelstrom/,
# checks main out into _main, and writes `update_channel: main` to
# ~/.maelstrom/config.yaml. Then it runs _main's own `bin/install.sh --source`,
# which builds and installs a main-<sha12> release. When the checkout is there
# already, the script skips the clone, so a second run upgrades. install.sh
# installs uv when it is missing.
#
# The body is one function, called on the last line, so that `curl | sh` runs
# nothing until the whole script has arrived.
set -eu

REPO="${MAEL_REPO_URL:-https://github.com/sminnee/maelstrom.git}"
CONFIG="$HOME/.maelstrom/config.yaml"
DEFAULT_PROJECTS="~/Projects"

say() { printf '%s\n' "$*"; }
die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

# has_tty: whether a user can answer a question. Under `curl | sh`, stdin is
# the script, so the questions read /dev/tty.
has_tty() { (exec </dev/tty) 2>/dev/null; }

# --- The projects directory. install.sh holds a copy of these functions. ---

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

# lay_out PROJECT: clone maelstrom bare into PROJECT and check main out into
# _main, as `mael project add` does, without the alpha worktree.
lay_out() {
  git clone --bare -- "$REPO" "$1/.git" &&
    git -C "$1" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*' &&
    git -C "$1" fetch --quiet origin &&
    # Detach HEAD, so that main is free for _main.
    sha="$(git -C "$1" rev-parse --verify refs/heads/main)" &&
    git -C "$1" update-ref --no-deref HEAD "$sha" &&
    git -C "$1" worktree add "$1/_main" main &&
    git -C "$1" branch --set-upstream-to origin/main main &&
    touch "$1/.mael"
}

main() {
  given=""
  setup=1
  while [ $# -gt 0 ]; do
    case "$1" in
    --projects-dir)
      [ $# -ge 2 ] || die "--projects-dir needs a directory"
      given="$2"
      shift 2
      ;;
    --no-setup) setup=0; shift ;;
    -h | --help)
      say "Usage: install-main.sh [--projects-dir DIR] [--no-setup]"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
    esac
  done
  command -v git >/dev/null ||
    die "git is not on PATH. Install git (on macOS: xcode-select --install), then run this again."
  resolve_projects_dir "$given"

  project="$projects_path/maelstrom"
  if [ -e "$project" ]; then
    [ -e "$project/_main/.git" ] && [ -f "$project/.mael" ] ||
      die "$project exists and is not a maelstrom checkout. Move it, or give another --projects-dir."
    say "Using the checkout in $project."
  else
    say "Cloning $REPO into $project..."
    mkdir "$project"
    # A step that fails, or an interrupt, leaves no directory behind, so the next
    # run clones again.
    trap 'rm -rf "$project"; exit 1' INT TERM HUP
    if ! lay_out "$project"; then
      rm -rf "$project"
      die "could not lay out $project"
    fi
    trap - INT TERM HUP
  fi
  # After the layout, so that a failed clone leaves the channel as it was.
  set_config_key update_channel main

  set -- --source "$project/_main" --projects-dir "$projects"
  [ "$setup" = 1 ] || set -- "$@" --no-setup
  if has_tty; then
    exec sh "$project/_main/bin/install.sh" "$@" </dev/tty
  fi
  exec sh "$project/_main/bin/install.sh" "$@"
}

main "$@"
