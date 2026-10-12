#!/bin/bash
# 在图形终端窗口中运行 frpc 代理服务
# 由 systemd 用户服务 frp-proxy.service 在 iot-smart-home.service 启动成功后调用
#
# 用法：
#   ./start_frpc_terminal.sh            # 外部调用（systemd）：
#                                        #   桌面会话已就绪 -> 直接在终端窗口运行 frpc
#                                        #   桌面会话未就绪 -> 先后台运行保证隧道可用，
#                                        #                     登录后自动切换到终端窗口
#   ./start_frpc_terminal.sh --inner    # 终端窗口内部：循环运行 frpc
#   ./start_frpc_terminal.sh --cleanup  # 清理残留 frpc 进程（systemd ExecStopPost 调用）
#
# 可覆盖的环境变量：
#   FRP_DIR       frpc 所在目录，默认 ~/Downloads/frp_0.70.1_linux_amd64
#   FRPC_CONF     配置文件，默认 frpc.toml
#   SERVICE_PORT  依赖的本地服务端口，默认 8006
#   LOG_FILE      日志文件（同时输出到终端窗口），默认 <项目>/logs/frpc.log
#   XDG_RUNTIME_DIR  用户运行时目录，默认 /run/user/<uid>
#   X11_SOCKET_DIR   X11 socket 目录，默认 /tmp/.X11-unix

set -uo pipefail

FRP_DIR="${FRP_DIR:-$HOME/Downloads/frp_0.70.1_linux_amd64}"
FRPC_CONF="${FRPC_CONF:-frpc.toml}"
SERVICE_PORT="${SERVICE_PORT:-8006}"
FRPC_BIN="$FRP_DIR/frpc"
LOG_FILE="${LOG_FILE:-$HOME/Projects/iot-smart_home/logs/frpc.log}"
X11_SOCKET_DIR="${X11_SOCKET_DIR:-/tmp/.X11-unix}"

SELF="$(readlink -f "$0")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ============ 清理残留的 frpc 进程 ============
# gnome-terminal 会把终端内的子进程放到 gnome-terminal-server 自己的 cgroup 中，
# 不在 frp-proxy.service 的 cgroup 内，systemd 停止服务时无法自动回收，
# 残留进程会导致 frp 服务端报 “proxy [web_8006] already exists”。
cleanup_stale_frpc() {
    local pids=""
    # 1) 残留的 frpc 客户端进程
    pids="$(pgrep -f "^${FRPC_BIN} -c" 2>/dev/null || true)"
    # 2) 残留的终端内部循环脚本（它会不断重启 frpc）
    pids="$pids
$(pgrep -f "${SELF} --inner" 2>/dev/null || true)"
    # 去重，只保留有效的数字 PID
    pids="$(echo "$pids" | tr -s ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ')"
    if [ -z "$pids" ]; then
        return 0
    fi
    log "清理残留进程: $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 2
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
    return 0
}

# 供 systemd 的 ExecStopPost 调用
if [ "${1:-}" = "--cleanup" ]; then
    cleanup_stale_frpc
    exit 0
fi

# 只杀掉其它终端窗口里的循环（用于切换模式时关掉旧窗口），不动 frpc
kill_other_loops() {
    local self_pid=$$ pids
    pids="$(pgrep -f "${SELF} --inner" 2>/dev/null | grep -v "^${self_pid}$" || true)"
    if [ -n "$pids" ]; then
        log "关闭其它终端窗口内的 frpc 循环: $(echo "$pids" | tr '\n' ' ')"
        echo "$pids" | xargs -r kill 2>/dev/null || true
    fi
    return 0
}

# 终端窗口内的循环启动 frpc 前“接管”：清掉其它 frpc 实例（例如切模式前
# 后台运行的实例）。放在这里而不是切换之前，可以把隧道中断压缩到 1~2 秒。
takeover_frpc() {
    local pids rest
    pids="$(pgrep -f "^${FRPC_BIN} -c" 2>/dev/null || true)"
    pids="$(echo "$pids" | tr -s ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ')"
    if [ -z "$pids" ]; then
        return 0
    fi
    log "接管已有 frpc 实例: $pids" | tee -a "$LOG_FILE"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    # 给 frpc 时间优雅退出（它会通知服务端注销 proxy），避免新实例报 already exists
    local i
    for i in 1 2 3 4; do
        pgrep -f "^${FRPC_BIN} -c" >/dev/null 2>&1 || break
        sleep 0.5
    done
    rest="$(pgrep -f "^${FRPC_BIN} -c" 2>/dev/null || true)"
    if [ -n "$rest" ]; then
        # shellcheck disable=SC2086
        kill -9 $rest 2>/dev/null || true
    fi
    return 0
}

# ============ 终端窗口内部：循环运行 frpc ============
if [ "${1:-}" = "--inner" ]; then
    trap 'echo; echo "收到停止信号，frpc 退出。"; exit 0' INT TERM HUP
    if [ ! -x "$FRPC_BIN" ]; then
        echo "[ERROR] 未找到可执行文件: $FRPC_BIN"
        exit 1
    fi
    cd "$FRP_DIR" || { echo "[ERROR] 无法进入目录: $FRP_DIR"; exit 1; }

    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null

    echo "==================== frpc 代理服务 ===================="
    echo "工作目录: $PWD"
    echo "配置文件: $FRPC_CONF"
    echo "日志文件: $LOG_FILE"
    echo "按 Ctrl+C 或关闭窗口即停止（重新启动: systemctl --user start frp-proxy）"
    echo "======================================================="

    while true; do
        takeover_frpc
        "$FRPC_BIN" -c "$FRPC_CONF" 2>&1 | tee -a "$LOG_FILE"
        rc="${PIPESTATUS[0]}"
        log "frpc 已退出（退出码 $rc），5 秒后自动重启..." | tee -a "$LOG_FILE"
        sleep 5
    done
fi

# ============ 等待本地服务端口就绪 ============
wait_for_port() {
    local waited=0
    while [ "$waited" -lt 120 ]; do
        if (exec 3<>"/dev/tcp/127.0.0.1/$SERVICE_PORT") 2>/dev/null; then
            log "本地服务端口 127.0.0.1:$SERVICE_PORT 已就绪"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    log "警告: 等待端口 $SERVICE_PORT 超时（已 120 秒），继续启动 frpc"
    return 1
}

# ============ 桌面会话检测 ============
# 注意：linger 模式下 user manager 在开机阶段就启动，此时尚未登录，
# 该用户自己的 wayland/X socket 还不存在（登录界面跑在 gdm 用户下），
# 因此不能仅凭 $XDG_RUNTIME_DIR/bus 判断桌面已就绪。
graphical_session_ready() {
    local uid runtime
    uid="$(id -u)"
    runtime="${XDG_RUNTIME_DIR:-/run/user/$uid}"
    # 用户会话 D-Bus（gnome-terminal 需要它连接 gnome-shell）
    [ -S "$runtime/bus" ] || return 1
    # 该用户自己的图形 socket：存在即说明桌面会话已建立
    if [ -S "$runtime/wayland-0" ]; then
        return 0
    fi
    # X11 会话：socket 需属于当前用户，避免误判到他人的显示
    if [ -S "$X11_SOCKET_DIR/X0" ] && [ -O "$X11_SOCKET_DIR/X0" ]; then
        return 0
    fi
    return 1
}

export_graphical_env() {
    local uid runtime
    uid="$(id -u)"
    runtime="${XDG_RUNTIME_DIR:-/run/user/$uid}"
    export XDG_RUNTIME_DIR="$runtime"
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
    export DISPLAY="${DISPLAY:-:0}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime/bus}"
}

# 在 gnome-terminal 窗口中运行 frpc（--wait 使 systemd 能跟踪该进程）
launch_in_terminal() {
    export_graphical_env
    log "在 gnome-terminal 窗口中启动 frpc（DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY）"
    exec gnome-terminal --wait --window --title="frpc 代理服务" \
        -- /bin/bash -c "'$SELF' --inner"
}

wait_for_port
cleanup_stale_frpc

if ! command -v gnome-terminal >/dev/null 2>&1; then
    log "未找到 gnome-terminal，改为后台运行 frpc"
    exec /bin/bash "$SELF" --inner
fi

# 桌面会话已就绪（登录状态下手动启停服务）→ 直接弹终端窗口
if graphical_session_ready; then
    launch_in_terminal
fi

# 桌面会话尚未建立（典型场景：开机自动启动时还没登录）：
# 先后台运行 frpc，保证隧道立即可用；随后持续等待桌面会话，
# 一旦登录就切换到终端窗口模式。
log "桌面会话尚未就绪，先后台运行 frpc（隧道立即可用），登录后自动切换为终端窗口"
/bin/bash "$SELF" --inner &
HEADLESS_PID=$!

while kill -0 "$HEADLESS_PID" 2>/dev/null; do
    if graphical_session_ready; then
        sleep 2
        log "检测到桌面会话，切换到终端窗口模式"
        kill_other_loops
        launch_in_terminal
    fi
    sleep 5
done

log "后台 frpc 已退出，服务结束"
exit 0
