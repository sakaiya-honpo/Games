"""
Vivaldi → Floorp パスワード移行ツール

Vivaldiに保存されたパスワード・ブックマーク・Cookie・履歴を
Floorpにインポートできる形式でエクスポートします。

使い方:
  1. Vivaldi を閉じる
  2. python vivaldi_to_floorp.py
  3. Floorp で about:logins を開き、CSVインポートでパスワードを取り込む
  4. ブックマークは Floorp のブックマークマネージャーからHTMLインポート

動作環境: Windows 10/11
依存: pip install pycryptodomex pywin32
"""

import argparse
import csv
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VIVALDI_PROFILES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Vivaldi" / "User Data",
]

FLOORP_PROFILES = [
    Path(os.environ.get("APPDATA", "")) / "Floorp" / "Profiles",
]


def find_vivaldi_profiles():
    """Vivaldiのプロファイルディレクトリを検出する。"""
    profiles = []
    for base in VIVALDI_PROFILES:
        if not base.exists():
            continue
        local_state = base / "Local State"
        if local_state.exists():
            try:
                with open(local_state, "r", encoding="utf-8") as f:
                    state = json.load(f)
                info_cache = state.get("profile", {}).get("info_cache", {})
                for profile_dir in info_cache:
                    full = base / profile_dir
                    if full.exists():
                        name = info_cache[profile_dir].get("name", profile_dir)
                        profiles.append((full, name))
            except (json.JSONDecodeError, KeyError):
                pass
        if not profiles:
            default = base / "Default"
            if default.exists():
                profiles.append((default, "Default"))
    return profiles


def get_vivaldi_master_key(user_data_dir):
    """VivaldiのLocal StateからDPAPI暗号化マスターキーを取得・復号する。"""
    try:
        import win32crypt
        from Cryptodome.Cipher import AES
    except ImportError:
        print("エラー: 必要なライブラリがありません。")
        print("  pip install pycryptodomex pywin32")
        sys.exit(1)

    local_state_path = user_data_dir / "Local State"
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    import base64
    encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    # "DPAPI" プレフィックスを除去
    encrypted_key = encrypted_key[5:]
    master_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
    return master_key


def decrypt_password(encrypted_value, master_key):
    """Chromium形式の暗号化パスワードを復号する。"""
    if not encrypted_value:
        return ""

    try:
        import win32crypt
        from Cryptodome.Cipher import AES
    except ImportError:
        return "<復号不可: ライブラリ不足>"

    # v10以降のAES-GCM暗号化
    if encrypted_value[:3] == b"v10" or encrypted_value[:3] == b"v11":
        iv = encrypted_value[3:15]
        payload = encrypted_value[15:]
        cipher = AES.new(master_key, AES.MODE_GCM, nonce=iv)
        decrypted = cipher.decrypt(payload)
        # GCMタグ(16バイト)を除去
        return decrypted[:-16].decode("utf-8", errors="replace")

    # 古い形式 (DPAPI直接暗号化)
    try:
        return win32crypt.CryptUnprotectData(
            encrypted_value, None, None, None, 0
        )[1].decode("utf-8", errors="replace")
    except Exception:
        return "<復号失敗>"


def export_passwords(profile_dir, master_key, output_path):
    """Vivaldiのパスワードをfloorp互換CSV形式でエクスポートする。"""
    login_data = profile_dir / "Login Data"
    if not login_data.exists():
        print(f"  Login Data が見つかりません: {login_data}")
        return 0

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(login_data, tmp.name)

    try:
        conn = sqlite3.connect(tmp.name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT origin_url, action_url, username_value, password_value, "
            "date_created, date_last_used FROM logins ORDER BY origin_url"
        )
        rows = cursor.fetchall()
        conn.close()
    finally:
        os.unlink(tmp.name)

    # Firefox/Floorp CSVインポート形式
    # url, username, password, httpRealm, formActionOrigin, guid, timeCreated,
    # timeLastUsed, timePasswordChanged
    count = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "url", "username", "password", "httpRealm",
            "formActionOrigin", "guid", "timeCreated",
            "timeLastUsed", "timePasswordChanged"
        ])
        for origin_url, action_url, username, encrypted_pw, created, last_used in rows:
            if not username and not encrypted_pw:
                continue
            password = decrypt_password(encrypted_pw, master_key)
            if not password or password.startswith("<"):
                continue
            # Chrome epoch (1601-01-01) → Unix epoch (ms)
            def chrome_to_unix_ms(chrome_time):
                if not chrome_time:
                    return 0
                return max(0, (chrome_time - 11644473600000000) // 1000)

            writer.writerow([
                origin_url,
                username,
                password,
                "",  # httpRealm
                action_url or origin_url,
                "",  # guid (自動生成される)
                chrome_to_unix_ms(created),
                chrome_to_unix_ms(last_used),
                chrome_to_unix_ms(created),
            ])
            count += 1

    return count


def export_bookmarks(profile_dir, output_path):
    """VivaldiのブックマークをNetscape HTML形式でエクスポートする。"""
    bookmarks_file = profile_dir / "Bookmarks"
    if not bookmarks_file.exists():
        print(f"  Bookmarks が見つかりません: {bookmarks_file}")
        return 0

    with open(bookmarks_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    count = 0

    def write_node(f, node, indent=1):
        nonlocal count
        prefix = "    " * indent
        if node.get("type") == "folder":
            name = node.get("name", "フォルダ")
            f.write(f'{prefix}<DT><H3>{_escape(name)}</H3>\n')
            f.write(f'{prefix}<DL><p>\n')
            for child in node.get("children", []):
                write_node(f, child, indent + 1)
            f.write(f'{prefix}</DL><p>\n')
        elif node.get("type") == "url":
            url = node.get("url", "")
            name = node.get("name", url)
            date_added = node.get("date_added", "0")
            try:
                ts = (int(date_added) - 11644473600000000) // 1000000
            except (ValueError, TypeError):
                ts = 0
            f.write(
                f'{prefix}<DT><A HREF="{_escape(url)}" '
                f'ADD_DATE="{ts}">{_escape(name)}</A>\n'
            )
            count += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE NETSCAPE-Bookmark-file-1>\n")
        f.write('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n')
        f.write("<TITLE>Bookmarks</TITLE>\n")
        f.write("<H1>Bookmarks</H1>\n")
        f.write("<DL><p>\n")

        roots = data.get("roots", {})
        for key in ["bookmark_bar", "other", "synced"]:
            root = roots.get(key)
            if root:
                write_node(f, root)

        f.write("</DL><p>\n")

    return count


def export_cookies(profile_dir, master_key, output_path):
    """VivaldiのCookieをJSON形式でエクスポートする。"""
    cookies_file = profile_dir / "Cookies"
    if not cookies_file.exists():
        # ネットワークサービス以降
        cookies_file = profile_dir / "Network" / "Cookies"
    if not cookies_file.exists():
        print(f"  Cookies が見つかりません")
        return 0

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(cookies_file, tmp.name)

    try:
        conn = sqlite3.connect(tmp.name)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT host_key, name, encrypted_value, path, "
                "is_secure, is_httponly, expires_utc FROM cookies"
            )
        except sqlite3.OperationalError:
            cursor.execute(
                "SELECT host_key, name, encrypted_value, path, "
                "secure, httponly, expires_utc FROM cookies"
            )
        rows = cursor.fetchall()
        conn.close()
    finally:
        os.unlink(tmp.name)

    cookies = []
    for host, name, encrypted_val, path, secure, httponly, expires in rows:
        value = decrypt_password(encrypted_val, master_key)
        if value.startswith("<"):
            continue
        cookies.append({
            "host": host,
            "name": name,
            "value": value,
            "path": path,
            "secure": bool(secure),
            "httpOnly": bool(httponly),
            "expirationDate": expires // 1000000 if expires else 0,
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

    return len(cookies)


def export_history(profile_dir, output_path):
    """Vivaldiの閲覧履歴をJSON形式でエクスポートする。"""
    history_file = profile_dir / "History"
    if not history_file.exists():
        print(f"  History が見つかりません: {history_file}")
        return 0

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(history_file, tmp.name)

    try:
        conn = sqlite3.connect(tmp.name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT url, title, visit_count, last_visit_time "
            "FROM urls ORDER BY last_visit_time DESC"
        )
        rows = cursor.fetchall()
        conn.close()
    finally:
        os.unlink(tmp.name)

    history = []
    for url, title, visit_count, last_visit in rows:
        try:
            ts = (last_visit - 11644473600000000) // 1000000
        except (TypeError, ValueError):
            ts = 0
        history.append({
            "url": url,
            "title": title,
            "visitCount": visit_count,
            "lastVisit": ts,
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return len(history)


def _escape(text):
    """HTMLエスケープ。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def install_floorp_instructions():
    """Floorpのインストール手順を表示する。"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║              Floorp インストール手順                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. 公式サイトからダウンロード:                              ║
║     https://floorp.app/ja/download                           ║
║                                                              ║
║  2. または winget でインストール:                            ║
║     winget install Ablaze.Floorp                             ║
║                                                              ║
║  3. または scoop でインストール:                             ║
║     scoop bucket add extras                                  ║
║     scoop install floorp                                     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


def import_instructions(output_dir):
    """Floorpへのインポート手順を表示する。"""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              Floorp インポート手順                           ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  【パスワード】                                              ║
║  1. Floorp で about:logins を開く                            ║
║  2. 右上の「…」メニュー → 「ログイン情報をインポート」      ║
║  3. passwords.csv を選択                                     ║
║                                                              ║
║  【ブックマーク】                                            ║
║  1. Ctrl+Shift+O でブックマークマネージャーを開く            ║
║  2. 「インポートとバックアップ」→                            ║
║     「HTMLからブックマークをインポート」                      ║
║  3. bookmarks.html を選択                                    ║
║                                                              ║
║  【Cookie】                                                  ║
║  → cookies.json は手動インポート不可。                       ║
║    サイトに再ログインしてください。                          ║
║    ※ パスワードが移行済みなので自動入力で簡単です           ║
║                                                              ║
║  【履歴】                                                    ║
║  → history.json は参照用エクスポートです。                   ║
║    Floorp への直接インポートはサポートされていません。        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

エクスポート先: {output_dir}

⚠️  passwords.csv にはパスワードが平文で含まれています。
    インポート完了後、必ず削除してください。
""")


def main():
    parser = argparse.ArgumentParser(
        description="Vivaldi → Floorp パスワード・データ移行ツール"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="エクスポート先ディレクトリ (デフォルト: デスクトップ/vivaldi_export)",
    )
    parser.add_argument(
        "--passwords-only",
        action="store_true",
        help="パスワードのみエクスポート",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Vivaldiプロファイル名 (デフォルト: 全プロファイル)",
    )
    parser.add_argument(
        "--install-guide",
        action="store_true",
        help="Floorpインストール手順を表示",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("エラー: このツールはWindows専用です。")
        print("Linux/macOSでは暗号化方式が異なるため、別途対応が必要です。")
        sys.exit(1)

    print("=" * 60)
    print("  Vivaldi → Floorp データ移行ツール")
    print("=" * 60)

    if args.install_guide:
        install_floorp_instructions()

    # エクスポート先
    if args.output:
        output_dir = Path(args.output)
    else:
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        output_dir = desktop / "vivaldi_export"
    output_dir.mkdir(parents=True, exist_ok=True)

    # プロファイル検出
    profiles = find_vivaldi_profiles()
    if not profiles:
        print("\nエラー: Vivaldiのプロファイルが見つかりません。")
        print("Vivaldiがインストールされているか確認してください。")
        sys.exit(1)

    print(f"\n検出されたプロファイル数: {len(profiles)}")

    for profile_dir, profile_name in profiles:
        if args.profile and profile_name != args.profile:
            continue

        print(f"\n--- プロファイル: {profile_name} ---")
        suffix = f"_{profile_name}" if len(profiles) > 1 else ""

        user_data_dir = profile_dir.parent
        try:
            master_key = get_vivaldi_master_key(user_data_dir)
        except Exception as e:
            print(f"  マスターキーの取得に失敗: {e}")
            print("  Vivaldiが起動中の場合は閉じてから再実行してください。")
            continue

        # パスワードエクスポート
        pw_path = output_dir / f"passwords{suffix}.csv"
        count = export_passwords(profile_dir, master_key, pw_path)
        print(f"  パスワード: {count} 件 → {pw_path.name}")

        if args.passwords_only:
            continue

        # ブックマーク
        bm_path = output_dir / f"bookmarks{suffix}.html"
        count = export_bookmarks(profile_dir, bm_path)
        print(f"  ブックマーク: {count} 件 → {bm_path.name}")

        # Cookie
        ck_path = output_dir / f"cookies{suffix}.json"
        count = export_cookies(profile_dir, master_key, ck_path)
        print(f"  Cookie: {count} 件 → {ck_path.name}")

        # 履歴
        hist_path = output_dir / f"history{suffix}.json"
        count = export_history(profile_dir, hist_path)
        print(f"  履歴: {count} 件 → {hist_path.name}")

    install_floorp_instructions()
    import_instructions(output_dir)


if __name__ == "__main__":
    main()
