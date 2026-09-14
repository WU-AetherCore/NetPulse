# NetPulse 网脉

轻量级网络设备管理系统，专为家庭/小型办公室网络设计。基于 Python + Flask + SQLite + iptables，运行在 Orange Pi / 树莓派等 Linux 设备上。

![Version](https://img.shields.io/badge/version-2.3-blue)
![Python](https://img.shields.io/badge/python-3.7+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

---

## ✨ 功能特性

### 📊 仪表盘
- 实时显示在线设备数量、上传/下载速率、总流量
- 实时流量趋势图（最近10分钟）
- 流量排行 TOP10
- 全局流量监控模式开关

### 📱 设备管理
- 自动扫描局域网所有设备（ARP + Ping）
- 显示设备IP、MAC、厂商信息
- 设备在线/离线状态实时监测
- 设备重命名
- WiFi频段标注（2.4G/5G/有线，点击切换）

### 🚫 设备控制
- **封禁设备**：一键踢出网络（ARP欺骗 + iptables DROP）
- **设备限速**：基于 tc HTB 的上下行限速
- **全局流量监控**：一键开启，所有设备流量自动经过管理设备

### 📈 流量统计
- iptables 内核级精确流量计数
- 实时上传/下载速率（滑动窗口平均）
- 每设备累计流量统计
- 24小时/7天/30天流量趋势图

### 🏆 流量排行
- 按今天/本周/本月统计
- 按总流量/下载/上传排序
- 前三名奖牌样式展示
- 点击设备查看详情

### 📋 连接记录
- 设备首次发现记录
- 设备上下线记录
- 封禁/限速操作记录
- 最多保留90天历史

---

## 🖥️ 系统要求

### 硬件
- **推荐**：Orange Pi Zero 2（1GB内存）/ 树莓派4B（2GB+）
- **最低**：任何能运行 Linux 的设备（512MB内存以上）
- 网络：有线或WiFi连接到路由器

### 软件
- 操作系统：Ubuntu 20.04+ / Debian 11+ / Armbian
- Python：3.7+
- 权限：root（iptables和tc需要）
- 依赖：iptables、iproute2（tc）

---

## 🚀 快速开始（一键部署）

### 方式一：一键安装脚本（推荐）

```bash
# 下载并运行一键安装脚本
curl -sSL https://raw.githubusercontent.com/WU-AetherCore/NetPulse/main/install.sh | sudo bash
```

安装完成后访问：`http://<设备IP>:8081`

### 方式二：Docker 部署

```bash
# 注意：Docker模式下设备封禁/限速功能可能受限
docker run -d \
  --name netpulse \
  --network host \
  --privileged \
  -p 8081:8081 \
  -v /etc/netpulse:/app/data \
  wuaethercore/netpulse:latest
```

---

## 📦 手动部署步骤

### 第一步：安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv iptables iproute2
```

### 第二步：下载项目

```bash
# 克隆仓库
git clone https://github.com/WU-AetherCore/NetPulse.git
cd NetPulse

# 或者下载 ZIP 包
wget https://github.com/WU-AetherCore/NetPulse/archive/refs/heads/main.zip
unzip main.zip
cd NetPulse-main
```

### 第三步：创建虚拟环境并安装依赖

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
```

### 第四步：配置系统

编辑 `config.py`，根据你的网络修改配置：

```python
# 网络配置
NETWORK_CIDR = "192.168.1.0/24"     # 你的网段
NETWORK_GATEWAY = "192.168.1.1"     # 路由器网关IP
MONITOR_INTERFACE = "eth0"          # 监控网口（eth0或wlan0）

# 设备管理配置
GATEWAY_IP = "192.168.1.1"          # 网关IP
LOCAL_MAC = "aa:bb:cc:dd:ee:ff"     # 本机MAC地址（用 ifconfig 查看）
MANAGE_INTERFACE = "eth0"            # 管理网口

# Web服务
WEB_PORT = 8081                      # Web界面端口
```

查看本机MAC地址：
```bash
ifconfig | grep ether
```

### 第五步：配置 systemd 服务（开机自启）

```bash
# 复制服务文件
sudo cp netpulse.service /etc/systemd/system/

# 编辑服务文件，修改路径为你的实际路径
sudo nano /etc/systemd/system/netpulse.service

# 重新加载并启动
sudo systemctl daemon-reload
sudo systemctl enable netpulse
sudo systemctl start netpulse

# 查看状态
sudo systemctl status netpulse
```

### 第六步：访问Web界面

在浏览器中打开：`http://<设备IP>:8081`

---

## ⚙️ 配置说明

### config.py 详细配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WEB_HOST` | `0.0.0.0` | Web服务监听地址 |
| `WEB_PORT` | `8081` | Web服务端口 |
| `NETWORK_CIDR` | `192.168.1.0/24` | 扫描的网段 |
| `NETWORK_GATEWAY` | `192.168.1.1` | 网关IP |
| `GATEWAY_IP` | `192.168.1.1` | 网关IP（用于ARP欺骗） |
| `LOCAL_MAC` | - | 本机MAC地址（必须配置） |
| `MANAGE_INTERFACE` | `eth0` | 管理网口 |
| `SCAN_INTERVAL` | `30` | 设备扫描间隔（秒） |
| `TRAFFIC_INTERVAL` | `3` | 流量统计间隔（秒） |
| `TRAFFIC_CHAIN` | `NETPULSE` | iptables链名 |
| `HOURLY_RETENTION_DAYS` | `7` | 小时流量保留天数 |
| `DAILY_RETENTION_DAYS` | `90` | 天流量保留天数 |
| `OFFLINE_THRESHOLD` | `120` | 设备离线判定阈值（秒） |

---

## 📖 使用说明

### 开启流量统计

NetPulse 需要设备流量经过管理设备才能统计流量。有两种方式：

#### 方式一：全局流量监控模式（推荐）

1. 打开仪表盘
2. 找到「流量统计需要设备流量经过 Orange Pi」卡片
3. 点击「🚀 开启全局流量监控模式」
4. 所有设备流量将自动经过管理设备，无需手动设置

**原理**：通过ARP欺骗让所有设备以为网关是管理设备，流量自动转发。

**注意**：
- 开启期间管理设备必须保持运行，否则设备会断网
- 关闭后自动恢复正常网络
- 部分有ARP防护的企业级设备可能不受影响

#### 方式二：手动设置网关

在每台设备上手动设置网关为管理设备的IP（如192.168.1.10）。

### 封禁设备

1. 进入「设备列表」
2. 找到要封禁的设备，点击「封禁」按钮
3. 确认后设备将无法访问互联网
4. 点击「解封」恢复网络

### 设备限速

1. 进入「设备列表」
2. 点击「限速」按钮
3. 输入上传和下载限速值（kbps）
4. 点击「取消」解除限速

### 标注WiFi频段

1. 进入「设备列表」
2. 点击设备名称下方的频段标签（默认"未知"）
3. 标签会循环切换：未知 → 2.4G → 5G → 有线 → 未知

### 查看流量排行

1. 点击「流量排行」标签
2. 选择时间范围：今天/本周/本月
3. 选择排序方式：总流量/下载/上传
4. 点击设备查看详情

---

## 🔌 API 文档

### 系统概览
```
GET /api/summary
```

### 设备列表
```
GET /api/devices
```

### 设备详情
```
GET /api/device/<mac>
```

### 设备重命名
```
POST /api/device/<mac>/rename
Content-Type: application/json
{"name": "新名称"}
```

### 封禁设备
```
POST /api/device/<mac>/block
```

### 解除封禁
```
POST /api/device/<mac>/unblock
```

### 设备限速
```
POST /api/device/<mac>/limit
Content-Type: application/json
{"upload_kbps": 100, "download_kbps": 500}
```

### 取消限速
```
POST /api/device/<mac>/unlimit
```

### 设置WiFi频段
```
POST /api/device/<mac>/band
Content-Type: application/json
{"band": "5g"}  // 2.4g / 5g / wired / unknown
```

### 流量排行
```
GET /api/traffic-ranking?days=7&sort=total
// days: 1 / 7 / 30
// sort: total / download / upload
```

### 全局流量监控状态
```
GET /api/global-spoof/status
```

### 开启全局流量监控
```
POST /api/global-spoof/enable
```

### 关闭全局流量监控
```
POST /api/global-spoof/disable
```

### 连接事件
```
GET /api/events?limit=100&mac=<可选>
```

### 立即扫描
```
POST /api/scan
```

---

## 🛠️ 常见问题

### Q: 流量统计显示0怎么办？
A: 需要开启「全局流量监控模式」，或者手动将设备网关设置为管理设备IP。

### Q: 开启全局监控后设备断网了？
A: 检查管理设备的IP转发是否开启：
```bash
cat /proc/sys/net/ipv4/ip_forward  # 应该返回1
sudo sysctl -w net.ipv4.ip_forward=1
```
检查NAT规则：
```bash
sudo iptables -t nat -L POSTROUTING -n
```

### Q: 设备封禁不生效？
A: 确保设备和管理设备在同一网段，并且管理设备有root权限。

### Q: 如何修改Web端口？
A: 编辑 `config.py` 中的 `WEB_PORT`，然后重启服务。

### Q: 数据存在哪里？
A: SQLite数据库文件 `netpulse.db`，在程序运行目录下。

### Q: 如何备份数据？
A: 复制 `netpulse.db` 文件即可，恢复时放回原目录。

### Q: 支持哪些路由器？
A: 任何标准路由器都支持，不需要路由器特殊功能。中继模式下也能正常工作。

---

## 📁 项目结构

```
NetPulse/
├── app.py              # Flask主程序，Web界面和API
├── config.py           # 配置文件
├── database.py         # SQLite数据库操作
├── scanner.py          # 设备扫描模块（ARP+Ping）
├── traffic.py          # 流量统计模块（iptables）
├── device_manager.py   # 设备管理模块（ARP欺骗/封禁/限速）
├── requirements.txt    # Python依赖
├── netpulse.service    # systemd服务文件
├── install.sh          # 一键安装脚本
├── templates/
│   └── index.html      # Web前端界面
└── README.md           # 说明文档
```

---

## 🔧 技术栈

- **后端**：Python 3 + Flask
- **数据库**：SQLite（WAL模式）
- **前端**：原生HTML/CSS/JavaScript + Chart.js
- **流量统计**：iptables 内核级计数
- **设备控制**：ARP欺骗 + iptables DROP + tc HTB
- **服务管理**：systemd

---

## ⚠️ 免责声明

本工具仅供学习和家庭网络管理使用。请勿用于非法用途。使用ARP欺骗功能请确保你有权管理该网络。

---

## 📄 许可证

MIT License

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

## 📮 联系方式

- GitHub Issues：https://github.com/WU-AetherCore/NetPulse/issues
