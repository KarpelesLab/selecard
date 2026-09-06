# SeleCard

Tools to **read and reproduce** the wireless signal of **Bunka Shutter (文化シヤッター)
SeleCard** garage-shutter card remotes, using a [HackRF](https://github.com/greatscottgadgets/hackrf)
software-defined radio. Two generations are supported: **SeleCard II** (~315 MHz) and
**SeleCard III** (~426 MHz).

**Read this in: [English](#english) · [日本語](#日本語)**

> **No real device IDs appear anywhere in this repository.** Every example uses the
> obviously-synthetic card number `01234567`.

---

## English

### What is this?

A SeleCard is a credit-card–shaped remote that opens and closes a Bunka Shutter garage
shutter over radio. These remotes are **discontinued** and no longer sold or supported.
This project figured out how their radio signal works — purely by listening over the air,
with no manufacturer information — and provides small Python programs that can:

* **read** the signal from a recording (show the card's number, which button was pressed, and whether the checksum is valid), and
* **reproduce** the **OPEN / STOP / CLOSE** commands and transmit them.

It is published so owners can keep using or recover their unsupported equipment, and so
people understand that these remotes are **not secure**.

> ⚠️ **Security:** these remotes have essentially no protection. Anyone who knows the
> 8-digit number printed on your card can operate your shutter. Treat that number like a key.

### What you need

* A **HackRF One** software-defined radio, with an antenna.
* A **computer** with **Python 3** + **numpy**, and the **HackRF command-line tools** (`hackrf_transfer`, `hackrf_info`).
* The **8-digit number printed on your card**, to send commands as that card. (You can also read it off the air with the `decode` command below.)

### ⚠️ Safety and legality

Only transmit to a shutter **you own, or are clearly authorised to test**.

**In Japan**, reproducing these signals is generally legal without a licence **as long as
your transmitter stays within the "extremely low power" (微弱無線) limits of the Radio Act** —
the same exemption the original ≤ 1 mW remotes rely on. Compliance is defined by the
radiated field strength **measured at about 3 metres from the transmitter**, which must stay
below the legal limit for the band. A general-purpose SDR such as a HackRF can easily exceed
that, so keep its output low and, to be sure, verify by measuring at ~3 m. This is expected
to be the usual way people make use of this project.

Outside Japan, transmission on these bands may require a licence — follow your local rules.

> **This is not legal advice, and the law may have changed since this was published. You are
> responsible for verifying the current requirements in your jurisdiction.**

The authors accept no liability (see [`LICENSE`](LICENSE)).

### Which model do I have?

| | SeleCard II | SeleCard III |
|---|---|---|
| Model printed on the card | `STX9531C` | `STX0031` |
| Radio frequency | ~315 MHz | ~426 MHz |
| Program to use | [`selecard2.py`](selecard2.py) | [`selecard3.py`](selecard3.py) |
| Technical details | [`PROTOCOL2.md`](PROTOCOL2.md) | [`PROTOCOL3.md`](PROTOCOL3.md) |

### Reading a card (decode)

Record the remote with a HackRF while you hold a button, then decode the file. For a
**SeleCard III** (426 MHz):

```console
$ hackrf_transfer -r rec.cs8 -f 425900000 -s 2000000 -l 24 -g 20      # (press the button while this runs)
$ python3 selecard3.py decode rec.cs8
ID 01234567  OPEN   checksum OK
```

For a **SeleCard II** (315 MHz) use `-f 314800000` and `selecard2.py decode`; it also shows
the counter value (see below).

### Sending commands

`--id` is the 8-digit number printed on your card (leading zeros optional). The program
writes a `.cs8` clip; add `--tx` to actually transmit it.

**SeleCard III:**

```console
$ python3 selecard3.py --id 1234567 open  --tx
$ python3 selecard3.py --id 1234567 close --tx
```

**SeleCard II:**

```console
$ python3 selecard2.py --id 1234567 open  --tx
$ python3 selecard2.py --id 1234567 close --tx
```

`open`, `stop`, and `close` all work the same way.

### SeleCard II only: the counter (and how to recover a de-synced card)

The SeleCard III is *fixed-code*: the same button always sends the same signal, so it can
be copied and replayed directly.

The **SeleCard II is different** — each press carries a **counter** that must move *forward*.
The shutter remembers the last counter it accepted and only reacts to a **higher** one
(within a window of **255**). `selecard2.py` handles this for you: it remembers the counter
per card (in `~/.selecard2/`) and advances it on every transmission.

If your commands **stop being accepted**, the tool's counter has fallen behind the shutter.
Re-enter the window once with:

```console
$ python3 selecard2.py --id 1234567 resync --tx      # sweeps the counter 0..4095
```

**Real cards can de-sync the same way.** When you hold the button, the card's counter
advances at about **5.9 steps per second** (one frame every ~169 ms). If you press or hold
the card **out of radio range** of the shutter, its counter races ahead while the shutter's
memory stays put; once it is more than 255 ahead, the card is outside the window and
**stops working**. Because the counter is 12-bit (0–4095) and the check wraps around, you
recover a real card by bringing it **back in range and holding the button until the counter
wraps all the way around** and re-enters the window — worst case about **11 minutes** of
continuous holding, less if it drifted further. The simple advice: **don't press the card
when it's out of range of its shutter.**

### Transmitting with a YARD Stick One (instead of a HackRF)

Both SeleCards are narrowband — OOK (II) and 2-FSK (III) — so a
[YARD Stick One](https://greatscottgadgets.com/yardstickone/) (a small CC1111-based
sub-GHz transceiver) can transmit them **natively**, with no IQ synthesis. It covers both
bands (315 and 426 MHz), emits a clean ~+10 dBm carrier, and suits a fixed install. Add
`--radio yardstick`:

```console
$ pip install rfcat        # provides the rflib module
$ python3 selecard2.py --id 1234567 open --tx --radio yardstick
$ python3 selecard3.py --id 1234567 open --tx --radio yardstick
```

With more than one stick (e.g. one per band/location), select it with `--yardstick-index N`.
The decode / checksum / counter logic is unchanged — only the transmit backend differs
([`yardstick.py`](yardstick.py)).

> **Not yet hardware-tested.** The backend is written from the CC1111/RfCat interface and
> our measured parameters; two things to confirm on first use (each a one-line fix, noted in
> `yardstick.py`): the 2-FSK tone polarity, and that no stray hardware preamble is prepended.

### How it was worked out

Both signals were captured with a HackRF at 2 MS/s and demodulated in software; the card
number model was confirmed against the numbers printed on real cards, and the commands were
validated by operating real shutters with fully synthesised signals. The full write-ups are
in [`PROTOCOL2.md`](PROTOCOL2.md) / [`PROTOCOL2_ja.md`](PROTOCOL2_ja.md) (II) and
[`PROTOCOL3.md`](PROTOCOL3.md) / [`PROTOCOL3_ja.md`](PROTOCOL3_ja.md) (III).

### Status

* **SeleCard III** (STX0031, 426 MHz) — fully decoded; validated on a real shutter.
* **SeleCard II** (STX9531C, 315 MHz) — fully decoded; general checksum confirmed on
  multiple cards and the forward counter handled, validated on a real shutter.

### License

MIT © Karpeles Lab Inc — see [`LICENSE`](LICENSE).

---

## 日本語

### これは何ですか？

セレカードは、文化シヤッター製のガレージシャッターを電波で開閉する、カード型のリモコン
です。これらのリモコンは**製造終了**しており、現在は販売もサポートもされていません。
本プロジェクトは、その電波の仕組みを**電波を受信して解析するだけ**で（メーカー資料は
一切使わずに）解明し、次のことができる小さな Python プログラムを提供します。

* 録音した電波を**読み取る**（カード番号・押されたボタン・チェックサムの正否を表示）
* **OPEN（開）/ STOP（停止）/ CLOSE（閉）**コマンドを**合成して送信する**

サポート終了した機器を利用者が使い続けられるように、また、これらのリモコンが
**安全ではない**ことを理解してもらうために公開しています。

> ⚠️ **セキュリティについて：** これらのリモコンにはほとんど保護がありません。カードに
> 印刷された8桁の番号を知っている人なら誰でもシャッターを操作できます。この番号は
> **鍵と同じ**ものとして扱ってください。

### 必要なもの

* **HackRF One**（ソフトウェア無線）とアンテナ
* **Python 3** と **numpy**、および **HackRF のコマンドラインツール**（`hackrf_transfer`、`hackrf_info`）が入った**PC**
* コマンドをそのカードとして送るための、**カードに印刷された8桁の番号**（後述の `decode` で電波から読み取ることもできます）

### ⚠️ 安全と法律について

送信して良いのは、**自分が所有する、または明確に許可されたシャッターのみ**です。

**日本国内**では、これらの信号の再現は、送信機が電波法の**微弱無線**の基準内に収まって
いる限り、原則として免許不要で合法です（元の ≤ 1 mW リモコンが依拠しているのと同じ免除
規定です）。適合の判断は、**送信機からおよそ3メートルの距離で測定した**電界強度が、その
周波数帯の法定上限を下回っていることによります。HackRF のような汎用 SDR はこの上限を
容易に超え得るため、出力を十分に低く保ち、確実を期すには約3メートルで測定して確認して
ください。本プロジェクトは、これが一般的な利用方法になると想定しています。

日本国外では、これらの帯域での送信に免許が必要な場合があります。お住まいの地域の規則に
従ってください。

> **これは法的助言ではなく、公開後に法令が変わっている可能性があります。現行の要件は
> ご自身の管轄区域で確認する責任があります。**

作者は一切の責任を負いません（[`LICENSE`](LICENSE) を参照）。

### どちらのモデルですか？

| | セレカード II | セレカード III |
|---|---|---|
| カード記載の型番 | `STX9531C` | `STX0031` |
| 周波数 | 約315 MHz | 約426 MHz |
| 使うプログラム | [`selecard2.py`](selecard2.py) | [`selecard3.py`](selecard3.py) |
| 技術詳細 | [`PROTOCOL2_ja.md`](PROTOCOL2_ja.md) | [`PROTOCOL3_ja.md`](PROTOCOL3_ja.md) |

### カードを読み取る（decode）

ボタンを押しながら HackRF で録音し、そのファイルを解析します。**セレカード III**
（426 MHz）の場合：

```console
$ hackrf_transfer -r rec.cs8 -f 425900000 -s 2000000 -l 24 -g 20      # (実行中にボタンを押す)
$ python3 selecard3.py decode rec.cs8
ID 01234567  OPEN   checksum OK
```

**セレカード II**（315 MHz）の場合は `-f 314800000` と `selecard2.py decode` を使います。
こちらはカウンタ値も表示します（後述）。

### コマンドを送る

`--id` はカードに印刷された8桁の番号です（先頭のゼロは省略可）。プログラムは `.cs8`
ファイルを書き出します。実際に送信するには `--tx` を付けます。

**セレカード III：**

```console
$ python3 selecard3.py --id 1234567 open  --tx
$ python3 selecard3.py --id 1234567 close --tx
```

**セレカード II：**

```console
$ python3 selecard2.py --id 1234567 open  --tx
$ python3 selecard2.py --id 1234567 close --tx
```

`open`・`stop`・`close` はすべて同じように使えます。

### セレカード II のみ：カウンタと「同期ずれ」からの復旧

セレカード III は**固定コード**です。同じボタンは常に同じ信号を送るため、そのまま
コピーして再送できます。

**セレカード II は異なります**。各操作には、必ず**前に進む必要があるカウンタ**が
含まれます。シャッターは最後に受け付けたカウンタ値を記憶しており、それより**大きい**
値（**255 以内**の範囲）にしか反応しません。`selecard2.py` はこれを自動で処理し、
カードごとにカウンタを（`~/.selecard2/` に）記憶して送信のたびに進めます。

コマンドが**受け付けられなくなった**場合、ツール側のカウンタがシャッターより遅れて
しまっています。次のコマンドで一度だけ範囲内に入れ直してください：

```console
$ python3 selecard2.py --id 1234567 resync --tx      # カウンタを 0..4095 まで掃引します
```

**実物のカードでも同じように同期ずれが起きます。** ボタンを押し続けると、カードの
カウンタは**毎秒およそ 5.9 ずつ**（約169 msごとに1フレーム）進みます。シャッターの
**電波範囲外**でカードを押したり押し続けたりすると、カード側のカウンタだけが先に進み、
シャッターの記憶は止まったままになります。差が 255 を超えると、カードは範囲から外れて
**動かなくなります**。カウンタは12ビット（0〜4095）で判定も一周する（wrap する）ため、
実物のカードは**電波範囲内に戻し、カウンタが一周して範囲に再び入るまでボタンを押し続ける**
ことで復旧できます。最悪の場合、**約11分**の連続押しが必要です（ずれが大きいほど短く
なります）。要するに、**シャッターの電波範囲外ではカードを押さないこと**です。

### YARD Stick One で送信する（HackRF の代わりに）

セレカードはどちらも狭帯域（II は OOK、III は 2-FSK）なので、
[YARD Stick One](https://greatscottgadgets.com/yardstickone/)（CC1111 ベースの小型
サブ GHz トランシーバ）で、IQ 合成なしに**そのまま送信**できます。両方の帯域
（315・426 MHz）をカバーし、クリーンな約 +10 dBm の搬送波を出力するため、固定設置に
向いています。`--radio yardstick` を付けます：

```console
$ pip install rfcat        # rflib モジュールを提供します
$ python3 selecard2.py --id 1234567 open --tx --radio yardstick
$ python3 selecard3.py --id 1234567 open --tx --radio yardstick
```

複数のスティックがある場合（帯域／場所ごとに1本など）は `--yardstick-index N` で選びます。
デコード／チェックサム／カウンタのロジックは同じで、送信バックエンドだけが変わります
（[`yardstick.py`](yardstick.py)）。

> **未実機検証。** このバックエンドは CC1111/RfCat のインターフェースと実測パラメータから
> 記述したもので、初回に確認すべき点が2つあります（それぞれ一行で修正可能、`yardstick.py`
> に記載）：2-FSK のトーン極性と、余分なハードウェアプリアンブルが付かないこと。

### 解析方法について

どちらの信号も HackRF で 2 MS/s で録音し、ソフトウェアで復調しました。カード番号の
モデルは実物のカードに印刷された番号と照合して確認し、コマンドは完全に合成した信号で
実際のシャッターを操作して検証しました。詳細は [`PROTOCOL2_ja.md`](PROTOCOL2_ja.md)（II）
と [`PROTOCOL3_ja.md`](PROTOCOL3_ja.md)（III）にあります。

### 状態

* **セレカード III**（STX0031、426 MHz）— 完全に解読、実機で検証済み。
* **セレカード II**（STX9531C、315 MHz）— 完全に解読。汎用チェックサムを複数のカードで
  確認し、前進カウンタにも対応、実機で検証済み。

### ライセンス

MIT © Karpeles Lab Inc — [`LICENSE`](LICENSE) を参照。
