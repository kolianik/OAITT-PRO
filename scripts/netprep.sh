#!/usr/bin/env bash
# netprep.sh — shared network preflight for the OAITT-PRO start/build scripts.
#
# SOURCE this file (do not exec); it mutates the parent shell's environment so the
# resolved proxy contract flows into `docker compose up`/`build`:
#   source "$(dirname "$0")/scripts/netprep.sh"
#
# Pipeline: discover system proxy -> probe DIRECT reachability (proxy bypassed) ->
#   * direct works            -> disable proxying            (OAITT_PROXY_MODE=direct)
#   * direct fails, proxy ok   -> loopback->host.docker.internal, else passthrough
#                                 (OAITT_PROXY_MODE=translated|passthrough)
#   * neither                  -> leave empty + warn         (OAITT_PROXY_MODE=none)
# Exports: HTTP_PROXY/HTTPS_PROXY (+lowercase), NO_PROXY (+lowercase), OAITT_PROXY_MODE.
#
# Set OAITT_NETPREP_NO_AUTORUN=1 to define the helpers without running the pipeline
# (used by tests/test_netprep_proxy.py).

netprep_log() { printf '[netprep] %s\n' "$*" >&2; }

# Translate a loopback proxy URL to a container-reachable host.docker.internal URL.
# Non-loopback URLs pass through unchanged. Port defaults to 80 when absent.
netprep_translate_loopback() {
    local url="$1"
    case "$url" in
        *127.0.0.1*|*localhost*|*::1*)
            local hostport port
            hostport="${url#*://}"     # strip scheme
            hostport="${hostport%%/*}"  # strip any path
            case "$hostport" in
                \[*\]:*) port="${hostport##*]:}" ;;   # [::1]:8080
                \[*\])   port="" ;;                    # [::1]
                *:*)     port="${hostport##*:}" ;;     # host:port
                *)       port="" ;;
            esac
            case "$port" in
                ''|*[!0-9]*) port=80 ;;
            esac
            printf 'http://host.docker.internal:%s\n' "$port"
            ;;
        *)
            printf '%s\n' "$url"
            ;;
    esac
}

# Canonical NO_PROXY: loopback + host-gateway + every internal service + DNS suffixes.
# Optional $1 is an extra bypass list (e.g. WinINET ProxyOverride) appended verbatim.
netprep_compute_no_proxy() {
    local extra="${1:-}"
    local base="localhost,127.0.0.1,::1,host.docker.internal,gateway-orchestrator,whisperx-service,gigaam-service,postgres,front-proxy,certbot,.local,.internal"
    if [ -n "$extra" ]; then
        printf '%s,%s\n' "$base" "$extra"
    else
        printf '%s\n' "$base"
    fi
}

# Raw TCP/TLS reach test that IGNORES proxy env (measures TRUE direct reachability).
netprep__reach() {
    local host="$1" port="${2:-443}"
    if command -v curl >/dev/null 2>&1; then
        curl --proxy '' --connect-timeout 3 -sS -o /dev/null -I "https://${host}" >/dev/null 2>&1
        return $?
    fi
    timeout 3 bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
}

# Direct internet works iff every critical download host is reachable with no proxy.
netprep_probe_direct() {
    local h ok=0 total=0
    for h in registry-1.docker.io pypi.org huggingface.co; do
        total=$((total + 1))
        if netprep__reach "$h" 443; then ok=$((ok + 1)); fi
    done
    [ "$ok" -eq "$total" ]
}

# Does the discovered proxy actually forward to the internet?
netprep_probe_proxy() {
    local proxy="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -x "$proxy" --connect-timeout 5 -sS -o /dev/null -I https://pypi.org >/dev/null 2>&1
        return $?
    fi
    return 0  # no curl: cannot CONNECT-probe; assume usable
}

# Discover a proxy URL from env, /etc/environment, then apt config. Echo it or return 1.
netprep_discover_proxy() {
    local v p=""
    for v in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy; do
        eval "p=\${$v:-}"
        if [ -n "$p" ]; then printf '%s\n' "$p"; return 0; fi
    done
    if [ -r /etc/environment ]; then
        p=$(grep -iE '^(HTTPS?_PROXY)=' /etc/environment 2>/dev/null | head -1 \
            | sed -E 's/^[^=]+=//; s/^"//; s/"$//')
        if [ -n "$p" ]; then printf '%s\n' "$p"; return 0; fi
    fi
    if ls /etc/apt/apt.conf /etc/apt/apt.conf.d/* >/dev/null 2>&1; then
        p=$(grep -RhiE 'Acquire::https?::Proxy' /etc/apt/apt.conf /etc/apt/apt.conf.d/ 2>/dev/null \
            | head -1 | sed -E 's/.*Proxy[^"]*"([^"]*)".*/\1/')
        if [ -n "$p" ]; then printf '%s\n' "$p"; return 0; fi
    fi
    return 1
}

netprep_main() {
    local mode="none" proxy="" resolved=""
    proxy="$(netprep_discover_proxy || true)"

    if netprep_probe_direct; then
        mode="direct"; resolved=""
        if [ -n "$proxy" ]; then
            netprep_log "Direct internet reachable; ignoring configured proxy ($proxy)."
        else
            netprep_log "Direct internet reachable; no proxy needed."
        fi
    elif [ -n "$proxy" ] && netprep_probe_proxy "$proxy"; then
        resolved="$(netprep_translate_loopback "$proxy")"
        case "$proxy" in
            *127.0.0.1*|*localhost*|*::1*) mode="translated" ;;
            *) mode="passthrough" ;;
        esac
        netprep_log "Direct blocked; using proxy ($mode): $resolved"
    else
        mode="none"; resolved=""
        netprep_log "WARNING: no direct internet and no working proxy. Builds/bootstrap will fail unless the cache is seeded (INSTALL.md §3.2 / GIGAAM_OFFLINE_MODE)."
    fi

    export HTTP_PROXY="$resolved" HTTPS_PROXY="$resolved"
    export http_proxy="$resolved" https_proxy="$resolved"
    local np; np="$(netprep_compute_no_proxy "${OAITT_PROXY_BYPASS:-}")"
    export NO_PROXY="$np" no_proxy="$np"
    export OAITT_PROXY_MODE="$mode"

    # Docker daemon image pulls do NOT use the shell proxy. Hint only (do not edit daemon config).
    if [ "$mode" != "direct" ] && command -v docker >/dev/null 2>&1; then
        local dproxy; dproxy="$(docker info --format '{{.HTTPProxy}}' 2>/dev/null || true)"
        if [ -z "$dproxy" ]; then
            netprep_log "Note: the Docker daemon has no proxy configured — base-image pulls may fail. Configure Docker Desktop Resources->Proxies, or on Linux a systemd drop-in (see INSTALL.md §6)."
        fi
    fi
    return 0
}

if [ -z "${OAITT_NETPREP_NO_AUTORUN:-}" ]; then
    netprep_main
fi
