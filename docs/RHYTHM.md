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

## Cách tạo lưới ký âm và bản đồ ô nhịp

Từ phiên bản 0.3, một clip có **bản đồ từng ô nhịp** thay vì một số chỉ nhịp cố định.
`RhythmAnalysis.bars` ghi thời gian đầu/cuối, vị trí nốt đen đầu/cuối, số chỉ nhịp,
nhóm phách và nguồn suy ra của mỗi ô. `meter` ở mức tổng quan là nhịp đầu đoạn;
`bpm` là tempo đại diện. Hai giá trị này không được dùng để ép mọi ô về cùng nhịp.

Model trả về thời điểm beat và downbeat. App xử lý như sau:

1. Kiểm tra thời gian tăng dần, nằm trong clip, có ít nhất bốn beat và hai downbeat.
   Marker ở đúng mép cuối hoặc tối đa một frame 20 ms trong phần đệm được loại riêng;
   giá trị nằm xa ngoài audio vẫn báo lỗi. Số marker loại được ghi trong metadata.
2. Ghép downbeat với beat gần nhất trong giới hạn 60 ms. Không lấy cú đánh đầu
   hoặc thời điểm 0 s để thay một downbeat chưa nhận diện được.
3. Giữ độ dài của từng khoảng giữa các downbeat. Chuỗi 4,4,3,3 beat/ô được biểu diễn
   thành 4/4 → 3/4, thay vì báo lỗi hoặc tiếp tục chia toàn bộ clip thành ô 4/4.
4. Chỉ khi một khoảng dài đơn lẻ là bội nguyên của hai khoảng kề có cùng số beat,
   app mới tạm nội suy đầu ô bị thiếu. Ví dụ 4,8,4 có thể là thiếu một downbeat.
   Các ô nội suy được đánh dấu và cảnh báo vì đây cũng có thể là một ô 8/4 thật.
   Chuỗi kéo dài 2,2,2,4,4,4 được giữ thành hai cách đếm, không dùng ước chung để
   ép tất cả thành 2/4. Model đổi cách đếm và nhạc thực sự đổi nhịp vẫn có thể
   cho cùng loại dữ liệu; người nghe cần phân biệt hai trường hợp này.
5. Nếu tỷ lệ thời gian pulse ngắn/dài khớp nhóm 2 và 3, và cùng mẫu xuất hiện ít
   nhất hai lần trong phạm vi bốn ô lân cận, app gợi ý nhóm phách cộng. Ví dụ
   `2+3` → 5/8, `2+2+3` hoặc `2+3+2` → 7/8. Pulse dài 3 móc đơn tương ứng 1.5
   nốt đen; không ép tất cả pulse thành khoảng cách bằng nhau. Cách này cũng
   giữ được những chuỗi 5/8 ↔ 7/8 luân phiên khi các mẫu nhóm phách lặp lại.
6. Các beat còn lại giữ nguyên timestamp. Vị trí âm nhạc được tích luỹ theo
   từng ô và từng nhóm phách; BPM đại diện là nghịch đảo trung vị số giây/nốt đen.
   Thay đổi tốc độ giữa các ô không bị từ chối chỉ vì khác BPM trung vị toàn clip.
7. Sau downbeat cuối, app tiếp tục cách đếm của ô vừa quan sát để hoàn tất ký âm.
   Những ô này có nguồn `extrapolated`; mép cuối danh nghĩa có thể vượt quá audio.
   App không thêm marker vào danh sách downbeat thô để giả vờ đã nhận diện chúng.

Vị trí nốt đen `0` ứng với **phách 1 đầu tiên được phát hiện**. Beat trước đó có
vị trí âm. Exporter giữ phần lấy đà hoặc đoạn bắt đầu giữa ô nhịp; sự kiện drum
và MIDI gốc giữ nguyên timestamp audio. Các ô 4/4 → 3/4 → 7/8 có các mốc đầu ô
riêng, nên đổi số chỉ nhịp không kéo lệch toàn bộ nốt phía sau.

## Số chỉ nhịp và nhóm phách còn có phần mơ hồ

Beat This! cung cấp beat/downbeat, **không trực tiếp phân loại mẫu số số chỉ nhịp**.
Năm pulse đều có thể được viết 5/4 hoặc 5/8; hai pulse đều có thể là 2/4 hoặc hai
nốt đen chấm trong 6/8. Khi không có gợi ý, app giữ cách đếm `n/4` cho pulse đều.
Nhóm 2/3 chỉ gợi ý mẫu số 8 khi có bằng chứng tỷ lệ thời gian lặp lại. Một ô
7/8 đơn lẻ có thể chưa đủ bằng chứng và vẫn cần sửa lại cách viết.

Gợi ý meter ở form đầu vào áp dụng cho **đầu đoạn**. Ví dụ hai pulse đầu mỗi ô
với gợi ý 6/8 tương ứng mỗi pulse 1.5 nốt đen; nếu đoạn sau có ba pulse/ô cùng
cách đếm, app có thể ghi 9/8. Gợi ý không buộc mọi ô sau phải là 6/8. Ba pulse
đều không thể bị ép thành 4/4 bằng cách tự gán mỗi pulse 4/3 nốt đen.

Bản đồ hỗ trợ tử số 1–32 và mẫu số 2, 4, 8, 16 để lưu và xuất nhịp lẻ, nhịp kép,
nhịp cộng và nhịp thay đổi. Đây là khả năng biểu diễn/ký âm, không phải cam kết
model nhận diện đúng tất cả các số chỉ nhịp đó. Các ô tự động luôn có
`estimated: true`; các ô thay đổi nhiều hoặc có nhiều khoảng chỉ một beat được
cảnh báo riêng. `rhythm_review_status` là `unstable` nếu có ít nhất ba khoảng
chỉ một beat, hoặc số lần đổi meter/nhóm phách lớn hơn `max(2, floor(số ô quan sát/3))`.
Các trường hợp khác vẫn là `estimated`. Đây là quy tắc yêu cầu nghe kiểm tra,
không phải xác suất sai: nhạc phức tạp thật cũng có thể vượt ngưỡng. Metadata ghi
ngưỡng, lý do bằng tiếng Việt và số liệu thô; không tạo tỷ lệ tin cậy chưa hiệu chỉnh.

Người dùng có thể sửa số chỉ nhịp/nhóm phách của từng ô rồi xuất lại, giữ timestamp
beat và hit gốc mà không chạy lại model. Việc sửa meter trên một ô **không sửa
được một vị trí downbeat vốn đã sai**; cần nghe cả audio gốc và click để phát hiện
trường hợp đó.

## Tempo thay đổi

Lưới thời gian là ánh xạ từng beat sang vị trí nốt đen, không phải phép cộng từ
một BPM duy nhất. Các pulse 0.5, 0.5, 0.75 giây trong 7/8 nhóm 2+2+3 tương ứng
1, 1, 1.5 nốt đen, nên vẫn là 120 BPM theo nốt đen. Khi khoảng giây/nốt đen thay
đổi thật, exporter có thể tạo tempo map ở đúng vị trí nhạc.

BPM tổng quan chỉ là giá trị đại diện; nghe MIDI ký âm/đọc các dấu tempo để kiểm
tra diễn biến trong clip. Nếu marker beat bị thiếu, thừa hoặc đặt sai, tempo map
cũng có thể sai theo. Dữ liệu thời gian gốc và metadata được giữ để kiểm tra lại.

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

Tests của bộ phân tích bao phủ offset `1.5 s`, dịch timeline thêm `0.371 s`,
phách đầu không phải beat đầu, 4/4 → 3/4, 4/4 → 7/8 → 4/4, 5/8 ↔ 7/8, nhóm
2+3 / 2+2+3 / 2+3+2, gợi ý đầu đoạn, thay đổi tempo lớn, thiếu/thừa marker,
ô một beat, nguồn ô nội suy/ngoại suy, model thiếu, chặn tải ngầm và click đúng mẫu.
Các test này cấp beat/downbeat có ground truth để kiểm tra thuật toán ánh xạ;
chúng không thay thế phép đo độ chính xác nhận diện từ audio.

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
Ở phiên bản 0.2, clip này được gợi ý 2/4 cho groove được tạo ở 4/4; chuỗi
marker thô có các khoảng 2 beat ở đầu và 4 beat về sau. Phiên bản 0.3 giữ hai
cách đếm này như một thay đổi cần kiểm tra, không coi đó là bằng chứng chắc chắn
nhạc đổi từ 2/4 sang 4/4. Đây là hạn chế đã quan sát, không phải ca nhận diện
meter được chấm đúng.

### Phép thử model thật với nhịp thay đổi và nhịp lẻ

Đã chạy Beat This! thật trên CPU, hai Torch threads, chặn socket/DNS khi suy luận,
với các groove drum tổng hợp có ground truth 120 BPM, offset đầu `0.371 s`:

| Fixture | Kết quả thực tế | Thời gian gồm nạp model | Gọi mạng |
|---|---|---:|---:|
| 8 ô 4/4 → 8 ô 3/4 → 8 ô 7/8, 42.571 s | Giữ 120 BPM đại diện và đoạn đầu 4/4; phát hiện thay đổi tại 16.38 s nhưng gợi ý 2/4 cho đoạn 3/4; phần sau có nhiều marker không ổn định | 5.280 s | 0 |
| 12 ô 7/8 nhóm 2+2+3, 21.571 s | Gợi ý 230.77 BPM và nhiều ô sai; downbeat đầu 1.38 s thay vì 0.371 s | 1.273 s | 0 |
| 16 ô 5/8 nhóm 2+3, 20.571 s | Downbeat đầu 0.38 s gần fixture, nhưng BPM 230.77 và nhiều số chỉ nhịp sai | 1.185 s | 0 |

Cả ba phép thử chạy đến bước tạo bản đồ ô, không còn bị lỗi vì nhóm beat thay đổi.
**Nhận diện tự động nhịp lẻ của model hiện tại vẫn chưa đạt yêu cầu trong các
fixture này.** Hỗ trợ bản đồ ô, xuất nhịp thay đổi và sửa nhóm phách không được
đánh đồng với việc đã giải quyết độ chính xác của beat/downbeat trên mọi bản nhạc.
Các fixture tổng hợp cũng không thay thế đánh giá trên nhạc thực tế của người dùng.
Không điều chỉnh heuristic chỉ để làm đúng ba fixture này.

Checkpoint đã kiểm tra:

```text
8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331
```

Tài liệu nguồn:

- [Beat This! inference](https://github.com/CPJKU/beat_this/blob/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c/beat_this/inference.py)
- [Beat/downbeat postprocessor](https://github.com/CPJKU/beat_this/blob/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c/beat_this/model/postprocessor.py)
- [README và giấy phép model](https://github.com/CPJKU/beat_this/tree/b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c#license)
