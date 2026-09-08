#!/bin/bash
set -u

cd "$HOME/ai_agent"

echo "=== AI Agent V6 一键启动 ==="

# 1. llama-server
if pgrep -af 'llama-server.*127.0.0.1:8080' >/dev/null; then
    echo "[OK] llama-server 已运行"
else
    echo "[START] llama-server"
    nohup "$HOME/llama.cpp/build/bin/llama-server" \
        -m "$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf" \
        --host 127.0.0.1:8080 \
        >/tmp/ai_agent_llama.log 2>&1 &
fi

# 等待 llama-server
for i in $(seq 1 30); do
    if curl --noproxy '*' -s http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
        echo "[OK] llama-server :8080"
        break
    fi
    sleep 1
done

# 2. Chromium + CDP
if pgrep -af 'chromium.*remote-debugging-port=9222' >/dev/null; then
    echo "[OK] Chromium CDP 已运行"
else
    echo "[START] Chromium CDP"
    nohup /usr/bin/chromium-browser \
        --disable-gpu \
        --remote-debugging-port=9222 \
        --remote-allow-origins=* \
        --user-data-dir="$HOME/.codex_chrome" \
        --no-first-run \
        --no-default-browser-check \
        https://chatgpt.com/ \
        >/tmp/ai_agent_chromium.log 2>&1 &
fi

# 等待 Chromium CDP
for i in $(seq 1 20); do
    if curl --noproxy '*' -s http://127.0.0.1:9222/json/list >/dev/null 2>&1; then
        echo "[OK] Chromium CDP :9222"
        break
    fi
    sleep 1
done

# 3. Agent 前台运行
echo "[START] AI Agent"
exec "$HOME/ai_agent/.venv/bin/python" "$HOME/ai_agent/my_agent_v3.py"
