#!/bin/sh
set -eu

FORCE_REGION=${FORCE_MIRROR_REGION:-${MIRROR_REGION:-auto}}
OUTPUT_MODE=${1:-plain}

OFFICIAL_BASE_IMAGE=${OFFICIAL_BASE_IMAGE:-python:3.11-alpine3.21}
CN_BASE_IMAGE=${CN_BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-alpine}
OFFICIAL_BASE_IMAGE_FALLBACK=${OFFICIAL_BASE_IMAGE_FALLBACK:-python:3.11-alpine}
CN_BASE_IMAGE_FALLBACK=${CN_BASE_IMAGE_FALLBACK:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.11-alpine}
CN_BASE_IMAGE_FALLBACK_2=${CN_BASE_IMAGE_FALLBACK_2:-docker.m.daocloud.io/library/python:3.11-alpine3.21}

OFFICIAL_PIP_INDEX_URL=${OFFICIAL_PIP_INDEX_URL:-https://pypi.org/simple}
CN_PIP_INDEX_URL=${CN_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
CN_PIP_TRUSTED_HOST=${CN_PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}

probe_url() {
    curl -fsSL --connect-timeout 3 --max-time 5 "$1" >/dev/null 2>&1
}

image_exists() {
    image_ref=$1

    if command -v docker >/dev/null 2>&1; then
        docker manifest inspect "$image_ref" >/dev/null 2>&1
        return $?
    fi

    return 1
}

read_country_code() {
    if ! command -v curl >/dev/null 2>&1; then
        return 1
    fi

    for endpoint in \
        "https://ipinfo.io/country" \
        "https://ifconfig.co/country-iso"
    do
        country_code=$(curl -fsSL --connect-timeout 3 --max-time 5 "$endpoint" 2>/dev/null | tr -d '\r\n ' || true)
        if [ -n "${country_code:-}" ]; then
            printf '%s' "$country_code"
            return 0
        fi
    done

    return 1
}

detect_region() {
    normalized_force=$(printf '%s' "$FORCE_REGION" | tr '[:upper:]' '[:lower:]')
    case "$normalized_force" in
        cn|global)
            printf '%s' "$normalized_force"
            return 0
            ;;
        auto|"")
            ;;
        *)
            echo "Unsupported MIRROR_REGION value: $FORCE_REGION" >&2
            exit 1
            ;;
    esac

    country_code=$(read_country_code || true)
    if [ "$country_code" = "CN" ] || [ "$country_code" = "cn" ]; then
        printf 'cn'
        return 0
    fi

    if [ -n "${country_code:-}" ]; then
        printf 'global'
        return 0
    fi

    if probe_url "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"; then
        printf 'global'
        return 0
    fi

    if probe_url "https://docker.m.daocloud.io/v2/"; then
        printf 'cn'
        return 0
    fi

    printf 'global'
}

emit_env() {
    region=$1
    if [ "$region" = "cn" ]; then
        base_image=
        for candidate in "$CN_BASE_IMAGE" "$CN_BASE_IMAGE_FALLBACK" "$CN_BASE_IMAGE_FALLBACK_2"; do
            if [ -n "$candidate" ] && image_exists "$candidate"; then
                base_image=$candidate
                break
            fi
        done
        if [ -z "$base_image" ]; then
            base_image=$CN_BASE_IMAGE_FALLBACK
        fi
        pip_index_url=$CN_PIP_INDEX_URL
        pip_trusted_host=$CN_PIP_TRUSTED_HOST
    else
        base_image=
        for candidate in "$OFFICIAL_BASE_IMAGE" "$OFFICIAL_BASE_IMAGE_FALLBACK"; do
            if [ -n "$candidate" ] && image_exists "$candidate"; then
                base_image=$candidate
                break
            fi
        done
        if [ -z "$base_image" ]; then
            base_image=$OFFICIAL_BASE_IMAGE
        fi
        pip_index_url=$OFFICIAL_PIP_INDEX_URL
        pip_trusted_host=
    fi

    case "$OUTPUT_MODE" in
        --export)
            printf 'export MIRROR_REGION=%s\n' "$region"
            printf 'export BASE_IMAGE=%s\n' "$base_image"
            printf 'export PIP_INDEX_URL=%s\n' "$pip_index_url"
            printf 'export PIP_TRUSTED_HOST=%s\n' "$pip_trusted_host"
            ;;
        plain|"")
            printf 'MIRROR_REGION=%s\n' "$region"
            printf 'BASE_IMAGE=%s\n' "$base_image"
            printf 'PIP_INDEX_URL=%s\n' "$pip_index_url"
            printf 'PIP_TRUSTED_HOST=%s\n' "$pip_trusted_host"
            ;;
        *)
            echo "Unsupported output mode: $OUTPUT_MODE" >&2
            exit 1
            ;;
    esac
}

emit_env "$(detect_region)"
