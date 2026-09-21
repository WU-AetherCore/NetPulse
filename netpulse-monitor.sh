#!/bin/bash
# NetPulse 自动监控恢复脚本 v2.0
# 功能：检测服务状态、全局流量监控、iptables规则、流量统计更新、ARP欺骗是否真正生效，如果异常则自动恢复

LOG_FILE="/var/log/netpulse-monitor.log"
NETPULSE_URL="http://localhost:8081"
STATE_FILE="/tmp/netpulse-monitor-state"
TRAFFIC_STATE_FILE="/tmp/netpulse-traffic-state"
CONFIG_FILE="/opt/netpulse/config.py"
EXPECTED_INTERFACE="eth0"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

run_sudo() {
    # cron 以 root 运行时直接执行；非 root 环境依赖免密 sudo（sudo -n），不在脚本中保存密码
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo -n "$@" 2>/dev/null
    fi
}

check_service() {
    if ! run_sudo systemctl is-active --quiet netpulse; then
        log "[WARN] NetPulse服务未运行，正在启动..."
        run_sudo systemctl start netpulse
        sleep 5
        run_sudo systemctl is-active --quiet netpulse && log "[OK] 启动成功" || log "[ERROR] 启动失败"
    fi
}

check_web() {
    if ! curl -s --connect-timeout 5 "$NETPULSE_URL/api/system" > /dev/null 2>&1; then
        log "[WARN] Web界面无法访问，重启服务..."
        run_sudo systemctl restart netpulse
        sleep 8
    fi
}

check_global_spoof() {
    status=$(curl -s --connect-timeout 5 "$NETPULSE_URL/api/global-spoof/status" 2>/dev/null)
    if ! echo "$status" | grep -q '"enabled":true'; then
        log "[WARN] 全局流量监控未开启，正在开启..."
        curl -s -X POST --connect-timeout 5 "$NETPULSE_URL/api/global-spoof/enable" > /dev/null 2>&1
        sleep 3
    fi
}

check_iptables() {
    if ! run_sudo iptables -L NETPULSE -n > /dev/null 2>&1; then
        log "[WARN] iptables流量统计链不存在，重启服务..."
        run_sudo systemctl restart netpulse
        sleep 8
    fi
}

check_nat_rules() {
    if ! run_sudo iptables -t nat -L PREROUTING -n | grep -q "dpt:53"; then
        log "[WARN] DNS劫持规则丢失，重新应用NAT规则..."
        run_sudo /usr/local/bin/netpulse-nat.sh
    fi
}

check_config() {
    manage_interface=$(grep 'MANAGE_INTERFACE' "$CONFIG_FILE" | grep -oP '"\K[^"]+' | head -1)
    if [ "$manage_interface" != "$EXPECTED_INTERFACE" ]; then
        log "[WARN] MANAGE_INTERFACE=$manage_interface，自动修正为$EXPECTED_INTERFACE..."
        sed -i "s/MANAGE_INTERFACE = \"$manage_interface\"/MANAGE_INTERFACE = \"$EXPECTED_INTERFACE\"/" "$CONFIG_FILE"
        run_sudo systemctl restart netpulse
        sleep 8
    fi
}

check_arp_spoof_effective() {
    if [ -f "$TRAFFIC_STATE_FILE" ]; then
        last_rx=$(cat "$TRAFFIC_STATE_FILE")
    else
        last_rx=0
    fi
    current_rx=$(cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null || echo 0)
    if [ "$last_rx" -gt 0 ]; then
        rx_diff=$((current_rx - last_rx))
        if [ "$rx_diff" -lt 102400 ]; then
            log "[WARN] ARP欺骗可能失效！1分钟接收仅${rx_diff}字节，重启服务..."
            run_sudo systemctl restart netpulse
            sleep 8
            run_sudo /usr/local/bin/netpulse-nat.sh
        fi
    fi
    echo "$current_rx" > "$TRAFFIC_STATE_FILE"
}

check_traffic_update() {
    current_traffic=$(curl -s --connect-timeout 5 "$NETPULSE_URL/api/summary" 2>/dev/null | grep -o '"total_download":[0-9]*' | cut -d: -f2)
    if [ -n "$current_traffic" ] && [ -f "$STATE_FILE" ]; then
        last_traffic=$(cat "$STATE_FILE")
        if [ "$current_traffic" = "$last_traffic" ]; then
            log "[WARN] 流量统计5分钟未更新，重启服务..."
            run_sudo systemctl restart netpulse
            sleep 8
        fi
    fi
    [ -n "$current_traffic" ] && echo "$current_traffic" > "$STATE_FILE"
}

main() {
    log "=== 开始监控检查 v2.0 ==="
    check_service
    check_web
    check_global_spoof
    check_config
    check_iptables
    check_nat_rules
    check_arp_spoof_effective
    
    check_count_file="/tmp/netpulse-check-count"
    count=$(cat "$check_count_file" 2>/dev/null || echo 0)
    count=$((count + 1))
    if [ "$count" -ge 5 ]; then
        check_traffic_update
        count=0
    fi
    echo "$count" > "$check_count_file"
    log "=== 监控检查完成 ==="
}

main
