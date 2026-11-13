# Drum transcription · Local prototype

Ứng dụng chạy trên PC để kiểm chứng chất lượng **audio → sự kiện trống → MIDI → MusicXML → PDF**. Suy luận AI chạy local; không gửi bản nhạc đến dịch vụ/API bên ngoài. Tải dependency/model là bước cài đặt riêng có chủ đích.

> Phiên bản 0.1 phục vụ thử chất lượng và chỉnh sheet. Chưa cam kết nhận diện đầy đủ mọi kỹ thuật trống. Demo chỉ kiểm tra ký âm, không đại diện độ chính xác AI.

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
python -m pip install -e '.[adtof]'
drum-transcription setup-models adtof
drum-transcription doctor
drum-transcription serve
```

Lệnh trên chọn **CPU** để bắt đầu kiểm chứng với môi trường đã chạy thử. Nếu dùng NVIDIA GPU, thay dòng cài Torch/TorchAudio bằng cặp CUDA phù hợp ở mục dưới trước khi cài extra.

Mở **http://127.0.0.1:8765**. Lần đầu nên dùng clip **30–60 giây**, xác nhận BPM, số chỉ nhịp và thời điểm phách đầu trong clip. BPM trong app luôn là số **nốt đen/phút**, kể cả 6/8.

Trên Ubuntu/WSL, cài FFmpeg bằng `sudo apt install ffmpeg`. Trên Windows PowerShell, tạo môi trường bằng `py -3.12 -m venv .venv`, kích hoạt bằng `.venv\Scripts\Activate.ps1`, các lệnh `python -m pip` và `drum-transcription` giữ nguyên. Cài FFmpeg rồi thêm thư mục chứa `ffmpeg.exe` vào PATH.

### CPU hoặc NVIDIA GPU

ADTOF cần PyTorch. Nếu chỉ thử CPU và muốn tránh tải các gói CUDA lớn, **cài PyTorch CPU trước lệnh cài extra adtof**:

```bash
python -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[adtof]'
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
3. Nhập BPM, số chỉ nhịp, grid và vị trí phách đầu **tính từ đầu clip**.
4. Nghe clip gốc, drum stem nếu có, và âm thanh trống tổng hợp từ kết quả.
5. Xem sự kiện gốc, tải MIDI gốc, MIDI đã căn nhịp, MusicXML và PDF nếu MuseScore sẵn sàng.
6. Chỉnh BPM/grid rồi **xuất lại sheet**. App tạo job mới từ sự kiện đã nhận diện; không chạy AI lại.

`events.json` giữ timestamp gốc. `raw.mid` giữ timing biểu diễn. MIDI của score và MusicXML là dữ liệu đã căn nhịp. Độ dài nốt trên sheet là cách biểu diễn tiết tấu, không phải thời gian ngân của cymbal. Thời điểm trước phách đầu có thể bị loại khỏi sheet và sẽ có cảnh báo; audio và sự kiện gốc vẫn được giữ.

**Kiểm tra đúng ba loại lỗi:** thiếu/thừa/nhầm cú đánh; lệch nhịp hoặc trường độ; ký âm khó đọc. Ghi thời gian chỉnh tay trên cùng một clip để quyết định model có giúp tiết kiệm công sức hay không. [Kịch bản kiểm chứng](docs/VALIDATION.md) có mẫu ghi kết quả.

## Mô hình và tách trống

| Thành phần | Trạng thái / mục đích |
|---|---|
| ADTOF-pytorch | Adapter local cho 5 nhóm kick/snare/toms/hi-hat/cymbals; baseline nhẹ. Chưa tách từng tom hoặc trạng thái hi-hat. |
| ADT_STR | Adapter local có chuẩn hóa ID nhạc cụ riêng; cần extra và checkpoint riêng. Độ chính xác phải kiểm chứng trên nhạc của bạn. |
| Demucs | Tách drum stem tùy chọn với checkpoint local rõ ràng; không bắt buộc để thử ADTOF. |
| music21 + MuseScore | Xuất MusicXML và PDF. Việc hiển thị ký hiệu không có nghĩa AI đã nhận diện được kỹ thuật đó. |

Xem [cài model, phiên bản pin và giới hạn giấy phép](docs/MODELS.md). Mã nguồn/weights upstream không được chép vào repo này. Lưu ý: ADTOF gộp 5 nhóm; âm lượng, hi-hat mở/đóng, ghost note, flam và roll không được tự gán khi model không cung cấp bằng chứng.

```bash
# Engine nhiều lớp hơn; dung lượng lớn hơn baseline.
# CPU: cài cặp torch/torchaudio CPU như hướng dẫn phía trên trước bước này.
python -m pip install -e '.[adt-str]'
drum-transcription setup-models adt_str

# Tuỳ chọn tách drum stem.
python -m pip install -e '.[demucs]'
drum-transcription setup-models demucs
```

Sau khi chuẩn bị model, có thể tắt mạng để chạy app. Không tải model ngầm khi bấm nhận diện. JavaScript/CSS được phục vụ từ app, không lấy từ CDN. App mặc định chỉ lắng nghe `127.0.0.1`; chưa có đăng nhập hoặc phân quyền để public lên Internet.

## Giới hạn v0.1

- BPM, nhịp và phách đầu do người dùng xác nhận. Chưa có tempo map tự động, nhịp thay đổi giữa bài, rubato hoặc swing tự động.
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
  engines/            # Local ADTOF / ADT_STR adapters and setup
  domain.py           # Raw event and score contracts
  notation.py         # Quantization, MusicXML, MIDI, optional PDF
  preview_audio.py    # Synthetic timing preview
  static/             # HTML/CSS/JS, no CDN or frontend build
```

`data/`, `models/`, audio người dùng và kết quả không được commit. Biến môi trường `DRUM_DATA_DIR`, `DRUM_MODELS_DIR`, `FFMPEG_BIN`, `MUSESCORE_BIN` cho phép đổi đường dẫn; xem [.env.example](.env.example). Không tự nạp `.env`: đặt biến trong terminal trước khi chạy.
