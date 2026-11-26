#!/usr/bin/env bash
# Build the labs formplayer: upstream dimagi/formplayer at the commit pinned in
# UPSTREAM, plus patches/*.patch applied in order, then the jar and (unless
# --no-docker) the image, using upstream's own Dockerfile.
#
#   formplayer/build.sh                       # clone, patch, jar, image labs-formplayer:<tag>
#   formplayer/build.sh --no-docker           # stop after the jar (proves the overlay applies and builds)
#   formplayer/build.sh --tests               # also run upstream's test suite (needs Postgres + env, see README)
#   formplayer/build.sh --test 'LabsAuth*'    # also run the matching formplayer test classes (repeatable;
#                                             # same Postgres + Redis + env as --tests). CI's labs set.
#   formplayer/build.sh --image REPO          # tag the image REPO:<tag> instead of labs-formplayer:<tag>
#   formplayer/build.sh --print-tag           # print the image tag for this overlay and exit
#
# The tag is content-addressed: <upstream sha, 12>-<sha256 of the patches, 12>. The same UPSTREAM and
# patches always give the same tag, and any change to either gives a new one, so a tag names exactly
# what is in the image and a rebuild can never silently replace a running image's contents.
#
# Needs git, a JDK 17 (JAVA_HOME or java on PATH) and, for the image, docker.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$HERE/.build"
IMAGE="labs-formplayer"
RUN_TESTS=0
TEST_FILTERS=()
DOCKER=1
PRINT_TAG=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tests) RUN_TESTS=1 ;;
    --no-docker) DOCKER=0 ;;
    --image) IMAGE="$2"; shift ;;
    --workdir) WORKDIR="$2"; shift ;;
    --print-tag) PRINT_TAG=1 ;;
    --test) TEST_FILTERS+=(--tests "$2"); shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

FORMPLAYER_REPO="" FORMPLAYER_SHA=""
while IFS='=' read -r name value; do
  case "$name" in
    FORMPLAYER_REPO) FORMPLAYER_REPO="$value" ;;
    FORMPLAYER_SHA) FORMPLAYER_SHA="$value" ;;
  esac
done < "$HERE/UPSTREAM"
: "${FORMPLAYER_REPO:?UPSTREAM has no FORMPLAYER_REPO}"
: "${FORMPLAYER_SHA:?UPSTREAM has no FORMPLAYER_SHA}"
if ! printf '%s' "$FORMPLAYER_SHA" | grep -qE '^[0-9a-f]{40}$'; then
  echo "UPSTREAM FORMPLAYER_SHA must be a full 40-character commit sha, got '$FORMPLAYER_SHA'" >&2
  exit 1
fi

shopt -s nullglob
PATCHES=("$HERE"/patches/*.patch)
shopt -u nullglob
if [ ${#PATCHES[@]} -eq 0 ]; then
  echo "no patches in $HERE/patches: this would build plain upstream, which has no labs mode" >&2
  exit 1
fi

if command -v sha256sum >/dev/null; then SHA256=(sha256sum); else SHA256=(shasum -a 256); fi # macOS
PATCHES_SHA=$(cat "${PATCHES[@]}" | "${SHA256[@]}" | cut -c1-12)
TAG="${FORMPLAYER_SHA:0:12}-${PATCHES_SHA}"
if [ "$PRINT_TAG" = 1 ]; then
  echo "$TAG"
  exit 0
fi

SRC="$WORKDIR/src"
echo "== formplayer $FORMPLAYER_SHA + ${#PATCHES[@]} patch(es) -> tag $TAG"
rm -rf "$SRC"
mkdir -p "$SRC"
cd "$SRC"
git init -q
git remote add origin "$FORMPLAYER_REPO"
git fetch -q --depth 1 origin "$FORMPLAYER_SHA"
git checkout -q --detach FETCH_HEAD
# The submodule (libs/commcare = commcare-core) is pinned by upstream's tree. GitHub serves any
# reachable commit, so a shallow fetch normally works; fall back to a full one if it does not.
git submodule update -q --init --recursive --depth 1 \
  || git submodule update -q --init --recursive

# Fixed identity, so the patched commits do not depend on who runs the build.
git -c user.name="connect-labs formplayer build" -c user.email="formplayer-build@invalid" \
  am -q "${PATCHES[@]}"
if [ "$(git rev-parse "HEAD~${#PATCHES[@]}")" != "$FORMPLAYER_SHA" ]; then
  echo "patches did not apply as a linear series on $FORMPLAYER_SHA" >&2
  exit 1
fi
git log --oneline "$FORMPLAYER_SHA..HEAD"

if [ -n "${JAVA_HOME:-}" ]; then
  JAVA="$JAVA_HOME/bin/java"
else
  JAVA="$(command -v java || true)"
fi
if [ -z "$JAVA" ] || ! "$JAVA" -version 2>&1 | grep -qE 'version "(1[7-9]|2[0-9])'; then
  echo "need a JDK 17+ (set JAVA_HOME); found: ${JAVA:-none}" >&2
  exit 1
fi

TASKS=(bootJar)
if [ "$RUN_TESTS" = 1 ]; then
  TASKS=(test bootJar)
elif [ ${#TEST_FILTERS[@]} -gt 0 ]; then
  # :test, not test: the filters name formplayer classes, and commcare-core's own test task
  # fails on a filter that matches nothing in it.
  TASKS=(:test "${TEST_FILTERS[@]}" bootJar)
fi
./gradlew --no-daemon -q "${TASKS[@]}"
JAR=$(ls build/libs/*.jar)
echo "== jar: $SRC/$JAR"

if [ "$DOCKER" = 0 ]; then
  exit 0
fi
docker build \
  --label "formplayer.upstream_sha=$FORMPLAYER_SHA" \
  --label "formplayer.patches_sha=$PATCHES_SHA" \
  -t "$IMAGE:$TAG" .
echo "== image: $IMAGE:$TAG"
