#!/bin/bash
cd "$(dirname "$0")"

if ! command -v node &>/dev/null; then
  echo "Node.js が見つかりません。"
  echo "https://nodejs.org/ からインストールしてください。"
  exit 1
fi

if [ ! -d "node_modules" ]; then
  echo "依存パッケージをインストール中..."
  npm install
fi

echo ""
echo "╔══════════════════════════════════════╗"
echo "║    Hiro 英会話練習アプリ             ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  ブラウザで開く → http://localhost:3000"
echo "  停止する → Ctrl+C"
echo ""

node server.js
