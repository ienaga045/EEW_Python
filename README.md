# JMA 日本列島 震度・緊急地震速報モニター

気象庁の地震情報 JSON と、気象庁発表の緊急地震速報を JSON 化している YDITS VXSE43 API を定期取得し、日本列島上に震度を表示する Python/Tkinter アプリです。

最大加速度をリアルタイムに見たい場合は、別アプリ `accel_monitor.py` を使います。こちらは NIED 強震モニタの最大加速度画像をそのまま表示します。

## 起動

```bash
python3 app.py
```

追加ライブラリは不要です。標準ライブラリのみで動作します。

最大加速度ビューア:

```bash
python3 accel_monitor.py
```

## 使っているデータ

- 地震情報一覧: `https://www.jma.go.jp/bosai/quake/data/list.json`
- 地震情報詳細: 一覧の `json` フィールドから `https://www.jma.go.jp/bosai/quake/data/{filename}` を取得
- 緊急地震速報: `https://api.ydits.net/vxse43`
- 最大加速度: `http://www.kmoni.bosai.go.jp/webservice/server/pros/latest.json` と `http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/acmap_s/{YYYYMMDD}/{YYYYMMDDHHMMSS}.acmap_s.gif`

気象庁の公開 JSON は地震情報・震度情報であり、リアルタイムの生加速度波形ではありません。このアプリでは Gal 値は表示せず、震度だけを表示します。

`accel_monitor.py` は、防災科研/NIED 強震モニタの「最大加速度」画像をローカル画面に表示します。NIED の利用条件に沿うため、色から観測値を抽出したり、観測点ごとの数値として再配信したりする処理は入れていません。

最大加速度ビューアはグレー背景の小さめウィンドウで、日本列島の画像が収まるサイズにしています。画像内に強い揺れを示す目立つ色が一定数出た場合は、警告音を鳴らします。起動直後の最初のフレームでは鳴らさず、連続鳴動を避けるため20秒のクールダウンを入れています。

最大加速度ビューアでも YDITS VXSE43 API を2秒間隔で確認し、緊急地震速報を受信した場合は画面下部に震央・最大予測震度を表示して警告音を鳴らします。揺れ検知音と緊急地震速報音は別々にオン/オフできます。

緊急地震速報は、気象庁が公開している即時配信電文そのものを直接受信するには気象業務支援センター等の契約経路が必要なため、YDITS の VXSE43 JSON API を利用しています。YDITS は開発段階のベータ版で、配信品質は無保証とされています。

## 警告音

新しい地震情報が検知され、最大震度が画面右上のしきい値以上だった場合に警告音を鳴らします。緊急地震速報を受けた場合も警告音を鳴らします。

- macOS: `/System/Library/Sounds/Sosumi.aiff`
- Windows: `winsound.MessageBeep`
- その他: ターミナルベル

起動直後の既存データでは警告音を鳴らさず、起動後に新しく取得された地震情報から鳴動します。
