# ====== Optional cgroup v2 scope for reliable garbage collection ======
# If cgroup v2 is available and writable, we create a dedicated cgroup for the script.
# All child processes are added to this cgroup. On cleanup, we kill the entire cgroup
# (this catches daemons and processes that change session/pgid).
INTERLINK_CG=""
INTERLINK_HAVE_CG=0

setup_cgroup() {
  if [ -d /sys/fs/cgroup ]; then
    INTERLINK_CG="/sys/fs/cgroup/nsenter-scope.$$"
    if mkdir -p "$INTERLINK_CG" 2>/dev/null; then
      # Add the current shell to the cgroup; children inherit membership.
      echo $$ > "$INTERLINK_CG/cgroup.procs" 2>/dev/null || true
      INTERLINK_HAVE_CG=1
    fi
  fi
}

# Track background jobs started via nsenter so we can kill them on exit.
INTERLINK_JOBS=()
INTERLINK_NETNS_PID=""
INTERLINK_SLIRP_PID=""

interlink_cleanup() {
  set +e

  echo "[cleanup] Shutting down..."

  # First try SIGTERM on tracked jobs, then SIGKILL if needed.
  for pid in "${INTERLINK_JOBS[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 1
  for pid in "${INTERLINK_JOBS[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done

  # Also terminate slirp4netns and the namespace anchor process.
  if [ -n "${INTERLINK_SLIRP_PID:-}" ]; then
    kill -TERM "$INTERLINK_SLIRP_PID" 2>/dev/null || true
    sleep 0.2
    kill -KILL "$INTERLINK_SLIRP_PID" 2>/dev/null || true
  fi

  if [ -n "${INTERLINK_NETNS_PID:-}" ]; then
    kill -TERM "$INTERLINK_NETNS_PID" 2>/dev/null || true
    sleep 0.2
    kill -KILL "$INTERLINK_NETNS_PID" 2>/dev/null || true
  fi

  # If cgroup v2 is present, kill everything in the cgroup.
  if [ "$INTERLINK_HAVE_CG" -eq 1 ]; then
    if [ -e "$INTERLINK_CG/cgroup.kill" ]; then
      # Kernel-supported atomic kill of all processes in the cgroup.
      echo 1 > "$INTERLINK_CG/cgroup.kill" 2>/dev/null || true
    else
      # Fallback: iterate processes and kill manually.
      if [ -e "$INTERLINK_CG/cgroup.procs" ]; then
        while read -r pid; do kill -TERM "$pid" 2>/dev/null || true; done < "$INTERLINK_CG/cgroup.procs"
        sleep 1
        while read -r pid; do kill -KILL "$pid" 2>/dev/null || true; done < "$INTERLINK_CG/cgroup.procs"
      fi
    fi
    rmdir "$INTERLINK_CG" 2>/dev/null || true
  fi

  echo "[cleanup] Done."
}


# ====== Helpers ======

interlink_make_namespace() {
  # Create the namespace. `setpriv --pdeathsig TERM` ensures that when this script dies,
  # the child process receives SIGTERM. The `unshare ... sleep infinity` process acts as
  # the "anchor" holding the namespace alive.
  setpriv --pdeathsig TERM \
    unshare --user --map-root-user --net --mount \
    sleep infinity &
  
  INTERLINK_NETNS_PID=$!
  
  # Add anchor to the cgroup so we can clean it up reliably.
  [ "$INTERLINK_HAVE_CG" -eq 1 ] && echo "$INTERLINK_NETNS_PID" > "$INTERLINK_CG/cgroup.procs" 2>/dev/null || true
  sleep 0.2
}

interlink_make_tap_device() {
  # Create slirp4netns bridge for the namespace. PDEATHSIG helps propagate termination.
  local slirp4netns_binary="$1" # path to slirp4netns binary
  local cidr="$2"               # CIDR of the tap device 172.18.2.0/24
  local mtu="$3"                # MTU of the tap device 1280
  local nspid="$4"                # command to execute in the namespace

  setpriv --pdeathsig TERM \
    $slirp4netns_binary --configure --mtu="$mtu" --cidr="$cidr"  --disable-host-loopback "$nspid" tap0 &

  INTERLINK_SLIRP_PID=$!
  [ "$INTERLINK_HAVE_CG" -eq 1 ] && echo "$INTERLINK_SLIRP_PID" > "$INTERLINK_CG/cgroup.procs" 2>/dev/null || true

  sleep 1
}

# Run a command inside the namespace in foreground.
# Use `--no-fork` so the PID we see is the real child’s PID.
# `setpriv --pdeathsig TERM` ensures children receive SIGTERM if the parent dies.
interlink_proxy_cmd() {
  nsenter --target="$INTERLINK_NETNS_PID" --user --mount --net --preserve-credentials --no-fork \
    setpriv --pdeathsig TERM -- "$@"
}

# Run a command inside the namespace in background and track its PID for cleanup.
interlink_proxy_cmd_bg() {
  nsenter --target="$INTERLINK_NETNS_PID" --user --mount --net --preserve-credentials --no-fork \
    setpriv --pdeathsig TERM -- "$@" &
  local pid=$!
  INTERLINK_JOBS+=("$pid")
  [ "$INTERLINK_HAVE_CG" -eq 1 ] && echo "$pid" > "$INTERLINK_CG/cgroup.procs" 2>/dev/null || true
}

interlink_ws_connect() {
  local AUTH_TOKEN="$1"
  local PORT_MAPPING="$2"
  local PATH_PREFIX="$3"
  local WEBSOCKET_URL="$4"

  # Start wstunnel in background inside the namespace and track it.
  interlink_proxy_cmd_bg "$HOME/bin/wstunnel" client \
    $PORT_MAPPING \
    --http-upgrade-path-prefix "$PATH_PREFIX/$AUTH_TOKEN" \
    "$WEBSOCKET_URL"
}

# ====== Main ======
# Ensure cleanup runs on script exit and on SIGINT/SIGTERM.
trap cleanup EXIT INT TERM

# DPORT is the dynamic port assigned for SOCKS5 proxying
DPORT=%(dynamic_fwd_port)d 

# Setup the /etc/resolv.conf file with precedence to cluster DNS
TMP_RESOLV_CONF=%(tmp_resolve_conf)s
cat <<EOF > $TMP_RESOLV_CONF
%(cluster_resolve)s
EOF

# Append host's nameservers
cat /etc/resolv.conf | grep nameserver >> $TMP_RESOLV_CONF
echo "options use-vc ndots:5" >> $TMP_RESOLV_CONF

echo "Setting up cgroup to ease cleanup of spawned processes"
interlink_setup_cgroup

echo "Creating network namespace in user space"
interlink_make_namespace

echo "Making TAP device"
#interlink_make_tap_device "%(slirp4netns_binary)s" "172.18.2.0/24" "1280" "$INTERLINK_NETNS_PID"
interlink_make_tap_device \
    "%(slirp4netns_binary)s" \
    "%(tap_cidr)s" "%(tap_mtu)s" \
    "$INTERLINK_NETNS_PID"

echo "Connecting via websocket"
interlink_ws_connect \
    "c4ecbdb37cec9bf1d766a147bc950b11" \
    "-R tcp://0.0.0.0:8081:localhost:8081 -L socks5://127.0.0.1:$DPORT" \
    "ingress-probe-manual-cst-zgcbm-interlink" \
    "wss://131.154.98.96.myip.cloud.infn.it"

echo "Configure a new TUN device and run tun2socks"

# Run network setup and then `exec tun2socks` so the only long-lived process is tun2socks.
proxy_cmd_bg /bin/bash -c "
  set -e
  ip tuntap add mode tun dev tun0
  ip addr add 172.18.3.1/24 dev tun0
  ip link set dev tun0 up
  ip route add 10.0.0.0/8 via 172.18.3.1 dev tun0
  mount --bind $TMP_RESOLV_CONF /etc/resolv.conf
  exec \"$HOME/bin/tun2socks\" -device tun0 -proxy \"socks5://localhost:$DPORT\" -interface tap0
  rm $TMP_RESOLV_CONF
"

echo "Executing the container (query example.com)"

# Foreground test command; ends by itself. No need to track its PID.
proxy_cmd apptainer exec docker://docker.io/python:3.12 /bin/sh <<EOF
cat /etc/resolv.conf  
dig @10.43.0.10 kubernetes.default.svc.cluster.local +tcp 
echo "getent"
getent hosts jupyter-hub.jupyter
echo "wget"
dig @10.43.0.10 jupyter-hub.jupyter +tcp 
curl -L http://jupyter-hub.jupyter.svc.cluster.local:8081 
echo "fine"
EOF

echo "All done."
# No explicit kill here; the trap 'cleanup' will run automatically on exit.