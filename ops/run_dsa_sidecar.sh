#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${DSA_SIDECAR_REPO_URL:-https://github.com/ZhuLinsen/daily_stock_analysis.git}"
DSA_COMMIT="${DSA_SIDECAR_COMMIT:-a2f19f65dc881e693f018eb08b889132eeeab659}"
BASE_DIR="${DSA_SIDECAR_BASE_DIR:-/var/lib/xauex/dsa-sidecar}"
REPO_DIR="${DSA_SIDECAR_REPO_DIR:-$BASE_DIR/daily_stock_analysis}"
VENV_DIR="${DSA_SIDECAR_VENV_DIR:-$BASE_DIR/.venv}"
ENV_FILE="${DSA_SIDECAR_ENV_FILE:-/etc/xauex/dsa-sidecar.env}"
HOST="${DSA_SIDECAR_HOST:-127.0.0.1}"
PORT="${DSA_SIDECAR_PORT:-18090}"
INSTALL_DEPS="${DSA_SIDECAR_INSTALL_DEPS:-auto}"
RUNTIME="${DSA_SIDECAR_RUNTIME:-auto}"
PYTHON_BIN="${DSA_SIDECAR_PYTHON:-}"
CONTAINER_NAME="${DSA_SIDECAR_CONTAINER_NAME:-xauex-dsa-sidecar}"
DOCKER_IMAGE="${DSA_SIDECAR_DOCKER_IMAGE:-xauex/dsa-sidecar:${DSA_COMMIT:0:12}}"
DOCKER_BUILD="${DSA_SIDECAR_DOCKER_BUILD:-auto}"
BUILD_CONTEXT="${DSA_SIDECAR_BUILD_CONTEXT:-$BASE_DIR/docker-build-context}"
DEFAULT_REQUIREMENT_CONSTRAINTS=$'numpy<2.0\npandas<3.0'
REQUIREMENT_CONSTRAINTS="${DSA_SIDECAR_REQUIREMENT_CONSTRAINTS-$DEFAULT_REQUIREMENT_CONSTRAINTS}"

reject_credentialed_github_url() {
  local label="$1"
  local value="$2"

  if [[ "$value" =~ https?://[^[:space:]/]+@github\.com([/:]|$) || "$value" =~ github_pat_ || "$value" =~ gh[pousr]_ ]]; then
    echo "$label contains a credential-bearing GitHub value; use SSH deploy keys, GIT_ASKPASS, or BuildKit secrets instead" >&2
    exit 1
  fi
}

reject_credentialed_github_urls_in_file() {
  local label="$1"
  local file="$2"

  [[ -f "$file" ]] || return 0
  if grep -Eq 'https?://[^[:space:]/]+@github\.com([/:]|$)|github_pat_|gh[pousr]_' "$file"; then
    echo "$label contains a credential-bearing GitHub value; refusing to run to avoid leaking it to service logs" >&2
    exit 1
  fi
}

ensure_repo() {
  reject_credentialed_github_url "DSA_SIDECAR_REPO_URL" "$REPO_URL"
  mkdir -p "$BASE_DIR"

  if [[ ! -d "$REPO_DIR/.git" ]]; then
    rm -rf "$REPO_DIR"
    git clone "$REPO_URL" "$REPO_DIR"
  fi

  git -C "$REPO_DIR" fetch --depth=1 origin "$DSA_COMMIT"
  git -C "$REPO_DIR" checkout --detach "$DSA_COMMIT"
}

python_is_compatible() {
  local python_cmd="$1"
  "$python_cmd" - <<'PY'
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)
PY
}

select_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    if python_is_compatible "$PYTHON_BIN"; then
      printf '%s\n' "$PYTHON_BIN"
      return 0
    fi
    echo "DSA_SIDECAR_PYTHON=$PYTHON_BIN is not compatible; DSA requires Python >=3.10,<3.13" >&2
    return 1
  fi

  local candidate
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

run_venv_sidecar() {
  local python_cmd="$1"
  ensure_repo

  if [[ ! -d "$VENV_DIR" ]]; then
    "$python_cmd" -m venv "$VENV_DIR"
    INSTALL_DEPS=1
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip
  if [[ "$INSTALL_DEPS" == "1" || "$INSTALL_DEPS" == "true" || "$INSTALL_DEPS" == "yes" || "$INSTALL_DEPS" == "auto" ]]; then
    reject_credentialed_github_urls_in_file "$REPO_DIR/requirements.txt" "$REPO_DIR/requirements.txt"
    python -m pip install -r "$REPO_DIR/requirements.txt"
  fi

  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  export WEBUI_HOST="$HOST"
  export API_PORT="$PORT"
  cd "$REPO_DIR"
  exec python main.py --serve-only --host "$HOST" --port "$PORT"
}

docker_build_needed() {
  if [[ "$DOCKER_BUILD" == "1" || "$DOCKER_BUILD" == "true" || "$DOCKER_BUILD" == "yes" || "$DOCKER_BUILD" == "always" ]]; then
    return 0
  fi
  if [[ "$DOCKER_BUILD" == "0" || "$DOCKER_BUILD" == "false" || "$DOCKER_BUILD" == "no" || "$DOCKER_BUILD" == "never" ]]; then
    return 1
  fi
  ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1
}

prepare_docker_build_context() {
  rm -rf "$BUILD_CONTEXT"
  mkdir -p "$BUILD_CONTEXT"
  (
    cd "$REPO_DIR"
    tar --exclude=.git -cf - .
  ) | (
    cd "$BUILD_CONTEXT"
    tar -xf -
  )

  if [[ -n "$REQUIREMENT_CONSTRAINTS" ]]; then
    {
      printf '\n# XAUEX sidecar build constraints for compatibility with older x86_64 hosts.\n'
      printf '%s\n' "$REQUIREMENT_CONSTRAINTS"
    } >> "$BUILD_CONTEXT/requirements.txt"
  fi

  reject_credentialed_github_urls_in_file "$BUILD_CONTEXT/requirements.txt" "$BUILD_CONTEXT/requirements.txt"
  printf '%s\n' "$BUILD_CONTEXT"
}

run_docker_sidecar() {
  ensure_repo
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker runtime requested but docker is not installed" >&2
    exit 1
  fi

  mkdir -p "$BASE_DIR/data" "$BASE_DIR/logs" "$BASE_DIR/reports" "$BASE_DIR/longbridge_tokens"
  if docker_build_needed; then
    build_context="$(prepare_docker_build_context)"
    docker build -f "$build_context/docker/Dockerfile" -t "$DOCKER_IMAGE" "$build_context"
  fi

  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  env_file_args=()
  if [[ -f "$ENV_FILE" ]]; then
    env_file_args=(--env-file "$ENV_FILE")
  fi

  exec docker run --rm \
    --name "$CONTAINER_NAME" \
    --env WEBUI_HOST=0.0.0.0 \
    --env API_PORT="$PORT" \
    --env TZ=Asia/Shanghai \
    "${env_file_args[@]}" \
    --publish "$HOST:$PORT:$PORT" \
    --volume "$BASE_DIR/data:/app/data" \
    --volume "$BASE_DIR/logs:/app/logs" \
    --volume "$BASE_DIR/reports:/app/reports" \
    --volume "$BASE_DIR/longbridge_tokens:/home/dsa/.longbridge" \
    "$DOCKER_IMAGE" \
    python main.py --serve-only --host 0.0.0.0 --port "$PORT"
}

case "$RUNTIME" in
  venv)
    selected_python="$(select_python)"
    run_venv_sidecar "$selected_python"
    ;;
  docker)
    run_docker_sidecar
    ;;
  auto)
    if selected_python="$(select_python 2>/dev/null)"; then
      run_venv_sidecar "$selected_python"
    elif command -v docker >/dev/null 2>&1; then
      echo "No compatible Python >=3.10,<3.13 found; using Docker runtime for DSA sidecar" >&2
      run_docker_sidecar
    else
      echo "No compatible Python >=3.10,<3.13 found and docker is unavailable" >&2
      exit 1
    fi
    ;;
  *)
    echo "Unsupported DSA_SIDECAR_RUNTIME=$RUNTIME; expected auto, venv, or docker" >&2
    exit 1
    ;;
esac
