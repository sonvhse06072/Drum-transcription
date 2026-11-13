# Trạng thái v0.3 — đổi số chỉ nhịp và tempo theo từng đoạn

## Phạm vi đã có source

- FastAPI + giao diện tiếng Việt, chạy ở `127.0.0.1:8765`; toàn bộ CSS/JS local.
- Upload audio, cắt clip bằng FFmpeg, một tác vụ AI tại một thời điểm, lưu job trên đĩa.
- ADTOF và ADT_STR chạy local từ revision/checkpoint đã pin; thiếu weights phải báo lỗi.
- Chuẩn hóa nhạc cụ ADT_STR trước khi xuất General MIDI và sheet.
- Beat This! local tự phát hiện tempo/phách 1; mặc định không yêu cầu nhập BPM hoặc downbeat. Số chỉ nhịp là gợi ý có cảnh báo khi mơ hồ.
- Bản đồ từng ô cho đổi nhịp, nhịp lẻ và nhóm phách; không còn yêu cầu toàn clip có cùng số beat/ô. Gợi ý đầu đoạn không khóa các đoạn sau.
- Cảnh báo rõ khi detector sinh nhiều ô một beat hoặc thay đổi cách đếm liên tục. Có thể sửa meter/nhóm phách theo ô hoặc theo dải ô mà không chạy lại model.
- Giữ sự kiện gốc; căn nốt theo các beat đã phát hiện, giữ phần lấy đà và số giây lẻ. Có chế độ ghi đè thủ công tùy chọn.
- Lưu nhịp phát hiện và mốc clip/file tải lên, thêm audio gốc trộn click để nghe kiểm chứng.
- MusicXML, MIDI gốc, MIDI theo sheet, JSON và WAV tổng hợp để nghe đối chiếu.
- Hai voice, percussion clef, tempo, số chỉ nhịp, beam/rest/tuplet; ký hiệu biểu cảm khi có marker tường minh.
- MIDI theo sheet và MusicXML có tempo map theo lưới beat, số chỉ nhịp tại từng đoạn và nối móc theo nhóm; tempo dao động nhỏ không in đầy nhãn. Grid chia nhỏ neo tại từng ô, giữ đúng barline cả với 3/8 hoặc 13/16 và triplet.
- Xuất lại sheet từ kết quả đã lưu, không chạy AI lại và không thay dữ liệu gốc.
- Tích hợp tùy chọn Demucs local và xuất PDF bằng MuseScore đã cài trên máy.

## Những gì đã kiểm tra

| Phần | Kết quả |
|---|---|
| Tests | 197 tests pass: 9 drum engine, 40 rhythm, 117 notation, 20 rhythm edit/domain, 10 API/job, 1 MP3 decode |
| Lint / cú pháp | Ruff, Python compile, JavaScript syntax và HTML/DOM reference checks pass |
| ADTOF thật | Checkpoint load strict; suy luận CPU trên 8 giây audio tổng hợp; không gọi mạng |
| ADT_STR thật | Checkpoint load strict; toàn bộ adapter + subprocess chạy CPU trên 2.56 giây audio tổng hợp; không gọi mạng |
| Beat This! thật | Groove 120 BPM lệch 0 / 1.5 / 0.371 giây → phách 1 phát hiện 0.02 / 1.52 / 0.38 giây; không gọi mạng |
| Luồng API + model thật | v0.2: MP3 cắt từ 1.5 s. v0.3: WAV đổi nhịp cắt từ 1.5 s, dài 32 s → Beat This! → ADTOF → events/MIDI/MusicXML/click/preview; cả hai thành công khi chặn socket/DNS |
| Xuất lại | Chỉnh từng ô nhiều lần rồi đổi sang manual; không gọi lại AI, giữ nguyên sự kiện và nhận diện gốc; bản đồ đang dùng lưu riêng |
| Cấu trúc nhạc | Ma trận meter/grid ban đầu và các ca mới 3/8, 5/8, 7/8, 13/16; voice đủ trường độ, nốt hai phía barline không mất/nhân đôi; MIDI tempo và meter đúng vị trí |
| Hình thức sheet | Verovio: demo, pickup, và fixture 4/4 → 3/4 → 5/8 → 7/8 → 13/16 với tempo 120 → 90, nhãn nhóm phách; 47/47 hit ở fixture chú giải được giữ |

Môi trường kiểm tra là Linux, Python 3.12, PyTorch/TorchAudio 2.8 CPU.
Chi tiết inference và checksum ở [MODELS.md](MODELS.md) và [RHYTHM.md](RHYTHM.md).

Trong phép thử MP3 ở v0.2 cắt giữa ô nhịp, app tự tìm 120 BPM và phách 1 tại 0.5 s trong clip (=2.0 s trong file tải lên), giữ cả 5 hit trước phách 1. 97/97 hit được giữ trên sheet ở grid đã chọn. Có một marker beat ở đúng mép cuối 16 s do frame đệm; đã sửa để loại riêng marker này và thêm regression.

Model gợi ý 2/4 cho clip MP3 tổng hợp 4/4 nói trên, vì có thêm downbeat marker trong những ô đầu. Đây là **giới hạn chất lượng gợi ý meter đã quan sát**, không được tính là nhận diện meter đúng. BPM và phách đầu vẫn khớp fixture; nghe click/gợi ý nhịp và đánh giá trên nhạc thật vẫn cần thiết. Không suy ra accuracy từ các phép thử tổng hợp này.

## Kết quả model thật với nhịp lẻ: chưa đạt chất lượng yêu cầu

Đã chạy thêm ba groove tổng hợp, nhịp gốc 120 nốt đen/phút, đầu groove lệch 0.371 giây, model Beat This! thật trên CPU và chặn socket/DNS. Cả ba chạy được đến bước tạo bản đồ, không có lần gọi mạng. Tuy nhiên:

| Fixture | Điều quan sát |
|---|---|
| 4/4 → 3/4 → 7/8 | Đầu 4/4 và BPM tổng quan đúng; mốc đổi đầu 16.38 s gần 16.371 s, nhưng phần 3/4 bị gợi ý thành 2/4, đoạn sau bất ổn |
| 7/8, nhóm 2+2+3 | Gợi ý 230.77 BPM thay vì 120; phách đầu lệch và nhiều ô sai |
| 5/8, nhóm 2+3 | Phách đầu gần đúng, nhưng gợi ý 230.77 BPM và nhiều số chỉ nhịp sai |

App đánh dấu cả ba bản đồ là `unstable` theo dấu hiệu quan sát được. Chưa có confidence đã hiệu chỉnh; nhạc đổi nhịp dày thật cũng có thể bị yêu cầu kiểm tra. **Hỗ trợ biểu diễn/xuất nhịp thay đổi đã có; tự động nghe đúng nhịp lẻ vẫn còn là vấn đề chất lượng model/giải mã.** Tài liệu [RHYTHM.md](RHYTHM.md) ghi đầy đủ kết quả, thời gian và giới hạn, không coi các ca trên là ca nhận diện đạt.

Luồng API thật v0.3 chạy thêm trên 32 giây của fixture đổi nhịp, cắt từ 1.5 giây: tự tìm BPM tổng quan 120, downbeat đầu 0.88 giây trong clip (=2.38 giây trong file), xuất 153/153 sự kiện nhận diện ở grid đã chọn, có tempo map và 35 ô trong bản đồ. Detector tạo 6 lần đổi cách đếm và app đánh dấu `unstable`; các ô này không được chấm là đúng. Thời gian toàn luồng trong môi trường kiểm tra là 53.461 giây, 0 gọi mạng. Số sự kiện giữ được là kiểm tra tính toàn vẹn dữ liệu, không phải precision/recall của nhận diện trống.

Chỉnh từng ô hiện giữ các ranh giới thời gian đã phát hiện. Khi downbeat sai, đổi nhãn meter không tự sửa được ranh giới đó; cần đánh giá lại detector hoặc sửa trong MuseScore. Chưa có kéo/gộp/tách marker trong giao diện.

## Những phần còn phải xác nhận trên PC

- **Chất lượng nhận diện trên nhạc thật**: chưa có clip có nhãn của người dùng; các audio tổng hợp chỉ dùng kiểm tra thực thi, không đo accuracy.
- **PDF bằng MuseScore**: adapter đã có, nhưng MuseScore AppImage không khởi động được trong container không có display. Kiểm tra hình thức bằng Verovio không thay cho xác nhận PDF MuseScore.
- **Giao diện trong trình duyệt**: đã kiểm tra cú pháp/contract với backend; chưa xác nhận screenshot browser vì Chromium không khởi động được trong môi trường này.
- **Demucs thực tế**: đường gọi với local model repo đã triển khai; chưa chạy inference tách stem trong lượt kiểm tra này.
- **Windows/WSL, CUDA/MPS và hiệu năng cả bài**: cần kiểm tra trên cấu hình thực của bạn.

App chưa có hủy tác vụ đang chạy; dừng server có thể cần đợi inference kết thúc. Giới hạn upload 200 MB được kiểm tra sau khi multipart parser đã nhận/spool file. Đây là bản local một người dùng, chưa có cơ chế public hosting.

## Bước tiếp theo hữu ích nhất

Chạy cùng ba clip 30–60 giây qua ADTOF và ADT_STR, mở MusicXML trong MuseScore, ghi lỗi thiếu/thừa/nhầm hit và số phút chỉnh tay theo [VALIDATION.md](VALIDATION.md). Kết quả đó quyết định có cần ưu tiên tách nguồn, cải thiện tempo, đổi model hay fine-tune trước khi mở rộng giao diện.
