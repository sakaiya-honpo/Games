/*
 * place_mouth.jsx  —  発話区間に口パク mp4 を自動配置する ExtendScript
 *
 * 前提の構成:
 *   ・下のビデオトラックに「閉じ口の立ち絵（静止画）」を常時表示
 *   ・その上のトラックに、喋っている区間だけ口パク mp4 を敷き詰める
 *   ・口パク mp4 は短いループ。区間の長さに合わせて自動で繰り返し配置する
 *
 * 使い方:
 *   1. 口パク mp4 を「プロジェクト」パネルに読み込んでおく
 *   2. アクティブなシーケンスに、口パクを乗せる空のビデオトラックを用意
 *   3. detect_speech.py が出力した *_mouth.json を用意
 *   4. 下の CONFIG を必要に応じて変更し、このスクリプトを実行
 *      （実行方法は README を参照。CEP パネルのボタン、または
 *       VS Code の "ExtendScript Debugger" から）
 *
 * ※ Premiere の ExtendScript API はバージョン差があるため、
 *   うまくトリミングされない場合は下の MEDIA_TYPE を 1 に変えて再実行。
 */

#target premierepro

(function () {
    // ===== CONFIG =====================================================
    var CONFIG = {
        // 口パク mp4 のプロジェクトアイテム名（プロジェクトパネルでの表示名）
        flapClipName: "kuchipaku",

        // 口パク mp4 の長さ（秒）。手元のループ動画の尺を入れる。
        loopSeconds: 1.0,

        // 配置先のビデオトラック番号（1始まり。例: V3 なら 3）
        videoTrackNumber: 3,

        // JSON をファイル選択ダイアログで選ぶ。false にすると jsonPath を使う。
        pickJsonDialog: true,
        jsonPath: "",

        // 配置前にトラック上の該当範囲を上書きする（overwrite）。
        // 立ち絵とは別トラックなので通常 true のままで良い。
        overwrite: true,

        // トリミングに使う mediaType。うまく尺が合わないときは 1 に変更。
        MEDIA_TYPE: 4
    };
    // ==================================================================

    function err(msg) {
        alert("[口パク配置] " + msg);
    }

    var proj = app.project;
    if (!proj) { err("プロジェクトが開かれていません。"); return; }

    var seq = proj.activeSequence;
    if (!seq) { err("アクティブなシーケンスがありません。"); return; }

    // --- JSON を読む -------------------------------------------------
    var jsonFile;
    if (CONFIG.pickJsonDialog) {
        jsonFile = File.openDialog("発話区間 JSON (*_mouth.json) を選択", "*.json");
        if (!jsonFile) { return; } // キャンセル
    } else {
        jsonFile = new File(CONFIG.jsonPath);
    }
    if (!jsonFile.exists) { err("JSON が見つかりません: " + jsonFile.fsName); return; }

    jsonFile.encoding = "UTF-8";
    jsonFile.open("r");
    var text = jsonFile.read();
    jsonFile.close();

    var data;
    try {
        data = eval("(" + text + ")"); // ExtendScript には JSON が無いため eval で読む
    } catch (e) {
        err("JSON の解析に失敗しました: " + e.toString());
        return;
    }
    var segments = data.segments || [];
    if (!segments.length) { err("区間が 0 件です。"); return; }

    // --- 口パククリップを探す ---------------------------------------
    function findItem(root, name) {
        for (var i = 0; i < root.children.numItems; i++) {
            var it = root.children[i];
            if (it.type === ProjectItemType.BIN) {
                var found = findItem(it, name);
                if (found) { return found; }
            } else if (it.name === name) {
                return it;
            }
        }
        return null;
    }
    var flap = findItem(proj.rootItem, CONFIG.flapClipName);
    if (!flap) {
        err("口パククリップ '" + CONFIG.flapClipName + "' がプロジェクトに見つかりません。\n"
            + "先にプロジェクトパネルへ読み込み、CONFIG.flapClipName を合わせてください。");
        return;
    }

    // --- 配置先トラック ---------------------------------------------
    var trackIndex = CONFIG.videoTrackNumber - 1;
    if (trackIndex < 0 || trackIndex >= seq.videoTracks.numTracks) {
        err("ビデオトラック V" + CONFIG.videoTrackNumber + " がありません。\n"
            + "先に空のトラックを追加してください。");
        return;
    }
    var track = seq.videoTracks[trackIndex];

    // --- 配置 -------------------------------------------------------
    // overwriteClip はプロジェクトアイテムの in/out を使うので、
    // フル尺コピーと端数コピーで out を切り替えて敷き詰める。
    var loop = CONFIG.loopSeconds;
    var placed = 0;

    function setInOut(inSec, outSec) {
        try {
            flap.setInPoint(inSec, CONFIG.MEDIA_TYPE);
            flap.setOutPoint(outSec, CONFIG.MEDIA_TYPE);
        } catch (e) {
            // API 差異でここが失敗する場合は端数トリムが効かないだけで、
            // フル尺配置は継続する。
        }
    }

    function placeAt(timeSec, durSec) {
        setInOut(0, durSec);
        // overwriteClip(projectItem, timeInSeconds)
        track.overwriteClip(flap, timeSec);
        placed++;
    }

    for (var s = 0; s < segments.length; s++) {
        var start = segments[s].start;
        var end = segments[s].end;
        var len = end - start;
        if (len <= 0) { continue; }

        var t = start;
        // フル尺ループを敷く
        while (len - (t - start) >= loop) {
            placeAt(t, loop);
            t += loop;
        }
        // 端数
        var rem = end - t;
        if (rem > 0.02) { // 20ms 未満の端数は無視
            placeAt(t, rem);
        }
    }

    // in/out を元（フル尺）に戻しておく
    setInOut(0, loop);

    alert("[口パク配置] 完了: " + segments.length + " 区間 / " + placed + " クリップを V"
        + CONFIG.videoTrackNumber + " に配置しました。");
})();
