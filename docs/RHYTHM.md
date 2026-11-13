# Tự nhận diện tempo và phách 1

App phân tích **beat và downbeat trên audio local** bằng Beat This! trước khi căn
nhịp drum sheet. Không lấy cú đánh đầu tiên hoặc giây 0 làm phách 1. BPM và vị trí
phách 1 là kết quả phân tích; chỉnh thủ công là tuỳ chọn để sửa một kết quả chưa đúng.

## Cài model

Trong virtualenv của app, cài cặp PyTorch/TorchAudio cùng build. Ví dụ CPU:

```bash
python -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[beat]'
drum-transcription setup-models beat_this
```

Source được pin tại
[CPJKU/beat_this · b95c8ab0](https://github.com/CPJKU/beat_this/tree/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c).
Checkpoint `final0` chính thức nằm ở `models/beat_this/final0.ckpt`; manifest ghi
nguồn tải, revision và SHA-256. Code và checkpoint được tác giả công bố dưới MIT.

Chỉ lệnh setup được tải checkpoint. Suy luận dùng file local đã mở, nên không
kích hoạt đường tự tải model theo tên của thư viện upstream. Checkpoint được
đọc bằng `weights_only=True` và model nạp state dict đầy đủ. Không dùng madmom/DBN,
API ngoài, dịch vụ nhận diện hoặc JavaScript từ CDN.

## Hai mốc thời gian khác nhau

- **Mốc trong clip**: thời gian tính từ đầu đoạn audio đã decode/cắt, luôn giữ phần
  lẻ của giây. Mốc phách 1 `1.52` không được làm tròn thành `1` hoặc `2`.
- **Mốc trong file upload**: cộng thời gian bắt đầu cắt clip vào mốc trong clip.
  Ví dụ cắt từ `1.5 s`, phách 1 được phát hiện tại `0.5 s` trong clip thì vị trí
  tương ứng trong file upload là `2.0 s`.

Nếu file upload vốn đã được cắt ở một phần mềm khác, app chỉ biết timeline của
file đó; nó không thể suy ra thời điểm trong một bản thu gốc chưa được cung cấp.

Beat This! dự đoán trên các frame khoảng **20 ms**. Giữ số thập phân giúp tránh
lỗi làm tròn giây, nhưng không biến model thành bộ đo chính xác đến từng mẫu audio.

## Cách tạo lưới ký âm

Model trả về hai danh sách thời điểm: các beat và các downbeat. App:

1. Kiểm tra thời gian tăng dần, nằm trong clip, có ít nhất bốn beat và hai downbeat.
   Marker ở đúng mép cuối hoặc tối đa một frame 20 ms trong phần đệm được loại riêng;
   giá trị nằm xa ngoài audio vẫn báo lỗi. Số marker loại được ghi trong metadata.
2. Ghép downbeat với beat gần nhất, trong giới hạn 60 ms. Upstream hiện đã thực
   hiện bước ghép; adapter kiểm tra lại để tránh lưới sai.
3. Xác định nhóm beat giữa các downbeat. Các khoảng nguyên bội của một nhóm đã
   quan sát có thể là downbeat bị bỏ sót; app ghi cảnh báo và giữ các beat có thật.
4. Tính tempo từ trung vị khoảng cách beat. BPM hiển thị luôn theo **nốt đen**.
5. Giữ toàn bộ timestamp beat để căn vị trí nốt; không dựng lại mọi beat bằng phép
   cộng một BPM đã làm tròn.

Vị trí âm nhạc quarter `0` ứng với **phách 1 đầu tiên được phát hiện**. Beat trước
mốc đó có vị trí quarter âm. Exporter dùng chúng để giữ phần lấy đà/đoạn bắt đầu
giữa ô nhịp, trong khi sự kiện và MIDI gốc giữ nguyên timestamp audio.

Nếu không có gợi ý meter, app dùng nhóm `n` beat làm gợi ý `n/4`. Cách đếm này
không chứng minh tuyệt đối số chỉ nhịp: hai pulse/ô có thể là 2/4 hoặc hai nốt đen
chấm trong 6/8. Khi chọn gợi ý 6/8 với hai pulse/ô, mỗi pulse tương ứng 1.5 nốt đen;
app chuyển BPM và vị trí quarter theo đúng tỷ lệ này. Half-time/double-time và
nhịp kép vẫn là những trường hợp cần nghe đối chiếu.

Gợi ý meter không được ép thành các pulse bất thường, ví dụ ba beat/ô sang 4/4
bằng cách tự gán mỗi pulse 4/3 nốt đen. Trường hợp không tương thích sẽ yêu cầu bỏ
gợi ý meter hoặc chủ động dùng chế độ thủ công.

Khoảng cách beat thay đổi đáng kể được ghi cảnh báo; BPM là giá trị đại diện của
đoạn nhạc. Grid bị đứt quãng hoặc số beat mỗi ô mâu thuẫn sẽ báo lỗi rõ ràng,
không thay bằng 120 BPM hoặc mặc định phách 1 ở 0 s.

## Nghe kết quả tự nhận diện

Audio đối chiếu nhịp trộn các click vào **chính clip đã xử lý**:

- Click thường đánh dấu beat.
- Click cao và rõ hơn đánh dấu downbeat được model phát hiện.
- Clip và phần im lặng đầu được giữ cùng timeline; không dịch audio về phách 1.

Model có thể dự đoán pulse xuyên qua đoạn im lặng, hoặc bỏ sót một downbeat. Vì
vậy tên hiển thị là *phách 1 đầu tiên phát hiện*, và app không công bố một tỷ lệ
“độ tin cậy” chưa được hiệu chỉnh. Nếu cách đếm nghe sai, thử clip dài/rõ nhịp hơn,
chọn gợi ý số chỉ nhịp hoặc dùng phần chỉnh lại.

## Kiểm tra đã thực hiện

Tests tập trung bao phủ offset `1.5 s`, dịch toàn bộ timeline thêm `0.371 s`,
phách đầu không phải beat đầu, meter 6/8, tempo thay đổi, thiếu downbeat marker,
nhóm nhịp mâu thuẫn, model bị thiếu, ngăn đường tải ngầm và click đúng vị trí mẫu.

Đã chạy **model thật trên CPU, chặn socket/DNS khi suy luận**, với một groove tổng
hợp 120 BPM dài khoảng 32 giây và các lượng im lặng thêm ở đầu:

| Dịch audio | Phách 1 model phát hiện | BPM | Meter gợi ý | Lần gọi mạng |
|---:|---:|---:|---|---:|
| 0 s | 0.02 s | 120 | 4/4 | 0 |
| 1.5 s | 1.52 s | 120 | 4/4 | 0 |
| 0.371 s | 0.38 s | 120 | 4/4 | 0 |

Môi trường: Python 3.12, Torch/TorchAudio 2.8.0 CPU, `beat-this` 1.1.0 tại revision
đã pin, hai Torch threads. Thời gian gồm khởi tạo/load model lần lượt là 4.956,
3.410 và 2.858 giây. Đây là kiểm tra thực thi và offset trên audio tổng hợp,
**không phải benchmark độ chính xác trên nhạc thực tế** hay cam kết hiệu năng PC.

Một phép thử đầy đủ đã encode groove sang MP3, cắt từ 1.5 s trong file và chạy
Beat This! cùng ADTOF qua API khi chặn mạng. App tìm 120 BPM, downbeat đầu 0.5 s trong
clip (=2.0 s trong file), giữ 5 hit pickup và xuất đủ MusicXML/MIDI/click audio.
Trong clip này model gợi ý 2/4 cho groove được tạo ở 4/4; kết quả meter vẫn cần
kiểm chứng, dù tempo và phách đầu khớp fixture. Đây là hạn chế đã quan sát, không
phải một trường hợp meter được chấm đúng.

Checkpoint đã kiểm tra:

```text
8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331
```

Tài liệu nguồn:

- [Beat This! inference](https://github.com/CPJKU/beat_this/blob/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c/beat_this/inference.py)
- [Beat/downbeat postprocessor](https://github.com/CPJKU/beat_this/blob/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c/beat_this/model/postprocessor.py)
- [README và giấy phép model](https://github.com/CPJKU/beat_this/tree/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c#license)
