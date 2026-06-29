#!/usr/bin/env python3
"""
Apple Music プレイリスト作成ヘルパー

ローカルマシンで実行してください:
  python3 create_apple_music_playlist.py

iTunes Search API で各曲の Apple Music トラックIDを検索し、
Apple Music ショートカット用の JSON データを出力します。
"""

import urllib.request
import urllib.parse
import json
import time
import sys

SONGS = [
    ("SOUL'd OUT", "イルカ"),
    ("SOUL'd OUT", "Picana"),
    ("SOUL'd OUT", "ウェカピポ"),
    ("SOUL'd OUT", "To All Tha Dreamers"),
    ("SOUL'd OUT", "ALIVE"),
    ("SOUL'd OUT", "Dream Drive"),
    ("KICK THE CAN CREW", "アンバランス"),
    ("KICK THE CAN CREW", "マルシェ"),
    ("KICK THE CAN CREW", "地球ブルース"),
    ("KICK THE CAN CREW", "千%"),
    ("Creepy Nuts", "のびしろ"),
    ("Creepy Nuts", "Bling-Bang-Bang-Born"),
    ("Creepy Nuts", "よふかしのうた"),
    ("Creepy Nuts", "二度寝"),
    ("Creepy Nuts", "かつて天才だった俺たちへ"),
    ("m-flo", "come again"),
    ("m-flo", "Astrosexy"),
    ("m-flo", "been so long"),
    ("RIP SLYME", "楽園ベイベー"),
    ("RIP SLYME", "Hot chocolate"),
    ("RIP SLYME", "STEPPER'S DELIGHT"),
    ("KREVA", "イッサイガッサイ"),
    ("KREVA", "音色"),
    ("DOUBLE", "Shake"),
    ("AI", "Story"),
    ("加藤ミリヤ 清水翔太", "Love Forever"),
    ("清水翔太", "My Boo"),
    ("久保田利伸", "LA・LA・LA LOVE SONG"),
    ("MISIA", "つつみ込むように"),
    ("Nujabes Shing02", "Luv(sic) pt.3"),
    ("Nujabes", "Aruarian Dance"),
    ("MONDO GROSSO", "ラビリンス"),
    ("MONDO GROSSO", "TIME"),
    ("宇多田ヒカル", "traveling"),
    ("Crystal Kay", "恋におちたら"),
    ("三浦大知", "(RE)PLAY"),
    ("SKY-HI", "Limo"),
    ("AK-69", "Flying B"),
    ("AKLO", "RED PILL"),
    ("Awich", "Queendom"),
    ("JP THE WAVY", "Cho Wavy De Gomenne"),
    ("BAD HOP", "Kawasaki Drift"),
    ("SIRUP", "LOOP"),
    ("SIRUP", "Do Well"),
    ("Vaundy", "踊り子"),
    ("BENI", "Kiss Kiss Kiss"),
    ("Awich", "GILA GILA"),
    ("KANDYTOWN", "Cruisin'"),
    ("LEX", "どこにもいない"),
    ("iri", "Wonderland"),
]


def search_itunes(artist, track):
    query = f"{artist} {track}"
    params = urllib.parse.urlencode({
        "term": query,
        "country": "JP",
        "media": "music",
        "entity": "song",
        "limit": 5,
    })
    url = f"https://itunes.apple.com/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "PlaylistBuilder/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None, str(e)

    if data.get("resultCount", 0) == 0:
        return None, "no results"

    for result in data["results"]:
        artist_lower = artist.lower().replace("’", "'").replace("'", "'")
        result_artist = result.get("artistName", "").lower().replace("’", "'").replace("'", "'")
        if artist_lower in result_artist or result_artist in artist_lower:
            return result, None

    return data["results"][0], None


def main():
    found = []
    not_found = []

    total = len(SONGS)
    print(f"\n{'='*60}")
    print(f"  Summer Vibes 2026 - Apple Music プレイリスト作成中...")
    print(f"  {total} 曲を iTunes Search API で検索します")
    print(f"{'='*60}\n")

    for i, (artist, track) in enumerate(SONGS, 1):
        result, err = search_itunes(artist, track)
        if result:
            found.append({
                "num": i,
                "searchArtist": artist,
                "searchTrack": track,
                "trackId": result["trackId"],
                "trackName": result["trackName"],
                "artistName": result["artistName"],
                "trackViewUrl": result.get("trackViewUrl", ""),
                "collectionName": result.get("collectionName", ""),
            })
            print(f"  [{i:2d}/{total}] ✅ {artist} - {track}")
            print(f"           → {result['artistName']} - {result['trackName']}")
        else:
            not_found.append({"num": i, "artist": artist, "track": track, "error": err})
            print(f"  [{i:2d}/{total}] ❌ {artist} - {track} ({err})")

        time.sleep(0.4)

    print(f"\n{'='*60}")
    print(f"  検索完了: {len(found)}/{total} 曲が見つかりました")
    print(f"{'='*60}\n")

    if not_found:
        print("--- 見つからなかった曲 ---")
        for item in not_found:
            print(f"  {item['num']}. {item['artist']} - {item['track']}")
        print()

    # Save links
    with open("summer_vibes_2026_links.md", "w") as f:
        f.write("# Summer Vibes 2026 - Apple Music Links\n\n")
        for item in found:
            f.write(f"{item['num']}. [{item['artistName']} - {item['trackName']}]({item['trackViewUrl']})\n")
    print("📄 summer_vibes_2026_links.md に保存しました")

    # Save JSON for Shortcuts
    track_ids = [item["trackId"] for item in found]
    with open("summer_vibes_2026_tracks.json", "w") as f:
        json.dump({
            "playlistName": "Summer Vibes 2026",
            "trackIds": track_ids,
            "tracks": [
                {"id": t["trackId"], "name": t["trackName"], "artist": t["artistName"]}
                for t in found
            ],
        }, f, ensure_ascii=False, indent=2)
    print("📄 summer_vibes_2026_tracks.json に保存しました")

    # Generate AppleScript for macOS Music app
    applescript = generate_applescript(found)
    with open("create_playlist.applescript", "w") as f:
        f.write(applescript)
    print("📄 create_playlist.applescript に保存しました")
    print()
    print("  macOS で Music.app にプレイリストを作成するには:")
    print("    osascript create_playlist.applescript")
    print()


def generate_applescript(found):
    search_commands = ""
    for item in found:
        safe_name = item["trackName"].replace('"', '\\"')
        safe_artist = item["artistName"].replace('"', '\\"')
        search_commands += f'''
    -- {item['num']}. {safe_artist} - {safe_name}
    set searchResult to (search playlist "ライブラリ" for "{safe_artist} {safe_name}")
    if (count of searchResult) > 0 then
        duplicate (item 1 of searchResult) to thePlaylist
    end if
'''

    return f'''tell application "Music"
    set thePlaylist to (make new playlist with properties {{name:"Summer Vibes 2026"}})
{search_commands}
end tell

display dialog "プレイリスト「Summer Vibes 2026」を作成しました！" buttons {{"OK"}} default button "OK"
'''


if __name__ == "__main__":
    main()
