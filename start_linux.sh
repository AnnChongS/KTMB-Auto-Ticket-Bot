#!/bin/bash

export PYTHONIOENCODING=utf-8

echo "==================================================="
echo "    🚄 KTMB 抢票系统 - Linux 一键启动"
echo "==================================================="
echo ""

# 1. 检查依赖
if ! dpkg -l | grep -q python3-venv; then
    echo "[系统] 正在安装 Python 依赖..."
    sudo apt update
    sudo apt install python3-venv python3-pip -y
fi

# 检查虚拟显示器
if ! command -v xvfb-run &> /dev/null; then
    echo "[系统] 正在安装核心组件 Xvfb (虚拟显示器)..."
    sudo apt update
    sudo apt install xvfb -y
fi

VENV_DIR="venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[系统] 创建虚拟环境..."
    python3 -m venv $VENV_DIR
fi
source $VENV_DIR/bin/activate

echo "[系统] 安装 Python 依赖库..."
pip install -r requirements.txt

echo "[系统] 正在配置 Linux 浏览器引擎..."
playwright install chromium
sudo npx playwright install-deps

# 杀掉旧的服务器和虚拟屏幕进程
pkill -f app.py
pkill -f Xvfb

echo "[系统] 正在虚拟屏幕 (:99) 中启动 Web 守护进程..."
nohup xvfb-run --server-args="-screen 0 1920x1080x24" python3 app.py > web_server.log 2>&1 &

echo ""
echo "==================================================="
echo "  🎉 启动成功！"
echo "  浏览器打开 http://localhost:5000 配置和启动抢票"
echo "  默认密码: admin123 (可通过环境变量 KTMB_WEB_PASSWORD 修改)"
echo "==================================================="
