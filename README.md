# Drum transcription · Local prototype

Ứng dụng chạy trên PC để kiểm chứng chất lượng **audio → sự kiện trống → MIDI → MusicXML → PDF**. Suy luận AI chạy local; không gửi bản nhạc đến dịch vụ/API bên ngoài. Tải dependency/model là bước cài đặt riêng có chủ đích.

> Phiên bản 0.3 thêm bản đồ số chỉ nhịp theo từng ô, nhóm phách lẻ và tempo thay đổi. Tempo/phách 1 vẫn tự nhận diện bằng model local; số chỉ nhịp là gợi ý cần nghe kiểm tra. Demo chỉ kiểm tra ký âm, không đại diện độ chính xác AI.

[Trạng thái triển khai và những gì đã kiểm tra](docs/IMPLEMENTATION_STATUS.md) phân biệt các luồng đã chạy thật với những phần còn cần xác nhận trên PC.

## Chạy nhanh trên Windows 11 + WSL / Linux

Cần **Python 3.11 hoặc 3.12**, Git và FFmpeg. Làm các bước trong cùng một môi trường: WSL hoặc Windows; không dùng chung thư mục `.venv` giữa hai hệ điều hành.

```bash
git clone https://github.com/sonvhse06072/Drum-transcription.git
cd Drum-transcription
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[adtof,beat]'
drum-transcription setup-models adtof
drum-transcription setup-models beat_this
drum-transcription doctor
drum-transcription serve
```

Lệnh trên chọn **CPU** để bắt đầu kiểm chứng với môi trường đã chạy thử. Nếu dùng NVIDIA GPU, thay dòng cài Torch/TorchAudio bằng cặp CUDA phù hợp ở mục dưới trước khi cài extra.

Mở **http://127.0.0.1:8765**. Lần đầu nên dùng clip **30–60 giây** và giữ chế độ **Tự nhận diện tempo & phách 1**. Không cần nhập BPM hoặc thời điểm phách đầu. App phân tích audio đã cắt, giữ mốc có phần lẻ của giây và hiển thị cả mốc trong clip lẫn mốc trong file tải lên. BPM trong app luôn là số **nốt đen/phút**, kể cả khi chọn nhịp 6/8.

Đã cài v0.2: dừng server, chạy `git pull --ff-only`, kích hoạt môi trường cũ, rồi `python -m pip install -e .` và `drum-transcription serve`. Không cần tải lại model cho v0.3. Chạy lại clip cũ để nhận bản đồ từng ô; kết quả đã lưu vẫn đọc được.

Trên Ubuntu/WSL, cài FFmpeg bằng `sudo apt install ffmpeg`. Trên Windows PowerShell, tạo môi trường bằng `py -3.12 -m venv .venv`, kích hoạt bằng `.venv\Scripts\Activate.ps1`, các lệnh `python -m pip` và `drum-transcription` giữ nguyên. Cài FFmpeg rồi thêm thư mục chứa `ffmpeg.exe` vào PATH.

### CPU hoặc NVIDIA GPU

ADTOF cần PyTorch. Nếu chỉ thử CPU và muốn tránh tải các gói CUDA lớn, **cài PyTorch CPU trước lệnh cài extra adtof**:

```bash
python -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[adtof,beat]'
```

Nếu có NVIDIA GPU, cài cặp **PyTorch/TorchAudio 2.8.0 cùng build CUDA**, phù hợp driver của máy, theo [hướng dẫn phiên bản chính thức](https://pytorch.org/get-started/previous-versions/). Tránh trộn Torch CPU với TorchAudio CUDA vì có thể thiếu thư viện khi import. Trong app chọn `auto` hoặc `cuda`; không tự chuyển sang API cloud khi GPU thiếu bộ nhớ.

### Chưa cài AI: kiểm tra giao diện và sheet trước

```bash
python -m pip install -e .
drum-transcription serve
```

Nút **Demo ký âm** xuất một groove đã biết trước để kiểm tra MusicXML, MIDI và âm thanh tổng hợp. Nếu chưa có model, nút nhận diện audio thật sẽ không chạy. CLI tương đương:

```bash
drum-transcription demo --output-dir data/demo
```

## Mở và xuất sheet bằng MuseScore

MusicXML luôn được xuất và có thể mở/chỉnh trong [MuseScore Studio](https://musescore.org/). Để app tự xuất PDF, cài MuseScore trên **cùng môi trường** và đặt đường dẫn chương trình khi app không tìm được trong PATH:

```bash
export MUSESCORE_BIN=/usr/bin/mscore
drum-transcription serve
```

PowerShell:

```powershell
$env:MUSESCORE_BIN = 'C:\Program Files\MuseScore 4\bin\MuseScore4.exe'
drum-transcription serve
```

Nếu app chạy WSL nhưng MuseScore chỉ cài trên Windows, cách kiểm chứng nhanh nhất là tải MusicXML rồi mở bằng MuseScore Windows. PDF tự động cần chương trình MuseScore local nhận được đường dẫn của môi trường đang chạy app.

## Luồng kiểm chứng chất lượng

1. Chọn file nhạc hoặc drum stem; đặt khoảng bắt đầu và độ dài clip.
2. Chọn ADTOF cho baseline 5 nhóm; dùng ADT_STR khi cần đối chiếu nhiều loại trống hơn.
3. Giữ chế độ nhận diện nhịp tự động. Có thể gợi ý số chỉ nhịp **đầu đoạn** hoặc đổi grid; BPM và phách 1 được suy ra từ audio. Gợi ý đầu đoạn không khóa số chỉ nhịp của các đoạn sau.
4. Nghe clip gốc, bản có click nhịp tự động, drum stem nếu có và âm thanh trống tổng hợp từ kết quả. Click mạnh đánh dấu phách 1 mà model tìm được.
5. Kiểm tra **Bản đồ ô nhịp**: thời gian, số chỉ nhịp, nhóm phách và tempo của từng ô. Chỗ đổi cách đếm, marker nội suy hoặc đuôi clip dự kiến cần nghe kỹ.
6. Nếu cần, chỉnh số chỉ nhịp/nhóm phách của một ô hoặc một dải ô rồi xuất lại; không chạy AI lại. Thao tác giữ các mốc beat/downbeat, thay cách ký âm. Nhóm phách như `2+2+3` thay cách nối móc; không di chuyển marker âm thanh.
7. Tải MIDI gốc, MIDI theo sheet, MusicXML và PDF nếu MuseScore sẵn sàng. Chế độ xuất lại bằng một BPM/meter/phách đầu cố định vẫn có riêng để ghi đè thủ công.

`events.json` giữ timestamp và kết quả nhận diện ban đầu. `raw.mid` giữ timing biểu diễn. `rhythm.json` lưu bản đồ đang dùng, gồm beat/downbeat, từng ô, nguồn suy đoán và lịch sử chỉnh meter. MusicXML và MIDI theo sheet dùng lưới beat, tempo map và số chỉ nhịp theo từng đoạn. BPM tổng quan là trung vị; tempo từng ô có thể khác. Dao động tempo nhỏ được giữ cho playback, không in đầy nhãn trên sheet. Độ dài nốt trên sheet là cách biểu diễn tiết tấu, không phải thời gian ngân của cymbal. Các cú đánh trước phách 1 vẫn được giữ trong phần lấy đà hoặc các ô nhịp trước đó.

Nhịp lẻ có hai phần khác nhau: **ghi được đúng ô nhịp** và **nghe ra cách đếm đúng**. App hỗ trợ ký âm tử số 1–32 với mẫu số 2/4/8/16, gồm `5/4`, `5/8`, `7/8`, `11/8`, `13/16`. Model Beat This! chỉ trả mốc beat/downbeat; bước hậu xử lý gợi ý số chỉ nhịp từng ô và nhóm 2/3 khi tỷ lệ thời gian lặp lại. Các pulse đều không xác định chắc chắn mẫu số, nên có thể gợi `5/4` trong khi người chơi muốn ghi `5/8`. Không coi gợi ý này là nhãn đã xác nhận.

Ví dụ: chọn đoạn từ **1,5 giây** trong file tải lên, model tìm phách 1 ở **0,372 giây** trong clip thì mốc tương ứng là **1,872 giây** trong file. App không làm tròn thành giây nguyên. Đây là ví dụ phép quy đổi mốc, không phải cam kết sai số model bằng 1 ms. Xem [nhận diện nhịp tự động](docs/RHYTHM.md).

**Kiểm tra đúng ba loại lỗi:** thiếu/thừa/nhầm cú đánh; lệch nhịp hoặc trường độ; ký âm khó đọc. Ghi thời gian chỉnh tay trên cùng một clip để quyết định model có giúp tiết kiệm công sức hay không. [Kịch bản kiểm chứng](docs/VALIDATION.md) có mẫu ghi kết quả.

## Mô hình và tách trống

| Thành phần | Trạng thái / mục đích |
|---|---|
| Beat This! | Tự phát hiện beat/downbeat, suy ra BPM và gợi ý số chỉ nhịp trên audio gốc; checkpoint local riêng. |
| ADTOF-pytorch | Adapter local cho 5 nhóm kick/snare/toms/hi-hat/cymbals; baseline nhẹ. Chưa tách từng tom hoặc trạng thái hi-hat. |
| ADT_STR | Adapter local có chuẩn hóa ID nhạc cụ riêng; cần extra và checkpoint riêng. Độ chính xác phải kiểm chứng trên nhạc của bạn. |
| Demucs | Tách drum stem tùy chọn với checkpoint local rõ ràng; không bắt buộc để thử ADTOF. |
| music21 + MuseScore | Xuất MusicXML và PDF. Việc hiển thị ký hiệu không có nghĩa AI đã nhận diện được kỹ thuật đó. |

Xem [cài model, phiên bản pin và giới hạn giấy phép](docs/MODELS.md). Mã nguồn/weights upstream không được chép vào repo này. Lưu ý: ADTOF gộp 5 nhóm; âm lượng, hi-hat mở/đóng, ghost note, flam và roll không được tự gán khi model không cung cấp bằng chứng.

```bash
# Engine nhiều lớp hơn; dung lượng lớn hơn baseline.
# CPU: cài cặp torch/torchaudio CPU như hướng dẫn phía trên trước bước này.
python -m pip install -e '.[adt-str,beat]'
drum-transcription setup-models adt_str
drum-transcription setup-models beat_this

# Tuỳ chọn tách drum stem.
python -m pip install -e '.[demucs]'
drum-transcription setup-models demucs
```

Sau khi chuẩn bị model, có thể tắt mạng để chạy app. Không tải model ngầm khi bấm nhận diện. JavaScript/CSS được phục vụ từ app, không lấy từ CDN. App mặc định chỉ lắng nghe `127.0.0.1`; chưa có đăng nhập hoặc phân quyền để public lên Internet.

## Giới hạn v0.3

- BPM và phách 1 được tự nhận diện, có thể chỉnh lại. Phách 1 đầu tiên **phát hiện được** có thể khác phách 1 thật nếu model bỏ sót; nghe lớp click để kiểm chứng. Nhịp kép, half/double-time và nhịp thay đổi giữa bài vẫn có thể gây nhầm.
- Số beat khác nhau giữa các ô không còn gây lỗi hoặc bị ép về một meter cho toàn clip. Tuy vậy, downbeat thừa/thiếu có thể trông giống đổi nhịp thật; các thay đổi tự suy ra được đánh dấu ước lượng. Nhóm phách dựa tỷ lệ 2/3 là heuristic có điều kiện, chưa phải model phân loại meter được đánh giá riêng.
- Chỉnh theo ô giữ nguyên ranh giới ô đã nhận diện; hiện chưa có thao tác gộp/tách ô hoặc kéo marker downbeat trong web. Nếu marker sai, có thể thử clip dài hơn hoặc sửa MusicXML trong MuseScore. Không tự diễn giải swing/polyrhythm.
- Tempo map đi theo beat grid, gồm cả sai số của detector. Lưới sai vẫn làm sheet sai; xuất được nhịp lẻ không đồng nghĩa nhận diện nhịp lẻ chính xác trên mọi bản nhạc.
- Nếu audio im lặng, quá ngắn hoặc beat/downbeat không hợp lệ, app báo chưa xác định được nhịp; không tự điền 120 BPM/phách 1 tại giây 0. Có thể thử đoạn dài hơn hoặc chủ động dùng chế độ thủ công.
- Grid nốt thẳng và chùm ba; hai voice cho tay/chân; ghi chú về cách xuất chi tiết ở [NOTATION.md](docs/NOTATION.md).
- Một tác vụ AI chạy tại một thời điểm. Mỗi clip tối đa 600 giây, upload tối đa 200 MB. Ưu tiên clip ngắn để kiểm chứng.
- Chưa có editor nốt trực tiếp trong web; dùng MuseScore sửa MusicXML. Chưa huấn luyện/fine-tune model riêng.
- Âm thanh so sánh là trống **tổng hợp đơn giản**, dùng nghe vị trí cú đánh; không phải soundfont giống bộ trống gốc.
- Các ngưỡng và model chưa được đánh giá trên một bộ nhạc có nhãn của người dùng; không có con số accuracy được quảng cáo.

## Phát triển

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

Source:

```text
src/drum_transcription/
  app.py              # FastAPI, local files and API
  jobs.py             # Single-worker queue and durable results
  audio.py            # FFmpeg clip decode and local Demucs
  rhythm.py           # Local Beat This!, tempo/downbeat grid and audible clicks
  rhythm_edit.py      # Review per-bar meter/grouping, retaining detected times
  engines/            # Local ADTOF / ADT_STR adapters and setup
  domain.py           # Raw event and score contracts
  notation.py         # Quantization, MusicXML, MIDI, optional PDF
  preview_audio.py    # Synthetic timing preview
  static/             # HTML/CSS/JS, no CDN or frontend build
```

`data/`, `models/`, audio người dùng và kết quả không được commit. Biến môi trường `DRUM_DATA_DIR`, `DRUM_MODELS_DIR`, `FFMPEG_BIN`, `MUSESCORE_BIN` cho phép đổi đường dẫn; xem [.env.example](.env.example). Không tự nạp `.env`: đặt biến trong terminal trước khi chạy.


## V0.4: mixed-meter review when the model misses barlines

Beat This! remains the automatic default. On a result with a wrong bar count,
expand **Chỉnh số chỉ nhịp và ranh giới ô nhịp** and use the second, advanced
form. Enter **N+1 semicolon-separated barline timestamps** for **N meter labels**
relative to the decoded clip (fractional seconds allowed). Example:

```text
Barlines: 0.371; 2.371; 3.871; 5.121
Meters:   4/4; 3/4; 5/8
```

This creates a clearly labeled **manual correction**, retaining original model
beat/downbeat evidence and every raw drum event. The initial automatically
detected map is never overwritten. A short 5/16 bar can use fractional boundaries
between beat markers. The first and last user boundaries must cover the detected
audio region; invalid/overlapping marks are rejected.

[Real-recording benchmark](docs/REAL_MUSIC_VALIDATION.md): the new model-score
postprocessor reduced Room 34 from 18 to 12 candidate bars, but the composer
specified 7. The automatic odd-meter result remains **NOT ACCEPTED**, even
though the export and unit tests run. This is why barline editing is offered
as a fallback, not counted as automatic recognition accuracy.


## New (v0.5): optional SheetSage2-Refined for odd/changing meters

Beat This! is still the default. For an **experimental** rhythm engine that
predicts a time signature for each bar, install the code and prepare the
non-commercial weights explicitly:

```bash
python -m pip install -e '.[adtof,meter-aware]'
drum-transcription setup-models sheetsage2_refined
drum-transcription doctor
drum-transcription serve
```

Select **SheetSage2-Refined · nhịp lẻ (thử nghiệm)** in
**Tempo & phách đầu → Mô hình nhận diện nhịp**. Choose the drum model separately
(ADTOF or ADT_STR). The user does **not** enter BPM or a downbeat by default.

The model is local-only during inference. The one-time setup resolves the
publisher's weight revision and stores only the encoder and bar head artifacts,
with checksums. The model weights are **CC BY-NC 4.0**, so this feature is
intended for personal/non-commercial research until a separate license review.
See [installation, provenance and limitations](docs/SHEETSAGE2.md).

**Note:** integration passing tests does not mean automatic recognition of
5/8, 7/8 and meter transitions has passed on real music. Compare against the
known-composer [Room 34 reference](docs/REAL_MUSIC_VALIDATION.md) and annotated
audio before deciding whether to change the default.
