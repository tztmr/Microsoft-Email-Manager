#!/bin/bash

set -e

echo "🚀 Microsoft-Email-Manager - 一键部署脚本"
echo "======================================="

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_ENV_SCRIPT="$ROOT_DIR/resolve-docker-build-env.sh"

get_compose_command() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
    else
        echo ""
    fi
}

COMPOSE_CMD="$(get_compose_command)"

check_dependencies() {
    echo "📋 检查依赖..."
    
    if ! command -v docker &> /dev/null; then
        echo "❌ Docker未安装，请先安装Docker"
        echo "   安装指南: https://docs.docker.com/get-docker/"
        exit 1
    fi
    
    if [ -z "$COMPOSE_CMD" ]; then
        echo "❌ Docker Compose 未安装，请先安装 docker compose 插件或 docker-compose"
        echo "   安装指南: https://docs.docker.com/compose/"
        exit 1
    fi
    
    echo "✅ 依赖检查通过"
}

create_directories() {
    echo "📁 创建数据目录..."
    mkdir -p data
    
    if [ ! -f "data/accounts.json" ]; then
        echo "{}" > data/accounts.json
        echo "✅ 创建空的账户配置文件"
    fi
}

resolve_build_env() {
    if [ ! -x "$BUILD_ENV_SCRIPT" ]; then
        chmod +x "$BUILD_ENV_SCRIPT"
    fi

    eval "$("$BUILD_ENV_SCRIPT" --export)"
    echo "🌐 网络区域: $MIRROR_REGION"
    echo "🐳 基础镜像: $BASE_IMAGE"
    echo "📦 Pip 源: $PIP_INDEX_URL"
}

deploy_service() {
    resolve_build_env

    echo "🔨 构建Docker镜像..."
    $COMPOSE_CMD build
    
    echo "🚀 启动服务..."
    $COMPOSE_CMD up -d
    
    echo "⏳ 等待服务启动..."
    sleep 10
    
    if $COMPOSE_CMD ps | grep -q "Up"; then
        echo "✅ 服务启动成功！"
        echo ""
        echo "📋 服务信息:"
        echo "   - Web界面: http://localhost:8073"
        echo "   - API文档: http://localhost:8073/docs"
        echo "   - 服务状态: $COMPOSE_CMD ps"
        echo "   - 查看日志: $COMPOSE_CMD logs -f"
        echo ""
        echo "🎉 部署完成！"
    else
        echo "❌ 服务启动失败，请检查日志:"
        echo "   $COMPOSE_CMD logs"
        exit 1
    fi
}

show_management_commands() {
    echo ""
    echo "🛠️  常用管理命令:"
    echo "   自动探测构建源: ./resolve-docker-build-env.sh"
    echo "   手动应用探测结果: eval \"\$(./resolve-docker-build-env.sh --export)\""
    echo "   启动服务: $COMPOSE_CMD up -d"
    echo "   停止服务: $COMPOSE_CMD down"
    echo "   重启服务: $COMPOSE_CMD restart"
    echo "   查看日志: $COMPOSE_CMD logs -f"
    echo "   查看状态: $COMPOSE_CMD ps"
    echo ""
}

main() {
    check_dependencies
    create_directories
    deploy_service
    show_management_commands
}

trap 'echo "❌ 部署中断"; exit 1' INT

main

echo "✨ 感谢使用 Microsoft-Email-Manager!"
